"""Per-invocation filesystem and network permissions for the Codex audit worker."""

import json
from pathlib import Path

# Pinned in backend/Dockerfile and verified locally; sandbox flags are version-sensitive.
CODEX_CLI_VERSION = "0.165.0"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def codex_permission_args(
    workspace: Path,
    skill_path: Path,
    source_paths: list[str],
    exact_read_paths: list[str] | None = None,
) -> list[str]:
    original_workspace = workspace.absolute()
    workspace = workspace.resolve(strict=True)
    original_skill = skill_path.absolute()
    skill = skill_path.resolve(strict=True)
    if original_workspace != workspace or original_skill != skill or not workspace.is_dir() or not skill.is_file():
        raise RuntimeError("Audit sandbox workspace or skill path is invalid")
    skill_root = skill.parent
    if workspace == skill_root or workspace.is_relative_to(skill_root) or skill_root.is_relative_to(workspace):
        raise RuntimeError("Audit sandbox workspace overlaps the installed skill")

    roots: dict[str, str] = {
        ":root": "deny",
        ":minimal": "read",
        ":tmpdir": "write",
        ":slash_tmp": "deny",
        str(workspace): "write",
        str(skill_root): "read",
    }
    for source_path in source_paths:
        original = Path(source_path)
        source = original.resolve(strict=True)
        if (
            not original.is_absolute()
            or original.absolute() != source
            or not source.is_file()
            or source.stat().st_nlink != 1
        ):
            raise RuntimeError("Audit source must be a regular singly-linked file without symlink components")
        if source.is_relative_to(workspace) or source.is_relative_to(skill_root):
            raise RuntimeError("Audit source overlaps a writable or skill directory")
        roots[str(source)] = "read"

    for read_path in exact_read_paths or []:
        original = Path(read_path)
        controller_file = original.resolve(strict=True)
        if (
            not original.is_absolute()
            or original.absolute() != controller_file
            or not controller_file.is_file()
            or controller_file.stat().st_nlink != 1
        ):
            raise RuntimeError("Controller input must be a regular singly-linked file without symlink components")
        if (
            controller_file.is_relative_to(skill_root)
            or any(controller_file == Path(source).resolve(strict=True) for source in source_paths)
        ):
            raise RuntimeError("Controller input overlaps a skill or source path")
        roots[str(controller_file)] = "read"

    filesystem = "{" + ", ".join(f"{_toml_string(path)}={_toml_string(mode)}" for path, mode in roots.items()) + "}"
    # Auto-review uses the broad workspace-write sandbox and would replace these per-job rules.
    return [
        "-c", 'default_permissions="boq_audit"',
        "-c", 'approval_policy="never"',
        "-c", f"permissions.boq_audit.filesystem={filesystem}",
        "-c", "permissions.boq_audit.network.enabled=false",
        "-c", 'web_search="disabled"',
    ]


def codex_exec_environment(workspace: Path, source_env: dict[str, str]) -> dict[str, str]:
    """Pass only runtime essentials; app secrets must not reach model shell tools."""
    original_workspace = workspace.absolute()
    workspace = workspace.resolve(strict=True)
    tmp_dir = workspace / ".tmp"
    if original_workspace != workspace or not workspace.is_dir() or tmp_dir.is_symlink() or not tmp_dir.is_dir():
        raise RuntimeError("Audit sandbox temporary directory is invalid")
    allowed = ("PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "TERM", "USER", "LOGNAME")
    environment = {key: source_env[key] for key in allowed if source_env.get(key)}
    environment["TMPDIR"] = str(tmp_dir)
    return environment


def safe_output_files(output_dir: Path) -> list[Path]:
    root = output_dir.resolve(strict=True)
    return [
        path for path in sorted(output_dir.rglob("*"))
        if not path.is_symlink()
        and path.is_file()
        and path.resolve(strict=True).is_relative_to(root)
        and path.stat().st_nlink == 1
    ]
