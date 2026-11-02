import re
from pathlib import Path

from app.services.audit_sandbox import CODEX_CLI_VERSION


def test_dockerfile_pins_the_verified_codex_cli_version():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"npm install --global @openai/codex@(\d+\.\d+\.\d+)", dockerfile)
    assert match, "Dockerfile must pin @openai/codex to an exact version"
    assert match.group(1) == CODEX_CLI_VERSION
