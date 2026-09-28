import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from jobhunter.config import get_settings
from jobhunter.db.migrate import init_database


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "frontend" / "package.json").is_file() and (parent / "backend").is_dir():
            return parent
    raise SystemExit("jobhunter dev must be run from a source checkout of this repository.")


def run_api() -> None:
    settings = get_settings()
    os.execvp(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "jobhunter.api.app:create_app",
            "--factory",
            "--host",
            settings.bind_host(),
            "--port",
            str(settings.port),
            "--log-level",
            settings.log_level.lower(),
        ],
    )


def run_dev() -> None:
    settings = get_settings()
    root = _repo_root()
    frontend = root / "frontend"
    if not (frontend / "node_modules").is_dir():
        raise SystemExit("frontend dependencies are missing. Run ./scripts/dev.sh once to install them.")

    env = os.environ.copy()
    env["JOBHUNTER_API_ORIGIN"] = f"http://{settings.bind_host()}:{settings.port}"

    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "jobhunter.api.app:create_app",
            "--factory",
            "--reload",
            "--host",
            settings.bind_host(),
            "--port",
            str(settings.port),
            "--log-level",
            settings.log_level.lower(),
        ],
        cwd=root / "backend",
        env=env,
        start_new_session=True,
    )
    web = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
        cwd=frontend,
        env=env,
        start_new_session=True,
    )

    print(f"API  http://{settings.bind_host()}:{settings.port}/health", flush=True)
    print("UI   http://127.0.0.1:5173", flush=True)

    exit_code = 0

    def terminate() -> None:
        for process in (web, api):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal exit_code
        exit_code = 0
        terminate()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while True:
            if api.poll() is not None or web.poll() is not None:
                exit_code = 1
                break
            time.sleep(0.4)
    finally:
        terminate()
    raise SystemExit(exit_code)


def main() -> None:
    parser = argparse.ArgumentParser(prog="jobhunter", description="Local job hunting assistant")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("dev", help="Start the API and the frontend")
    sub.add_parser("api", help="Start the API only")
    sub.add_parser("db-upgrade", help="Create or migrate the local SQLite database")
    args = parser.parse_args()
    if args.command == "api":
        run_api()
    elif args.command == "dev":
        run_dev()
    elif args.command == "db-upgrade":
        init_database(get_settings())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
