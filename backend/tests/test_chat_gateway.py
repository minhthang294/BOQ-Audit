import asyncio
import importlib.util
import json
import tomllib
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("sbtech_gateway", Path(__file__).resolve().parents[2] / "chat" / "gateway.py")
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


def test_gateway_config_denies_secrets_and_disables_tools(tmp_path):
    config = tomllib.loads(gateway.config_text(tmp_path / "workspace", tmp_path / "home"))
    filesystem = config["permissions"]["sbtech-chat"]["filesystem"]
    assert filesystem[":root"] == "deny"
    assert filesystem[str(tmp_path / "home")] == "deny"
    assert filesystem[str(tmp_path / "workspace")] == "read"
    assert not config["permissions"]["sbtech-chat"]["network"]["enabled"]
    assert config["approval_policy"] == "never"
    for feature in ("shell_tool", "unified_exec", "multi_agent", "apps", "plugins", "view_image", "image_generation", "code_mode_host"):
        assert not config["features"][feature]


class FakeProcess:
    def __init__(self, tool=False):
        self.events = asyncio.Queue()
        self.stdout = self
        self.stdin = self
        self.returncode = None
        self.tool = tool
        self.requests = []

    def write(self, raw):
        message = json.loads(raw)
        self.requests.append(message)
        method = message.get("method")
        result = {}
        if method in ("thread/start", "thread/resume"):
            result = {"activePermissionProfile": {"id": "sbtech-chat"}, "thread": {"id": "test-thread"}}
        if method == "turn/start":
            result = {"turn": {"id": "test-turn"}}
        if "id" in message:
            self.events.put_nowait({"id": message["id"], "result": result})
        if method == "turn/start":
            self.events.put_nowait({"method": "item/completed", "params": {"threadId": "test-thread", "item": {"type": "commandExecution" if self.tool else "agentMessage", "text": "Câu trả lời"}}})
            self.events.put_nowait({"method": "turn/completed", "params": {"threadId": "test-thread", "turn": {"id": "test-turn", "status": "completed"}}})

    async def drain(self):
        pass

    async def readline(self):
        return (json.dumps(await self.events.get()) + "\n").encode()

    def terminate(self):
        self.returncode = 0

    async def wait(self):
        return self.returncode


@pytest.mark.parametrize("tool", [False, True])
def test_gateway_protocol_resume_and_tool_rejection(tmp_path, monkeypatch, tool):
    auth = tmp_path / "auth.json"
    auth.write_text('{"test": "not-real-credentials"}')
    monkeypatch.setattr(gateway, "AUTH_FILE", auth)
    monkeypatch.setattr(gateway, "SESSION_ROOT", tmp_path / "sessions")
    processes = []
    async def spawn(*args, **kwargs):
        assert "CHAT_GATEWAY_TOKEN" not in kwargs["env"]
        process = FakeProcess(tool)
        processes.append(process)
        return process
    monkeypatch.setattr(gateway.asyncio, "create_subprocess_exec", spawn)
    request = gateway.ReplyRequest(session_key="a" * 64, context={"status": "SUBMITTED"}, message="Hi")
    async def run():
        if tool:
            with pytest.raises(RuntimeError, match="tool"):
                await gateway.conversation(request)
            assert processes[-1].returncode == 0
            return
        reply = await gateway.conversation(request)
        assert reply["text"] == "Câu trả lời"
        request.thread_id = reply["thread_id"]
        await gateway.conversation(request)
        assert any(r.get("method") == "thread/resume" for r in processes[-1].requests)
        assert all(r["params"]["permissions"] == "sbtech-chat" for r in processes[-1].requests if r.get("method") in ("thread/resume", "turn/start"))
        request.session_key = "b" * 64
        with pytest.raises(RuntimeError, match="belong"):
            await gateway.conversation(request)
    asyncio.run(run())


def test_gateway_rejects_untrusted_fields_and_paths():
    from pydantic import ValidationError
    for extra in ({"cwd": "/data"}, {"session_key": "../another-account"}, {"command": "cat /auth/auth.json"}):
        params = {"session_key": "a" * 64, "context": {}, "message": "Hi"}
        params.update(extra)
        with pytest.raises(ValidationError):
            gateway.ReplyRequest(**params)


def test_private_gateway_auth_body_limit_and_generic_errors(tmp_path, monkeypatch):
    import httpx2 as httpx
    token = "gateway-test-token-at-least-32-characters"
    monkeypatch.setattr(gateway, "TOKEN", token)
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    monkeypatch.setattr(gateway, "AUTH_FILE", auth)
    async def unavailable(_):
        raise RuntimeError("provider-secret-path-and-token")
    monkeypatch.setattr(gateway, "conversation", unavailable)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway.app), base_url="http://gateway") as client:
            payload = {"session_key": "a" * 64, "context": {}, "message": "Hi"}
            assert (await client.get("/health")).status_code == 200
            assert (await client.post("/reply", json=payload)).status_code == 401
            headers = {"Authorization": "Bearer " + token}
            failed = await client.post("/reply", json=payload, headers=headers)
            assert failed.status_code == 503
            assert "provider-secret" not in failed.text
            payload["context"] = {"data": "x" * 200001}
            assert (await client.post("/reply", json=payload, headers=headers)).status_code == 413
    asyncio.run(run())
