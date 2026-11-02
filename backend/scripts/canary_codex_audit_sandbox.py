"""Check that the audit Codex profile enforces exact reads and workspace writes."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.audit_sandbox import codex_exec_environment, codex_permission_args


def _canary(parent: Path, codex: str) -> dict[str, str]:
    parent = parent.resolve(strict=True)
    if not parent.is_dir() or parent.is_relative_to(Path("/tmp")) or Path("/tmp").is_relative_to(parent):
        raise RuntimeError("Choose an existing writable parent outside /tmp")

    with tempfile.TemporaryDirectory(prefix="boq-codex-sandbox-", dir=parent) as temporary:
        root = Path(temporary)
        workspace = root / "batch"
        (workspace / "work").mkdir(parents=True)
        (workspace / ".tmp").mkdir()
        skill_root = root / "skill"
        skill_root.mkdir()
        skill = skill_root / "SKILL.md"
        skill.write_text("SYNTHETIC_SKILL_SENTINEL", encoding="utf-8")
        source = root / "source.pdf"
        source.write_text("SYNTHETIC_SOURCE_SENTINEL", encoding="utf-8")
        controller = workspace / "work" / "batch_context.json"
        controller.write_text("SYNTHETIC_CONTROLLER_SENTINEL", encoding="utf-8")
        sibling = root / "ungranted.txt"
        sibling.write_text("SYNTHETIC_UNGRANTED_SENTINEL", encoding="utf-8")
        runtime = Path(codex).resolve(strict=True)
        interpreter = str(Path(sys.executable).resolve(strict=True))
        args = codex_permission_args(workspace, skill, [str(source)], [str(controller), str(runtime), interpreter])
        codex_home = root / "codex-home"
        codex_home.mkdir(mode=0o700)
        env = codex_exec_environment(workspace, {**os.environ, "CODEX_HOME": str(codex_home)})
        env["BOQ_CANARY_PATHS"] = json.dumps({
            "controller": str(controller),
            "source": str(source),
            "skill": str(skill),
            "sibling": str(sibling),
            "output": str(workspace / "work" / "canary-output.txt"),
        })
        probe = """import json, os
p=json.loads(os.environ['BOQ_CANARY_PATHS']); out={}
for label,path in ((\"controller\",p[\"controller\"]),(\"source\",p[\"source\"]),(\"skill\",p[\"skill\"])):
 try: out[label+\"_read\"]=open(path).read()
 except Exception as e: out[label+\"_read_error\"]=type(e).__name__+\":\"+str(e)
try: open(p[\"controller\"],\"w\").write(\"TAMPERED\"); out[\"controller_write\"]=\"allowed\"
except Exception as e: out[\"controller_write\"]=type(e).__name__+\":\"+str(e)
try: out[\"sibling_read\"]=open(p[\"sibling\"]).read()
except Exception as e: out[\"sibling_read\"]=type(e).__name__+\":\"+str(e)
try: open(p[\"output\"],\"w\").write(\"SYNTHETIC_OUTPUT\"); out[\"output_write\"]=\"allowed\"
except Exception as e: out[\"output_write\"]=type(e).__name__+\":\"+str(e)
print(json.dumps(out,sort_keys=True))"""
        result = subprocess.run(
            [codex, "sandbox", "--permission-profile", "boq_audit", "-C", str(workspace), *args, sys.executable, "-c", probe],
            env=env, text=True, capture_output=True, timeout=30,
        )
        if result.returncode:
            raise RuntimeError(f"Codex sandbox exited {result.returncode}: {result.stderr[-1000:]}")
        checks = json.loads(result.stdout)
        expected = {
            "controller_read": "SYNTHETIC_CONTROLLER_SENTINEL",
            "source_read": "SYNTHETIC_SOURCE_SENTINEL",
            "skill_read": "SYNTHETIC_SKILL_SENTINEL",
            "output_write": "allowed",
        }
        if any(checks.get(key) != value for key, value in expected.items()):
            raise RuntimeError(f"Codex sandbox allowed/denied path check failed: {checks}")
        if "Read-only file system" not in checks.get("controller_write", ""):
            raise RuntimeError(f"Codex sandbox allowed controller write: {checks}")
        if "FileNotFoundError" not in checks.get("sibling_read", ""):
            raise RuntimeError(f"Codex sandbox exposed ungranted sibling: {checks}")
        return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-dir", type=Path, default=Path("/var/tmp"), help="writable canary parent outside /tmp")
    parser.add_argument("--codex", default=shutil.which("codex"), help="Codex CLI path; defaults to PATH lookup")
    args = parser.parse_args()
    if not args.codex:
        parser.error("Codex CLI was not found; pass --codex")
    try:
        result = _canary(args.parent_dir, str(Path(args.codex).resolve(strict=True)))
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": "PASS", "codex": str(Path(args.codex).resolve()), "checks": result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
