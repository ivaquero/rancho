"""Command line entry points: ``rancho serve`` and ``rancho simulate``."""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from pathlib import Path
from typing import Any

from rancho import __version__
from rancho.engine import run_scenario
from rancho.scenario import DEFAULT_PRESET, PRESETS, ScenarioConfig

DEFAULT_WORK_DIR = Path(".rancho")
DEFAULT_DIST = Path("frontend/dist")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rancho",
        description="基于 CropForge 的矩形网格农田仿真台",
    )
    parser.add_argument("--version", action="version", version=f"rancho {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="启动仿真服务与前端界面")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    serve.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    serve.add_argument("--dist", type=Path, default=DEFAULT_DIST, help="前端产物目录")

    simulate = subparsers.add_parser("simulate", help="无界面跑一次仿真并导出 bundle")
    simulate.add_argument("--preset", default=DEFAULT_PRESET, choices=sorted(PRESETS))
    simulate.add_argument("--rows", type=int, default=None)
    simulate.add_argument("--cols", type=int, default=None)
    simulate.add_argument("--days", type=int, default=None)
    simulate.add_argument("--out", type=Path, default=None)
    simulate.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)

    subparsers.add_parser("presets", help="列出内置场景")
    return parser


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from rancho.api import create_app

    work_dir = Path(args.work_dir).resolve()
    dist_dir = Path(args.dist).resolve()
    app = create_app(work_dir, dist_dir)
    url = f"http://{args.host}:{args.port}/"

    if not args.no_open:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()

    print(f"Rancho {__version__} → {url}")
    print(f"  work dir : {work_dir}")
    print(f"  client   : {dist_dir if dist_dir.exists() else '未构建（仅 API）'}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _simulate(args: argparse.Namespace) -> int:
    preset = PRESETS[args.preset]
    config = ScenarioConfig.model_validate(preset["config"])
    overrides = {
        key: value
        for key, value in {"rows": args.rows, "cols": args.cols, "days": args.days}.items()
        if value is not None
    }
    if overrides:
        config = config.model_copy(update=overrides)

    bundle: dict[str, Any] = run_scenario(config, Path(args.work_dir).resolve())
    out_path = Path(args.out) if args.out is not None else Path(f"runs/{bundle['id']}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")

    meta = bundle["meta"]
    last = bundle["series"][-1]
    print(f"scenario : {meta['name']} ({meta['crop']}, {meta['variety']})")
    print(f"grid     : {meta['rows']} × {meta['cols']} cells, {meta['area_ha']} ha")
    print(f"horizon  : {meta['days']} days, seed {meta['seed']}, {meta['elapsed_s']} s")
    print(f"final    : LAI {last['lai']:.2f}, 生物量 {last['biomass']:.1f} g/株")
    print(
        f"yield    : {bundle['yield']['t_per_ha']:.2f} t/ha (收获指数 {bundle['yield']['harvest_index']:.2f} 估算)"
    )
    print(f"bundle   : {out_path}")
    return 0


def _presets() -> int:
    for key, preset in PRESETS.items():
        print(f"{key:<14} {preset['label']}")
        print(f"{'':<14} {preset['description']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    match args.command:
        case "serve":
            return _serve(args)
        case "simulate":
            return _simulate(args)
        case "presets":
            return _presets()
    return 1
