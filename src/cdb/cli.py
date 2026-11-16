"""A headless client of the same contracts used by the desktop UI."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from cdb import __version__
from cdb.branding import APP_NAME
from cdb.application.examples import demo_preset
from cdb.application.service import ApplicationService
from cdb.config import PROFILE_LOCK_NAME, build_config
from cdb.domain.contracts import CdbError, ErrorCode
from cdb.domain.serialization import preset_from_dict, preset_to_dict
from cdb.infrastructure.profile_lock import ProfileLock

CLI_DESCRIPTION = f"{APP_NAME} headless interface · JSON output"
ENCODING = "utf-8"
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTERRUPTED = 130


def emit(value, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    print(json.dumps(value, ensure_ascii=False, default=str), file=stream)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding=ENCODING)
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("presets", help="프리셋 목록")
    register = subcommands.add_parser("register", help="버전 1 JSON 프리셋 등록/수정")
    register.add_argument("path", type=Path)
    demo = subcommands.add_parser("demo", help="데모 프리셋 등록")
    demo.add_argument("--memory", action="store_true")
    collect = subcommands.add_parser("collect", help="명령어 실행 및 저장")
    collect.add_argument("preset_id")
    query = subcommands.add_parser("query", help="SELECT SQL 조회")
    query.add_argument("preset_id")
    query.add_argument("--sql", required=True)
    subcommands.add_parser("history", help="최근 실행 이력")
    args = parser.parse_args(argv)
    if args.command is None:
        from cdb.__main__ import run_gui
        return run_gui(args.data_dir)
    config = build_config(args.data_dir)
    lock = ProfileLock(config.data_directory / PROFILE_LOCK_NAME)
    service = None
    try:
        lock.acquire()
        service = ApplicationService(config)
        service.recover()
        match args.command:
            case "presets":
                emit([preset_to_dict(preset, include_environment=False) for preset in service.list_presets()])
            case "register":
                preset = preset_from_dict(json.loads(args.path.read_text(encoding=ENCODING)))
                emit(preset_to_dict(service.save_preset(preset), include_environment=False))
            case "demo":
                emit(preset_to_dict(service.save_preset(demo_preset(config, memory=args.memory))))
            case "collect":
                emit(asdict(service.collect(args.preset_id)))
            case "query":
                emit(asdict(service.query(args.preset_id, args.sql)))
            case "history":
                emit([asdict(run) for run in service.list_runs()])
        return EXIT_OK
    except CdbError as exc:
        emit({"error": exc.code.value, "message": str(exc), "details": exc.details}, error=True)
        return EXIT_ERROR
    except (OSError, ValueError) as exc:
        emit({"error": ErrorCode.INVALID_PRESET.value, "details": str(exc)}, error=True)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    finally:
        if service:
            service.close()
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
