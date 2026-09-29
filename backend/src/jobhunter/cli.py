import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from jobhunter.config import get_settings
from jobhunter.db.migrate import init_database
from jobhunter.db.session import build_engine, session_factory
from jobhunter.llm.client import client_from_environment
from jobhunter.llm.errors import LLMError
from jobhunter.resume.application import prepare_application_resume
from jobhunter.resume.master import MasterResumeError, compile_master, validate_master
from jobhunter.resume.tailoring import LLMTailoringPlanner, tailor_application_resume


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
    agent = subprocess.Popen(
        [sys.executable, "-m", "jobhunter.cli", "agent"],
        cwd=root / "backend",
        env=env,
        start_new_session=True,
    )

    print(f"API  http://{settings.bind_host()}:{settings.port}/health", flush=True)
    print("UI   http://127.0.0.1:5173", flush=True)
    print("Agent worker started; it stays stopped until you press Start in the UI.", flush=True)

    exit_code = 0

    def terminate() -> None:
        for process in (web, agent, api):
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
            if api.poll() is not None or web.poll() is not None or agent.poll() is not None:
                exit_code = 1
                break
            time.sleep(0.4)
    finally:
        terminate()
    raise SystemExit(exit_code)


def _resume_session():
    settings = get_settings()
    init_database(settings)
    engine = build_engine(settings)
    return settings, engine, session_factory(engine)()


def run_resume_validate() -> None:
    settings, engine, session = _resume_session()
    try:
        report = validate_master(session, settings)
    except MasterResumeError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        session.close()
        engine.dispose()
    kinds = ", ".join(section.kind for section in report.parsed.sections) or "none"
    print(f"valid {report.path}")
    print(f"sha256 {report.sha256}")
    print(f"sections {kinds}")


def run_resume_prepare(application_id: int) -> None:
    settings, engine, session = _resume_session()
    try:
        report = prepare_application_resume(session, settings, application_id)
    except MasterResumeError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        session.close()
        engine.dispose()
    print(f"tex {report.tex_path}")
    print(f"pdf {report.pdf_path}")
    print(f"sha256 {report.sha256}")


def run_resume_tailor(application_id: int) -> None:
    try:
        planner = LLMTailoringPlanner(client_from_environment())
    except LLMError as exc:
        raise SystemExit(str(exc)) from exc
    settings, engine, session = _resume_session()
    try:
        report = tailor_application_resume(session, settings, application_id, planner)
    except MasterResumeError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        session.close()
        engine.dispose()
    print(f"tex {report.tex_path}")
    print(f"pdf {report.pdf_path}")
    print(f"report {report.change_report_path}")
    print(f"evidence {report.evidence_path}")
    print(f"changes {report.applied_changes} rejected {report.rejected}")
    if report.plan_error:
        print(f"plan not applied: {report.plan_error}")


def run_resume_compile() -> None:
    settings, engine, session = _resume_session()
    try:
        report = compile_master(session, settings)
    except MasterResumeError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        session.close()
        engine.dispose()
    print(f"compiled {report.pdf_path}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="jobhunter", description="Local job hunting assistant")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("dev", help="Start the API and the frontend")
    sub.add_parser("api", help="Start the API only")
    sub.add_parser("db-upgrade", help="Create or migrate the local SQLite database")
    agent = sub.add_parser("agent", help="Run the agent worker (start and stop it from the UI)")
    agent.add_argument("--start", action="store_true", help="Start the agent immediately")
    resume = sub.add_parser("resume", help="Validate or compile the master resume")
    resume_sub = resume.add_subparsers(dest="resume_command")
    resume_sub.add_parser("validate", help="Check the configured master resume and store its structure")
    resume_sub.add_parser("compile", help="Compile the master resume with pdflatex")
    prepare = resume_sub.add_parser("prepare", help="Copy and compile a resume for one application")
    prepare.add_argument("application_id", type=int)
    tailor = resume_sub.add_parser("tailor", help="Tailor a resume for one application with the LLM")
    tailor.add_argument("application_id", type=int)
    args = parser.parse_args()
    if args.command == "api":
        run_api()
    elif args.command == "dev":
        run_dev()
    elif args.command == "db-upgrade":
        init_database(get_settings())
    elif args.command == "agent":
        from jobhunter.agent.worker import run_worker

        run_worker(get_settings(), start=args.start)
    elif args.command == "resume" and args.resume_command == "validate":
        run_resume_validate()
    elif args.command == "resume" and args.resume_command == "compile":
        run_resume_compile()
    elif args.command == "resume" and args.resume_command == "prepare":
        run_resume_prepare(args.application_id)
    elif args.command == "resume" and args.resume_command == "tailor":
        run_resume_tailor(args.application_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
