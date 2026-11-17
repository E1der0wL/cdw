"""A headless client of the same contracts used by the desktop UI."""

from __future__ import annotations

from cdw.i18n import tr

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from cdw import __version__
from cdw.branding import APP_NAME
from cdw.config import build_config
from cdw.domain.contracts import CdwError, ErrorCode
from cdw.domain.serialization import preset_from_dict, preset_to_dict, query_to_dict
from cdw.server.client import RemoteApplicationService, ServerClient

CLI_DESCRIPTION = tr('cli.description').format(app_name=APP_NAME)
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
    subcommands.add_parser("presets", help=tr('cli.main'))
    register = subcommands.add_parser("register", help=tr('cli.main.register'))
    register.add_argument("path", type=Path)
    server = subcommands.add_parser("server", help=tr("cli.server.help"))
    server.add_argument("action", choices=("start", "status", "stop"))
    collect = subcommands.add_parser("collect", help=tr('cli.main.collect'))
    collect.add_argument("preset_id")
    query = subcommands.add_parser("query", help=tr('cli.main.query'))
    query.add_argument("preset_id")
    query.add_argument("--sql", required=True)
    subcommands.add_parser("history", help=tr('cli.main_2'))
    args = parser.parse_args(argv)
    if args.command is None:
        from cdw.__main__ import run_gui
        return run_gui(args.data_dir)
    config = build_config(args.data_dir)
    service = None
    try:
        if args.command == "server":
            client = ServerClient(config)
            emit(getattr(client, args.action)())
            return EXIT_OK
        service = RemoteApplicationService(config)
        match args.command:
            case "presets":
                emit([preset_to_dict(preset, include_environment=False) for preset in service.list_presets()])
            case "register":
                preset = preset_from_dict(json.loads(args.path.read_text(encoding=ENCODING)))
                emit(preset_to_dict(service.save_preset(preset), include_environment=False))
            case "collect":
                emit(asdict(service.collect(args.preset_id)))
            case "query":
                emit(query_to_dict(service.query(args.preset_id, args.sql)))
            case "history":
                emit([asdict(run) for run in service.list_runs()])
        return EXIT_OK
    except CdwError as exc:
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


if __name__ == "__main__":
    raise SystemExit(main())
