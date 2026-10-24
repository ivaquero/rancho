"""Command line entry points: ``rancho dev``, ``rancho serve``, ``rancho simulate``."""

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
from datetime import date, timedelta
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
    simulate.add_argument(
        "--weather",
        choices=("synthetic", "open-meteo"),
        default=None,
        help="气象来源：合成气候或按经纬度取实测",
    )
    simulate.add_argument("--year", type=int, default=None, help="实测气象的年份")
    simulate.add_argument("--offline", action="store_true", help="实测气象只读缓存")
    simulate.add_argument("--out", type=Path, default=None)
    simulate.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)

    weather = subparsers.add_parser("weather", help="按经纬度拉取开源日尺度气象")
    weather.add_argument(
        "--preset", default=None, choices=sorted(PRESETS), help="用场景的坐标与播期"
    )
    weather.add_argument("--lat", type=float, default=None)
    weather.add_argument("--lon", type=float, default=None)
    weather.add_argument("--start", type=date.fromisoformat, default=None, help="YYYY-MM-DD")
    weather.add_argument("--days", type=int, default=None)
    weather.add_argument("--year", type=int, default=None, help="用 --preset 时覆盖年份")
    weather.add_argument("--protected", action="store_true", help="换算成棚内小气候")
    weather.add_argument(
        "--transmission", type=float, default=None, help="棚体透光率（默认取场景值）"
    )
    weather.add_argument("--csv", type=Path, default=None, help="另存为 CropForge 气象 CSV")
    weather.add_argument("--json", action="store_true", help="输出 JSON（含逐日序列）")
    weather.add_argument("--cache-dir", type=Path, default=None)
    weather.add_argument("--offline", action="store_true", help="只读缓存，不联网")
    weather.add_argument("--timeout", type=float, default=20.0)

    subparsers.add_parser("presets", help="列出内置场景")
    subparsers.add_parser("crops", help="列出作物模板（TOML）及其路径")

    econ = subparsers.add_parser("econ", help="大棚经济层原型：Mesa 竞价、成本曲线与验证")
    econ.add_argument("--site", default="tropical", help="气象站点：tropical / temperate")
    econ.add_argument("--days", type=int, default=28, help="仿真天数（逐小时推进）")
    econ.add_argument("--seed", type=int, default=42)
    econ.add_argument(
        "--tariff",
        default="farm,cheap",
        help="逗号分隔的电价情景：farm（农业峰谷）、cheap（谷段优惠）",
    )
    econ.add_argument(
        "--growth",
        default="cropforge",
        choices=("cropforge", "analytic", "farm"),
        help=(
            "产量来源：farm（真跑 CropForge 农场并与市场迭代）/ cropforge（插件 + "
            "Beer-Lambert）/ analytic（饱和光响应代理）"
        ),
    )
    econ.add_argument(
        "--farm-passes",
        type=int,
        default=3,
        help="--growth farm 时市场与农场交替迭代的最大轮数（收敛即提前停止）",
    )
    econ.add_argument("--out-dir", type=Path, default=Path("runs/econ"))
    econ.add_argument("--no-open", action="store_true", help="不自动打开 HTML 报告")
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
    weather_overrides = {
        key: value
        for key, value in {
            "source": args.weather,
            "year": args.year,
            "offline": args.offline or None,
        }.items()
        if value is not None
    }
    if weather_overrides:
        config = config.model_copy(
            update={"weather": config.weather.model_copy(update=weather_overrides)}
        )

    bundle: dict[str, Any] = run_scenario(config, Path(args.work_dir).resolve())
    out_path = Path(args.out) if args.out is not None else Path(f"runs/{bundle['id']}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")

    meta = bundle["meta"]
    last = bundle["series"][-1]
    weather = meta.get("weather") or {}
    print(f"scenario : {meta['name']} ({meta['crop']}, {meta['variety']})")
    print(f"grid     : {meta['rows']} × {meta['cols']} cells, {meta['area_ha']} ha")
    print(f"horizon  : {meta['days']} days, seed {meta['seed']}, {meta['elapsed_s']} s")
    if weather:
        print(f"weather  : {weather['label']} · {weather['start']} → {weather['end']}")
    print(f"final    : LAI {last['lai']:.2f}, 生物量 {last['biomass']:.1f} g/株")
    print(
        f"yield    : {bundle['yield']['t_per_ha']:.2f} t/ha (收获指数 {bundle['yield']['harvest_index']:.2f} 估算)"
    )
    print(f"bundle   : {out_path}")
    return 0


def _print_weather(series: Any) -> None:
    meta = series.meta()
    print(f"来源     : {meta['label']}（{meta['provider']}）")
    if meta["grid"]:
        print(
            f"网格点   : {meta['grid'][0]}, {meta['grid'][1]} · 海拔 {meta['elevation_m']} m"
            f" · {meta['timezone']}"
        )
    print(f"时段     : {meta['start']} → {meta['end']}（{meta['days']} 天）")
    print(f"气温     : 均 {meta['mean_temp_c']} °C（{meta['min_temp_c']}–{meta['max_temp_c']}）")
    print(f"光照     : 均 {meta['mean_radiation_mj']} MJ/m²/d")
    print(f"降水     : 合计 {meta['rain_total_mm']} mm，雨日 {meta['rain_days']} 天")
    print(f"湿度     : 均 {meta['mean_humidity_pct']} %")
    print(f"风速     : 均 {meta['mean_wind_ms']} m/s")
    for note in meta["notes"]:
        print(f"备注     : {note}")


def _weather(args: argparse.Namespace) -> int:
    from rancho.weather import WeatherError, fetch_weather, write_weather_csv

    preset = ScenarioConfig.model_validate(PRESETS[args.preset]["config"]) if args.preset else None
    latitude = args.lat if args.lat is not None else (preset.latitude if preset else None)
    longitude = args.lon if args.lon is not None else (preset.longitude if preset else None)
    if latitude is None or longitude is None:
        raise SystemExit("需要 --lat/--lon，或用 --preset 指定场景坐标")

    if args.start is not None:
        start = args.start
    elif preset is not None:
        year = args.year if args.year is not None else preset.weather.year
        start = date(year, 1, 1) + timedelta(days=preset.sowing_doy - 1)
    else:
        raise SystemExit("需要 --start YYYY-MM-DD（或用 --preset 从播期推算）")

    days = args.days if args.days is not None else (preset.days if preset else 30)
    protected = args.protected or bool(preset and preset.protected)
    if args.transmission is not None:
        transmission = args.transmission
    elif preset is not None:
        transmission = preset.cover_transmission
    else:
        transmission = 1.0
    cache_dir = Path(args.cache_dir) if args.cache_dir is not None else DEFAULT_WORK_DIR / "weather"

    try:
        series = fetch_weather(
            latitude,
            longitude,
            start,
            days,
            cache_dir=cache_dir,
            timeout_s=args.timeout,
            offline=args.offline,
            protected=protected,
            cover_transmission=transmission,
        )
    except WeatherError as exc:
        raise SystemExit(f"取数失败：{exc}") from exc

    if args.json:
        payload = {
            "meta": series.meta(),
            "days": [
                {
                    "date": day.day.isoformat(),
                    "tmax_c": round(day.tmax_c, 1),
                    "tmin_c": round(day.tmin_c, 1),
                    "radiation_mj": round(day.radiation_mj, 1),
                    "rainfall_mm": round(day.rainfall_mm, 1),
                    "humidity_pct": round(day.humidity_pct),
                    "wind_ms": round(day.wind_ms, 1),
                }
                for day in series.days
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_weather(series)
    if args.csv is not None:
        write_weather_csv(args.csv, series)
        print(f"csv      : {args.csv.resolve()}")
    return 0


def _presets() -> int:
    for key, preset in PRESETS.items():
        config = ScenarioConfig.model_validate(preset["config"])
        source = "实测" if config.weather.source == "open-meteo" else "合成"
        print(f"{key:<14} {preset['label']}  [{source}气象]")
        print(f"{'':<14} {preset['description']}")
    return 0


def _crops() -> int:
    """Print the loaded templates — the manual-entry checklist, in one place."""
    from rancho.crops import TEMPLATE_DIR, TEMPLATES

    for key, crop in TEMPLATES.items():
        print(f"{key:<10} {crop.label}")
        print(
            f"{'':<10} 积温 {crop.season_gdd:>6.0f} °C·d · RUE {crop.growth.rue:.2f} · "
            f"LAI≤ {crop.growth.lai_max:.1f} · 根区 {crop.water.root_zone_mm:.0f} mm · "
            f"Kc {crop.water.kc_mid:.2f} · p {crop.water.depletion_fraction:.2f} · "
            f"追肥 {len(crop.water.splits)} 次"
        )
        print(f"{'':<10} {crop.species} · {len(crop.stages)} 个物候阶段")
    print(f"\n模板目录 : {TEMPLATE_DIR}")
    return 0


def _econ(args: argparse.Namespace) -> int:
    """Run the greenhouse economics probe and write the report bundle."""
    from rancho.econ.probe import probe
    from rancho.econ.report import write_csv, write_html, write_json
    from rancho.econ.spec import SITES, TARIFFS

    if args.site not in SITES:
        raise SystemExit(f"未知站点：{args.site}（可选 {', '.join(SITES)}）")
    tariff_keys = tuple(key.strip() for key in args.tariff.split(",") if key.strip())
    unknown = [key for key in tariff_keys if key not in TARIFFS]
    if not tariff_keys or unknown:
        raise SystemExit(f"未知电价情景：{', '.join(unknown) or args.tariff}")

    result = probe(
        site_key=args.site,
        days=args.days,
        seed=args.seed,
        tariff_keys=tariff_keys,
        growth=args.growth,
        passes=max(1, args.farm_passes),
    )

    out_dir = Path(args.out_dir) / result.id
    write_json(result, out_dir / "probe.json")
    write_csv(result, out_dir / "daily.csv")
    report = write_html(
        result,
        out_dir / "report.html",
        title=f"Rancho 经济层原型 · {result.site.label}",
    )

    print(f"site     : {result.site.label} · {result.days} 天 · seed {result.seed}")
    print(f"growth   : {result.growth}")
    if result.coupling is not None:
        loop = result.coupling
        state = "已收敛" if loop["converged"] else "未收敛（达到轮数上限）"
        print(
            f"coupling : {loop['passes']} 轮 · 产量残差 {loop['residual'] * 100:.3f}% · "
            f"{state} · {loop['grid']}×{loop['grid']} 田间网格"
        )
    print(f"run      : {result.id}")
    for regime in result.regimes:
        print(
            f"\n情景     : {regime.tariff.label}（峰 {regime.tariff.peak} / 平 "
            f"{regime.tariff.flat} / 谷 {regime.tariff.valley} 元/kWh）"
        )
        print(f"{'作物':<6}{'独享':>10}{'共享':>10}{'变动':>10}{'产量':>10}{'毛利':>10}{'逾配':>8}")
        for item in regime.comparisons:
            margin = (
                f"{item.delta_margin_yuan:>8.0f}元"
                if item.margin_flat
                else f"{item.delta_margin * 100:>8.1f}%"
            )
            print(
                f"{item.label:<6}{item.solo_unit_cost:>10.2f}{item.pooled_unit_cost:>10.2f}"
                f"{item.delta_unit * 100:>9.1f}%{item.delta_yield * 100:>9.1f}%"
                f"{margin}{item.pressure * 100:>7.0f}%"
            )
        print(f"结论     : {regime.verdict.summary}")

    print(f"\nreport   : {report.resolve()}")
    print(f"daily    : {(out_dir / 'daily.csv').resolve()}")
    if not args.no_open:
        webbrowser.open(report.resolve().as_uri())
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
        case "weather":
            return _weather(args)
        case "presets":
            return _presets()
        case "crops":
            return _crops()
        case "econ":
            return _econ(args)
    return 1
