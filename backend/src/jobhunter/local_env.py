"""Load a local .env file into the process environment.

Existing environment variables win. Values are not logged.
"""

import os
from pathlib import Path


def load_local_env() -> None:
    for path in env_files():
        for key, value in _parse(path).items():
            if os.environ.get(key, "").strip():
                continue
            os.environ[key] = value


def env_files() -> list[Path]:
    found: list[Path] = []
    cwd_file = Path.cwd() / ".env"
    if cwd_file.is_file():
        found.append(cwd_file)
    root = _repo_root()
    if root is not None:
        root_file = root / ".env"
        if root_file.is_file() and root_file.resolve() not in {path.resolve() for path in found}:
            found.append(root_file)
    return found


def _repo_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".env.example").is_file() and (parent / "backend").is_dir():
            return parent
    return None


def _parse(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        values[key] = _unquote(value.strip())
    return values


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
