"""Command line entry points: ``rancho dev``, ``rancho serve`` and ``rancho simulate``."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import warnings
import webbrowser
from pathlib import Path
from typing import Any

from rancho import __version__
from rancho.scenario import DEFAULT_PRESET, PRESETS, ScenarioConfig

# ``cropforge`` 在 import 阶段就会拉进 ``fastapi.middleware.wsgi``（只为了它自带的
# Dash 面板），而 starlette 1.9 起该模块已被弃用。Rancho 用不到它，所以只在这一次
# 导入的范围内屏蔽这条告警：退出 with 后不留任何全局过滤规则，别的告警照常抛。
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r"starlette\.middleware\.wsgi is deprecated",
        category=UserWarning,
        module=r"fastapi\.middleware\.wsgi",
    )
    from rancho.engine import run_scenario

DEFAULT_WORK_DIR = Path(".rancho")
DEFAULT_DIST = Path("frontend/dist")
DEFAULT_FRONTEND = Path("frontend")

DEV_HOST = "127.0.0.1"
DEV_API_PORT = 8000
DEV_CLIENT_PORT = 5173


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rancho",
        description="基于 CropForge 的矩形网格农田仿真台",
    )
    parser.add_argument("--version", action="version", version=f"rancho {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dev = subparsers.add_parser("dev", help="一条命令同时启动后端 API 与前端热更新")
    dev.add_argument("--host", default=DEV_HOST)
    dev.add_argument("--api-port", type=int, default=DEV_API_PORT, help="后端端口")
    dev.add_argument("--client-port", type=int, default=DEV_CLIENT_PORT, help="前端端口")
    dev.add_argument(
        "--frontend", type=Path, default=None, help="前端工程目录（默认仓库根下的 frontend/）"
    )
    dev.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    dev.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)

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


def _find_repo_root() -> Path:
    """Locate the checkout that owns ``frontend/package.json``."""
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for candidate in (start, *start.parents):
            if (candidate / DEFAULT_FRONTEND / "package.json").is_file():
                return candidate
    return Path.cwd()


def _ensure_free(host: str, port: int, label: str, flag: str) -> None:
    """Fail early with a readable message instead of letting uvicorn/vite crash."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            raise SystemExit(
                f"{label}端口 {port} 已被占用：先停掉占用进程，或用 {flag} 换一个"
            ) from None


def _client_command(frontend: Path, host: str, port: int) -> list[str]:
    """Prefer running vite's entry directly so the dev server stays one process."""
    vite_entry = frontend / "node_modules" / "vite" / "bin" / "vite.js"
    node = shutil.which("node")
    if node is not None and vite_entry.is_file():
        return [node, str(vite_entry), "--host", host, "--port", str(port)]

    if shutil.which("pnpm") is None:
        raise SystemExit("找不到 node/pnpm：请先安装 Node.js")
    if not (frontend / "node_modules").is_dir():
        raise SystemExit(f"{frontend} 下缺少 node_modules：先执行 cd frontend && pnpm install")
    return ["pnpm", "dev", "--host", host, "--port", str(port)]


def _terminate(proc: subprocess.Popen[str] | subprocess.Popen[bytes]) -> None:
    """Stop the client process; we never read its streams, so either mode is fine."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # vite may hang on to a worker; /T takes the whole tree down.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def _dev(args: argparse.Namespace) -> int:
    root = _find_repo_root()
    frontend = (args.frontend or root / DEFAULT_FRONTEND).resolve()
    if not (frontend / "package.json").is_file():
        raise SystemExit(f"{frontend} 下没有 package.json：从仓库根目录运行，或用 --frontend 指定")

    _ensure_free(args.host, args.api_port, "后端", "--api-port")
    _ensure_free(args.host, args.client_port, "前端", "--client-port")

    import uvicorn

    from rancho.api import create_app

    work_dir = Path(args.work_dir).resolve()
    # 开发期前端由 vite 提供，后端只负责 /api，不必挂 dist。
    app = create_app(work_dir, None)
    server = uvicorn.Server(
        uvicorn.Config(app, host=args.host, port=args.api_port, log_level="info")
    )
    backend = threading.Thread(target=server.run, name="rancho-api", daemon=True)
    backend.start()
    while not server.started and backend.is_alive():
        time.sleep(0.05)
    if not backend.is_alive():
        print("后端启动失败，详见上方 uvicorn 日志", flush=True)
        return 1

    client_url = f"http://{args.host}:{args.client_port}/"
    env = {**os.environ, "RANCHO_API": f"http://{args.host}:{args.api_port}"}
    print(f"Rancho {__version__} · 开发模式")
    print(f"  前端 {client_url}")
    print(f"  接口 http://{args.host}:{args.api_port}/api/docs")
    print("  Ctrl+C 同时结束前后端\n", flush=True)

    proc = subprocess.Popen(
        _client_command(frontend, args.host, args.client_port), cwd=frontend, env=env
    )
    if not args.no_open:
        threading.Timer(1.8, webbrowser.open, args=(client_url,)).start()

    try:
        while proc.poll() is None and backend.is_alive():
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n正在停止前后端…", flush=True)
    finally:
        _terminate(proc)
        server.should_exit = True
        backend.join(timeout=8)
    return 0


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
        case "dev":
            return _dev(args)
        case "serve":
            return _serve(args)
        case "simulate":
            return _simulate(args)
        case "presets":
            return _presets()
    return 1
