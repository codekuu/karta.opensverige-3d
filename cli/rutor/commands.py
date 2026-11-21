"""One function per rutor command. None of them imports torch at import time."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from rutor import api, config, pipeline, redraw_core
from rutor.errors import RutorError, bad_input, check_failed, environment, network, refused
from rutor.manifest import build_manifest, encode_manifest, png_size, write_manifest
from rutor.check import problems as check_problems
from rutor.render import render_cell


def require_token() -> str:
    token = config.read_token()
    if not token:
        raise environment("not logged in: run rutor login")
    return token


def parse_cell(text: str) -> tuple[int, int]:
    parts = text.split("_")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise bad_input(f"cell must look like 64_59, not {text!r}")
    return int(parts[0]), int(parts[1])


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise network(f"service answer has no {label}")
    parsed: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            parsed[key] = item
    return parsed


def _rows(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise network(f"service answer has no {label}")
    return value


def cmd_cities(args: argparse.Namespace) -> int:
    token = require_token()
    _status, body = api.get("/v1/sheets", token=token)
    if args.json:
        print(json.dumps(body))
        return 0
    for sheet in _rows(_object(body, "object").get("sheets"), "sheets"):
        fields = _object(sheet, "sheet")
        print(f"sheet: {fields.get('id')} {fields.get('state')} {fields.get('done')}/{fields.get('total')}")
    return 0


def _cell_line(cell: object, free_only: bool) -> str | None:
    if not isinstance(cell, list) or len(cell) < 3:
        raise network("service grid cell was not a list")
    col, row, state = cell[0], cell[1], cell[2]
    if free_only and state != "fri":
        return None
    line = f"cell: {col}_{row} {state}"
    login = cell[3] if len(cell) > 3 else None
    if isinstance(login, str) and login:
        line += f" {login}"
    return line


def cmd_grid(args: argparse.Namespace) -> int:
    token = require_token()
    _status, body = api.get(f"/v1/grid/{args.sheet}", token=token)
    if args.json:
        print(json.dumps(body))
        return 0
    payload = _object(body, "object")
    for cell in _rows(payload.get("cells"), "cells"):
        line = _cell_line(cell, args.free)
        if line is not None:
            print(line)
    return 0


def pick_next(sheet: dict[str, object], cells: list[object]) -> tuple[int, int]:
    cols = sheet.get("cols")
    rows = sheet.get("rows")
    if not isinstance(cols, int) or not isinstance(rows, int):
        raise network("sheet is missing cols or rows")
    centre_col = (cols - 1) / 2
    centre_row = (rows - 1) / 2
    parsed: list[tuple[int, int, str]] = []
    for cell in cells:
        if not isinstance(cell, list) or len(cell) < 3:
            continue
        col, row, state = cell[0], cell[1], cell[2]
        if isinstance(col, int) and isinstance(row, int) and isinstance(state, str):
            parsed.append((col, row, state))
    by_pos = {(col, row): state for col, row, state in parsed}
    free = [(col, row) for col, row, state in parsed if state == "fri"]
    if not free:
        raise refused("refused: no_free_cell")

    def beside_live(col: int, row: int) -> bool:
        for d_col in (-1, 0, 1):
            for d_row in (-1, 0, 1):
                if d_col == 0 and d_row == 0:
                    continue
                if by_pos.get((col + d_col, row + d_row)) == "live":
                    return True
        return False

    def distance(col: int, row: int) -> float:
        return (col - centre_col) ** 2 + (row - centre_row) ** 2

    adjacent = [cell for cell in free if beside_live(cell[0], cell[1])]
    pool = adjacent or free
    pool.sort(key=lambda cell: (distance(cell[0], cell[1]), cell[1], cell[0]))
    return pool[0]


def cmd_claim(args: argparse.Namespace) -> int:
    token = require_token()
    if bool(args.next) == bool(args.cell):
        raise bad_input("Pass a cell like 64_59, or --next, and not both.")
    if args.next:
        _status, body = api.get(f"/v1/grid/{args.sheet}", token=token)
        payload = _object(body, "object")
        col, row = pick_next(_object(payload.get("sheet"), "sheet"), _rows(payload.get("cells"), "cells"))
    else:
        col, row = parse_cell(str(args.cell))
    _status, claimed = api.post("/v1/claim", {"sheet": args.sheet, "col": col, "row": row}, token)
    expires = _object(claimed, "object").get("expires_at")
    print(f"claimed: {args.sheet} {col}_{row} expires {expires}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    token = require_token()
    col, row = parse_cell(args.cell)
    api.post("/v1/release", {"sheet": args.sheet, "col": col, "row": row}, token)
    print(f"released: {args.sheet} {col}_{row}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    del args
    token = require_token()
    _status, body = api.get("/v1/me", token=token)
    locks = _rows(_object(body, "object").get("locks"), "locks")
    if not locks:
        print("locks: 0")
        return 0
    for lock in locks:
        fields = _object(lock, "lock")
        print(f"lock: {fields.get('sheet')} {fields.get('col')}_{fields.get('row')} expires {fields.get('expires_at')}")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    del args
    token = require_token()
    _status, body = api.get("/v1/me", token=token)
    print(f"login: {_object(body, 'object').get('login')}")
    return 0


def _python_missing() -> str | None:
    if sys.version_info >= (3, 11):
        return None
    return f"{sys.version_info.major}.{sys.version_info.minor} is older than 3.11"


def _torch_missing() -> str | None:
    try:
        import torch
    except ImportError:
        return "PyTorch is not installed"
    cuda = getattr(torch, "cuda", None)
    available = getattr(cuda, "is_available", None)
    if not callable(available) or not available():
        return "no CUDA device"
    return None


def _bitsandbytes_missing() -> str | None:
    try:
        import bitsandbytes
    except ImportError:
        return "bitsandbytes is not installed"
    del bitsandbytes
    return None


def _render_missing() -> str | None:
    try:
        import numpy
        from PIL import Image
    except ImportError:
        return "pillow and numpy are not installed (pip install pillow numpy)"
    del numpy, Image
    return None


def _service_missing() -> str | None:
    try:
        _status, body = api.get("/v1/health")
    except RutorError as exc:
        return exc.message
    if isinstance(body, dict) and body.get("ok") is True:
        return None
    return "health did not answer ok"


def _login_missing() -> str | None:
    token = config.read_token()
    if not token:
        return "not logged in: run rutor login"
    try:
        _status, body = api.get("/v1/me", token=token)
    except RutorError as exc:
        return exc.message
    login = body.get("login") if isinstance(body, dict) else None
    if isinstance(login, str) and login:
        return None
    return "service did not return a login"


def cmd_preflight(args: argparse.Namespace) -> int:
    del args
    checks = (
        ("python", _python_missing()),
        ("torch", _torch_missing()),
        ("bitsandbytes", _bitsandbytes_missing()),
        ("render", _render_missing()),
        ("service", _service_missing()),
        ("login", _login_missing()),
    )
    missing = False
    for name, problem in checks:
        if problem is None:
            print(f"check: {name} ok")
        else:
            print(f"check: {name} missing {problem}")
            missing = True
    return 3 if missing else 0


def _sheet_body(token: str, sheet_id: str) -> dict[str, object]:
    _status, body = api.get(f"/v1/grid/{sheet_id}", token=token)
    return _object(_object(body, "object").get("sheet"), "sheet")


def cmd_render(args: argparse.Namespace) -> int:
    token = require_token()
    col, row = parse_cell(args.cell)
    sheet = _sheet_body(token, args.sheet)
    out = config.work_dir(args.sheet) / f"{col}_{row}.src.png"
    render_cell(sheet, col, row, out)
    print(f"rendered: {args.sheet} {col}_{row} {out}")
    return 0


def _se3_main() -> Callable[..., int]:
    try:
        from rutor import se3_price
    except ImportError:
        path = config.repo_root() / "scripts" / "se3_price.py"
        spec = importlib.util.spec_from_file_location("se3_price_script", path)
        if spec is None or spec.loader is None:
            raise environment("se3 price check is missing: scripts/se3_price.py")
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        se3_price = loaded
    main = getattr(se3_price, "main", None)
    if not callable(main):
        raise environment("se3 price check has no main")
    return main


def _report_pins() -> None:
    found = {name: "" for name in pipeline.PINS}
    mismatched = pipeline.check_pins(found)
    if any(pin == "" for pin in pipeline.PINS.values()):
        print("pin: unset")
    if mismatched:
        raise environment("pin mismatch: " + ", ".join(mismatched))


def _gpu_name() -> str:
    try:
        import torch
    except ImportError:
        return "unknown"
    cuda = getattr(torch, "cuda", None)
    available = getattr(cuda, "is_available", None)
    if not callable(available) or not available():
        return "unknown"
    device_name = getattr(cuda, "get_device_name", None)
    if not callable(device_name):
        return "unknown"
    return str(device_name(0))


def _source_info(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {"provider": "unknown", "version": "unknown"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise bad_input(f"{path.name} is not JSON") from exc
    payload = _object(raw, path.name)
    source: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            source[key] = value
    if "provider" not in source:
        source["provider"] = "unknown"
    if "version" not in source:
        source["version"] = "unknown"
    return source


def _open_image(path: Path) -> object:
    try:
        from PIL import Image
    except ImportError as exc:
        raise environment("Pillow is not installed. Follow the README install, then run rutor redraw again.") from exc
    return Image.open(path)


def _save_image(image: object, path: Path) -> None:
    save = getattr(image, "save", None)
    if not callable(save):
        raise environment("redraw did not return an image")
    save(path)


def _check_cell(directory: Path, col: int, row: int) -> list[str]:
    """Run the quality check on the source and the redrawn PNG in the work directory."""
    src = directory / f"{col}_{row}.src.png"
    png = directory / f"{col}_{row}.png"
    for path in (src, png):
        if not path.is_file():
            raise bad_input(f"missing {path.name}: run rutor render and rutor redraw first")
    return check_problems(_open_image(src), _open_image(png))


def _record_check(manifest: dict[str, object], found: list[str]) -> None:
    manifest["check"] = "ok" if not found else "failed"
    manifest["check_problems"] = list(found)
    for problem in found:
        print(f"check: {problem}")
    if not found:
        print("check: ok")


def cmd_check(args: argparse.Namespace) -> int:
    col, row = parse_cell(args.cell)
    directory = config.work_dir(args.sheet)
    manifest_path = directory / f"{col}_{row}.manifest.json"
    if not manifest_path.is_file():
        raise bad_input(f"missing {manifest_path.name}: run rutor redraw {args.sheet} {col}_{row} first")
    manifest = _read_manifest(manifest_path)
    found = _check_cell(directory, col, row)
    _record_check(manifest, found)
    write_manifest(manifest, manifest_path)
    if found:
        raise check_failed(f"{args.sheet} {col}_{row} failed the quality check; do not upload it")
    return 0


def cmd_redraw(args: argparse.Namespace) -> int:
    col, row = parse_cell(args.cell)
    if args.wait_for_cheap:
        code = int(_se3_main()())
        if code != 0:
            return code
    message = redraw_core.preflight_message()
    if message:
        raise environment(message)
    _report_pins()
    token = require_token()
    directory = config.work_dir(args.sheet)
    src = directory / f"{col}_{row}.src.png"
    if not src.is_file():
        raise bad_input(f"missing {src.name}: run rutor render {args.sheet} {col}_{row} first")
    sheet = _sheet_body(token, args.sheet)
    name = sheet.get("name")
    if not isinstance(name, str) or not name:
        raise bad_input(f"sheet {args.sheet} has no name")
    seed = args.seed if isinstance(args.seed, int) else None
    try:
        image = redraw_core.redraw_image(_open_image(src), pipeline.prompt_for(name), seed)
    except ValueError as exc:
        raise bad_input(str(exc)) from exc
    out = directory / f"{col}_{row}.png"
    _save_image(image, out)
    rendered_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = build_manifest(
        pipeline=pipeline.PIPELINE,
        seed=seed,
        png=out,
        source=_source_info(directory / f"{col}_{row}.src.json"),
        gpu=_gpu_name(),
        rendered_at=rendered_at,
    )
    print(f"redrawn: {args.sheet} {col}_{row} {out}")
    found = _check_cell(directory, col, row)
    _record_check(manifest, found)
    write_manifest(manifest, directory / f"{col}_{row}.manifest.json")
    if found:
        raise check_failed(f"{args.sheet} {col}_{row} failed the quality check; the PNG is kept but must not be uploaded")
    return 0


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise bad_input(f"{path.name} is not JSON") from exc
    return _object(raw, path.name)


def cmd_upload(args: argparse.Namespace) -> int:
    token = require_token()
    col, row = parse_cell(args.cell)
    directory = config.work_dir(args.sheet)
    png = directory / f"{col}_{row}.png"
    manifest_path = directory / f"{col}_{row}.manifest.json"
    if not png.is_file():
        raise bad_input(f"missing {png.name}: run rutor redraw {args.sheet} {col}_{row} first")
    if not manifest_path.is_file():
        raise bad_input(f"missing {manifest_path.name}: run rutor redraw {args.sheet} {col}_{row} first")
    width, height = png_size(png)
    manifest = _read_manifest(manifest_path)
    told_w = manifest.get("width")
    told_h = manifest.get("height")
    if told_w != width or told_h != height:
        raise bad_input(f"{png.name} is {width}x{height} but the manifest says {told_w}x{told_h}")
    if manifest.get("check") != "ok":
        raise check_failed(f"{args.sheet} {col}_{row} has not passed rutor check: run rutor check {args.sheet} {col}_{row}")
    _status, body = api.put_bytes(
        f"/v1/upload/{args.sheet}/{col}_{row}",
        png.read_bytes(),
        {"Content-Type": "image/png", "X-Manifest": encode_manifest(manifest)},
        token,
    )
    fields = _object(body, "object")
    print(f"uploaded: {args.sheet} {col}_{row} state {fields.get('state')}")
    print(f"url: {fields.get('url')}")
    return 0


def _queue_cells(token: str, state: str) -> list[object]:
    _status, body = api.get(f"/v1/queue?state={state}", token=token)
    return _rows(_object(body, "object").get("cells"), "cells")


def _waiting_line(cell: dict[str, object]) -> str:
    label = "approved" if cell.get("state") == "godkand" else "waiting"
    return (
        f"{label}: {cell.get('sheet')} {cell.get('col')}_{cell.get('row')} "
        f"{cell.get('owner_login')} uploaded {cell.get('uploaded_at')}"
    )


def cmd_queue(args: argparse.Namespace) -> int:
    token = require_token()
    state = args.state if isinstance(args.state, str) else "vantar"
    if state not in ("vantar", "godkand"):
        raise bad_input("state must be vantar or godkand")
    cells = _queue_cells(token, state)
    parsed: list[dict[str, object]] = []
    for cell in cells:
        fields = _object(cell, "cell")
        parsed.append(fields)
        print(_waiting_line(fields))
    show = args.show
    if not isinstance(show, str) or not show:
        return 0
    dest = Path(show)
    dest.mkdir(parents=True, exist_ok=True)
    for fields in parsed:
        sheet = fields.get("sheet")
        col = fields.get("col")
        row = fields.get("row")
        if not isinstance(sheet, str) or isinstance(col, bool) or not isinstance(col, int):
            raise network("queue cell is missing sheet, col or row")
        if isinstance(row, bool) or not isinstance(row, int):
            raise network("queue cell is missing sheet, col or row")
        data = api.download(f"/v1/inkorg/{sheet}/{col}_{row}.png", token)
        (dest / f"{sheet}_{col}_{row}.png").write_bytes(data)
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    token = require_token()
    col, row = parse_cell(args.cell)
    api.post("/v1/approve", {"sheet": args.sheet, "col": col, "row": row}, token)
    print(f"approved: {args.sheet} {col}_{row}")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    comment = args.comment.strip() if isinstance(args.comment, str) else ""
    if not comment or len(comment) > 500:
        raise bad_input("Pass --comment with 1 to 500 characters.")
    token = require_token()
    col, row = parse_cell(args.cell)
    api.post(
        "/v1/reject",
        {"sheet": args.sheet, "col": col, "row": row, "comment": comment},
        token,
    )
    print(f"rejected: {args.sheet} {col}_{row}")
    return 0


def cmd_trust(args: argparse.Namespace) -> int:
    token = require_token()
    trusted = 0 if args.off else 1
    api.post("/v1/trust", {"login": args.login, "trusted": trusted}, token)
    print(f"trust: {args.login} {'off' if args.off else 'on'}")
    return 0


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    cities = sub.add_parser("cities", help="list sheets")
    cities.add_argument("--json", action="store_true")
    cities.set_defaults(func=cmd_cities)

    grid = sub.add_parser("grid", help="list cells on a sheet")
    grid.add_argument("sheet")
    grid.add_argument("--free", action="store_true")
    grid.add_argument("--json", action="store_true")
    grid.set_defaults(func=cmd_grid)

    claim = sub.add_parser("claim", help="lock one cell")
    claim.add_argument("sheet")
    claim.add_argument("cell", nargs="?")
    claim.add_argument("--next", action="store_true")
    claim.set_defaults(func=cmd_claim)

    release = sub.add_parser("release", help="give a lock back")
    release.add_argument("sheet")
    release.add_argument("cell")
    release.set_defaults(func=cmd_release)

    status = sub.add_parser("status", help="show your locks")
    status.set_defaults(func=cmd_status)

    whoami = sub.add_parser("whoami", help="show the logged-in GitHub login")
    whoami.set_defaults(func=cmd_whoami)

    preflight = sub.add_parser("preflight", help="check the machine before a redraw")
    preflight.set_defaults(func=cmd_preflight)

    render = sub.add_parser("render", help="render one cell to a source PNG")
    render.add_argument("sheet")
    render.add_argument("cell")
    render.set_defaults(func=cmd_render)

    redraw = sub.add_parser("redraw", help="redraw a source PNG into pixel art")
    redraw.add_argument("sheet")
    redraw.add_argument("cell")
    redraw.add_argument("--seed", type=int, default=None)
    redraw.add_argument("--wait-for-cheap", action="store_true")
    redraw.set_defaults(func=cmd_redraw)

    check = sub.add_parser("check", help="run the quality check on a redrawn cell")
    check.add_argument("sheet")
    check.add_argument("cell")
    check.set_defaults(func=cmd_check)

    upload = sub.add_parser("upload", help="upload the redrawn PNG")
    upload.add_argument("sheet")
    upload.add_argument("cell")
    upload.set_defaults(func=cmd_upload)

    queue = sub.add_parser("queue", help="list cells waiting for review")
    queue.add_argument("--state", choices=("vantar", "godkand"), default="vantar")
    queue.add_argument("--show", default=None, help="directory to download inbox PNGs into")
    queue.set_defaults(func=cmd_queue)

    approve = sub.add_parser("approve", help="approve one uploaded cell")
    approve.add_argument("sheet")
    approve.add_argument("cell")
    approve.set_defaults(func=cmd_approve)

    reject = sub.add_parser("reject", help="reject one cell and free it")
    reject.add_argument("sheet")
    reject.add_argument("cell")
    reject.add_argument("--comment", default="")
    reject.set_defaults(func=cmd_reject)

    trust = sub.add_parser("trust", help="mark a login trusted or not")
    trust.add_argument("login")
    trust.add_argument("--off", action="store_true")
    trust.set_defaults(func=cmd_trust)
