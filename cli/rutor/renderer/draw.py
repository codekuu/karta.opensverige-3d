"""Oblique orthophoto render. The drawing matches render_putsad.py.

Roofs and ground come from the orthophoto. Building footprints come from
OpenStreetMap and are lifted to a height from their tags. Walls get storeys,
window rows, a shopfront ground floor and a cornice. The camera is the pixel
cities' 45-degree oblique orthographic view, 0.25 m per pixel, east as is,
north compressed by cos 45, height lifted by sin 45.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np
from PIL import Image, ImageDraw

MPP = 0.25
PHOTO_LUM_MEAN = 118.0
PHOTO_LUM_STD = 52.0
SIDE = 1536
T = math.sqrt(0.5)
DEFAULT_HEIGHT = 18.0
STOREY = 3.3
GROUND_STOREY = 4.0
BAY_PX = 12  # one window bay is 3 m at 4 px per metre east-west

# Göteborg plaster, stone and brick, light to dark.
PALETTE = np.array(
    [
        (226, 214, 186),
        (214, 190, 150),
        (232, 222, 200),
        (196, 120, 92),
        (170, 104, 84),
        (210, 200, 182),
        (188, 176, 160),
        (224, 196, 140),
        (150, 150, 146),
        (206, 168, 120),
    ],
    dtype=np.float32,
)
GLASS = np.array((52, 64, 78), dtype=np.float32)
GLASS_HI = np.array((96, 118, 138), dtype=np.float32)
SHOP = np.array((40, 46, 54), dtype=np.float32)


def height_of(tags: dict[str, object]) -> float:
    for key in ("height", "building:height"):
        if key in tags:
            try:
                return float(str(tags[key]).replace("m", "").strip())
            except ValueError:
                pass
    if "building:levels" in tags:
        try:
            return STOREY * float(str(tags["building:levels"])) + 1.5
        except ValueError:
            pass
    kind = tags.get("building", "yes")
    if kind in ("garage", "garages", "shed", "hut", "kiosk", "roof", "carport"):
        return 3.0
    if kind in ("church", "cathedral"):
        return 24.0
    return DEFAULT_HEIGHT


def _rng_for(key: str) -> np.random.Generator:
    """Randomness tied to a place in the world, so every cell draws a tree the same way."""
    return np.random.default_rng(int(hashlib.sha1(key.encode()).hexdigest()[:16], 16))


def plaster_for(key: str) -> np.ndarray:
    digest = int(hashlib.sha1(key.encode()).hexdigest(), 16)
    return PALETTE[digest % len(PALETTE)]


def wall_pixels(
    metres: np.ndarray,
    sx: np.ndarray,
    plaster: np.ndarray,
    top: np.ndarray,
) -> np.ndarray:
    """Colour of wall pixels at height `metres` above ground, sheet column `sx`.

    `sx` counts from the sheet's left edge, so window bays line up across cells.
    """
    out = plaster * 0.86
    bay = sx % BAY_PX
    upper = metres >= GROUND_STOREY
    in_storey = np.where(upper, (metres - GROUND_STOREY) % STOREY, metres)
    window = upper & (in_storey > 0.9) & (in_storey < 2.3) & (bay >= 3) & (bay <= 8) & (metres < top - 0.9)
    glass = np.where((in_storey > 1.9)[:, None], GLASS_HI, GLASS)
    out = np.where(window[:, None], glass, out)
    shop = (~upper) & (metres > 0.4) & (metres < 3.0) & (bay >= 1) & (bay <= 10)
    out = np.where(shop[:, None], SHOP, out)
    band = (~upper) & (metres >= 3.2)
    out = np.where(band[:, None], plaster * 0.7, out)
    cornice = metres >= top - 0.6
    out = np.where(cornice[:, None], np.minimum(plaster * 1.08, 255), out)
    return out


def _elements(data: dict[str, object]) -> list[dict[str, object]]:
    raw = data.get("elements")
    if not isinstance(raw, list):
        raise ValueError("osm json has no elements list")
    elements: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("osm element is not an object")
        elements.append({key: value for key, value in item.items() if isinstance(key, str)})
    return elements


def _ident(element: dict[str, object]) -> int:
    ident = element.get("id")
    if isinstance(ident, bool) or not isinstance(ident, int):
        raise ValueError("osm element has no id")
    return ident


def _tags(element: dict[str, object]) -> dict[str, object]:
    raw = element.get("tags", {})
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if isinstance(key, str)}


def _node_ids(element: dict[str, object]) -> list[int]:
    raw = element.get("nodes")
    if not isinstance(raw, list):
        return []
    ident: list[int] = []
    for item in raw:
        if isinstance(item, int) and not isinstance(item, bool):
            ident.append(item)
    return ident


def _coord(element: dict[str, object], key: str) -> float:
    value = element.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"osm node has no {key}")
    return float(value)


def draw(
    orto_image: Image.Image,
    osm: dict[str, object],
    trees: dict[str, object],
    lat: float,
    lon: float,
    west: float,
    south: float,
    east: float,
    north: float,
    lift_m: float = 0.0,
    grid_lat: float | None = None,
    sheet_x0: int = 0,
) -> Image.Image:
    """Project one orthophoto and its OSM documents into the 1536 px cell window."""
    orto = np.asarray(orto_image.convert("RGB"))
    rows, cols = orto.shape[:2]
    elements = _elements(osm)
    nodes = {
        _ident(element): (_coord(element, "lat"), _coord(element, "lon"))
        for element in elements
        if element.get("type") == "node"
    }
    ways = {_ident(element): element for element in elements if element.get("type") == "way"}

    def to_pixel(point_lat: float, point_lon: float) -> tuple[float, float]:
        return (point_lon - west) / (east - west) * cols, (north - point_lat) / (north - south) * rows

    rings: list[tuple[list[int], float, str]] = []
    for way in ways.values():
        tags = _tags(way)
        if "building" in tags and tags.get("building") != "no":
            rings.append((_node_ids(way), height_of(tags), f"w{_ident(way)}"))
    for rel in (element for element in elements if element.get("type") == "relation"):
        tags = _tags(rel)
        if "building" not in tags:
            continue
        members = rel.get("members")
        if not isinstance(members, list):
            continue
        for member in members:
            if not isinstance(member, dict):
                continue
            ref = member.get("ref")
            if (
                member.get("type") == "way"
                and member.get("role") == "outer"
                and isinstance(ref, int)
                and not isinstance(ref, bool)
                and ref in ways
            ):
                rings.append((_node_ids(ways[ref]), height_of(tags), f"r{_ident(rel)}"))
    rings.sort(key=lambda ring: ring[1])

    id_img = Image.new("I", (cols, rows), 0)
    id_draw = ImageDraw.Draw(id_img)
    heights = [0.0]
    plasters = [np.zeros(3, dtype=np.float32)]
    for ring, height, key in rings:
        pts = [to_pixel(*nodes[node_id]) for node_id in ring if node_id in nodes]
        if len(pts) >= 3:
            heights.append(height)
            plasters.append(plaster_for(key))
            id_draw.polygon(pts, fill=len(heights) - 1)
    ids = np.asarray(id_img, dtype=np.int32)
    height_lut = np.array(heights, dtype=np.float32)
    plaster_lut = np.stack(plasters)
    height = height_lut[ids]

    # Vegetation from OSM: crowns as a height field, lawns as a ground tint.
    tree_elements = _elements(trees)
    tnodes = {_ident(element): element for element in tree_elements if element.get("type") == "node"}
    tways = {_ident(element): element for element in tree_elements if element.get("type") == "way"}
    # One longitude scale per sheet keeps every cell on the same metre grid.
    cos_ref = math.cos(math.radians(lat if grid_lat is None else grid_lat))
    ppm_x = cols / ((east - west) * 111320.0 * cos_ref)
    ppm_y = rows / ((north - south) * 111320.0)
    crown_img = Image.new("F", (cols, rows), 0.0)
    crown_id = Image.new("I", (cols, rows), 0)
    lawn_img = Image.new("L", (cols, rows), 0)
    cdraw, idraw, ldraw = ImageDraw.Draw(crown_img), ImageDraw.Draw(crown_id), ImageDraw.Draw(lawn_img)
    crowns: list[tuple[float, float, float, float, str]] = []
    lon_step_per_m = 1.0 / (111320.0 * cos_ref)
    lat_step_per_m = 1.0 / 111320.0

    def world_grid(step_m: float) -> list[tuple[int, int, float, float]]:
        """Grid points every step_m metres, counted from lat 0 / lon 0, inside this image."""
        lon_step = step_m * lon_step_per_m
        lat_step = step_m * lat_step_per_m
        points: list[tuple[int, int, float, float]] = []
        for j in range(math.ceil(south / lat_step), math.floor(north / lat_step) + 1):
            for i in range(math.ceil(west / lon_step), math.floor(east / lon_step) + 1):
                px, py = to_pixel(j * lat_step, i * lon_step)
                points.append((i, j, px, py))
        return points

    def num(tags: dict[str, object], key: str, default: float) -> float:
        try:
            return float(str(tags.get(key, default)).split()[0].replace("m", ""))
        except ValueError:
            return default

    def ring_px(way: dict[str, object]) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        for node_id in _node_ids(way):
            node = tnodes.get(node_id)
            if node is not None:
                points.append(to_pixel(_coord(node, "lat"), _coord(node, "lon")))
        return points

    for element in tnodes.values():
        tree_tags = _tags(element)
        if tree_tags.get("natural") == "tree":
            px, py = to_pixel(_coord(element, "lat"), _coord(element, "lon"))
            tree_height = num(tree_tags, "height", 11.0)
            radius = num(tree_tags, "diameter_crown", 7.0) / 2
            crowns.append(
                (px, py, max(2.0, min(radius, 7.0)), max(5.0, min(tree_height, 22.0)), f"node:{_ident(element)}")
            )
    for way in tways.values():
        tree_tags = _tags(way)
        pts = ring_px(way)
        if len(pts) < 2:
            continue
        if tree_tags.get("natural") == "tree_row":
            for segment, ((x0, y0), (x1, y1)) in enumerate(zip(pts, pts[1:])):
                seg_m = math.hypot((x1 - x0) / ppm_x, (y1 - y0) / ppm_y)
                for i in range(max(1, int(seg_m / 7.0))):
                    fraction = (i + 0.5) / max(1, int(seg_m / 7.0))
                    crowns.append(
                        (
                            x0 + (x1 - x0) * fraction,
                            y0 + (y1 - y0) * fraction,
                            3.2,
                            10.0,
                            f"row:{_ident(way)}:{segment}:{i}",
                        )
                    )
        wood = tree_tags.get("natural") in ("wood", "scrub") or tree_tags.get("landuse") == "forest"
        lawn = (
            wood
            or tree_tags.get("landuse") in ("grass", "meadow", "village_green", "recreation_ground", "cemetery")
            or tree_tags.get("leisure") in ("park", "garden", "playground", "dog_park")
            or tree_tags.get("natural") == "grassland"
        )
        if len(pts) >= 3 and lawn:
            ldraw.polygon(pts, fill=255)
        if len(pts) >= 3 and wood:
            mask = Image.new("L", (cols, rows), 0)
            ImageDraw.Draw(mask).polygon(pts, fill=1)
            occupied = np.asarray(mask, dtype=bool)
            for i, j, gx, gy in world_grid(5.0):
                xi, yi = int(gx), int(gy)
                if not (0 <= xi < cols and 0 <= yi < rows) or not occupied[yi, xi]:
                    continue
                rng = _rng_for(f"wood:{i}:{j}")
                jx, jy = rng.uniform(-2, 2, 2)
                crowns.append(
                    (
                        gx + jx * ppm_x,
                        gy + jy * ppm_y,
                        rng.uniform(3.0, 4.5),
                        rng.uniform(12.0, 17.0),
                        f"wood:{i}:{j}",
                    )
                )
    # Unmapped trees: in parks, woods and yards far from streets, where the April photo
    # shows the busy texture of bare crowns, plant a crown every 8 m.
    lum0 = orto.astype(np.float32).mean(axis=2)

    def box_mean(values: np.ndarray, radius: int) -> np.ndarray:
        # float64: a float32 running sum loses precision with distance from the corner.
        cumulative = np.pad(values.astype(np.float64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        height_px, width_px = values.shape
        y0 = np.clip(np.arange(height_px) - radius, 0, height_px)
        y1 = np.clip(np.arange(height_px) + radius + 1, 0, height_px)
        x0 = np.clip(np.arange(width_px) - radius, 0, width_px)
        x1 = np.clip(np.arange(width_px) + radius + 1, 0, width_px)
        total = cumulative[y1][:, x1] - cumulative[y0][:, x1] - cumulative[y1][:, x0] + cumulative[y0][:, x0]
        area = (y1 - y0)[:, None] * (x1 - x0)[None, :]
        return total / area

    radius = round(2 * ppm_x)
    variance = box_mean(lum0**2, radius) - box_mean(lum0, radius) ** 2
    busy = np.sqrt(np.maximum(variance, 0)) > 22
    road_img = Image.new("L", (cols, rows), 0)
    road_draw = ImageDraw.Draw(road_img)
    big = {
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "residential",
        "unclassified",
        "living_street",
        "pedestrian",
        "service",
    }
    for way in ways.values():
        if _tags(way).get("highway") in big:
            pts = [to_pixel(*nodes[node_id]) for node_id in _node_ids(way) if node_id in nodes]
            if len(pts) >= 2:
                road_draw.line(pts, fill=255, width=round(2 * 9 * ppm_x))
    near_road = np.asarray(road_img) > 0
    lawn0 = np.asarray(lawn_img) > 0
    open_ground = (ids == 0) & ~near_road
    plant = busy & (open_ground | (lawn0 & (ids == 0)))
    for i, j, gx, gy in world_grid(8.0):
        xi, yi = int(gx), int(gy)
        if not (0 <= xi < cols and 0 <= yi < rows) or not plant[yi, xi]:
            continue
        rng = _rng_for(f"yard:{i}:{j}")
        if rng.random() < 0.25:
            continue
        jx, jy = rng.uniform(-4.0, 4.0, 2)
        crowns.append(
            (gx + jx * ppm_x, gy + jy * ppm_y, rng.uniform(2.6, 4.8), rng.uniform(8.0, 15.0), f"yard:{i}:{j}")
        )
    yard_lawn = open_ground & busy
    lawn_mask = np.asarray(lawn_img).copy()
    lawn_mask[yard_lawn] = 255
    lawn_img = Image.fromarray(lawn_mask)
    expanded: list[tuple[float, ...]] = []
    for px, py, crown_radius, crown_height, key in crowns:
        rng = _rng_for(f"crown:{key}")
        crown_radius = crown_radius * rng.uniform(0.7, 1.35)
        crown_height = crown_height * rng.uniform(0.8, 1.25)
        green_index = float(rng.integers(0, 7))
        for _blob in range(int(rng.integers(2, 5))):
            angle = rng.uniform(0, 2 * math.pi)
            offset = rng.uniform(0, 0.45) * crown_radius
            expanded.append(
                (
                    px + math.cos(angle) * offset * ppm_x,
                    py + math.sin(angle) * offset * ppm_y,
                    crown_radius * rng.uniform(0.55, 0.85),
                    crown_height * rng.uniform(0.88, 1.06),
                    green_index,
                    rng.uniform(0.85, 1.15),
                )
            )
    # Sort by height, then position, so draw order never depends on input order.
    expanded.sort(key=lambda crown: (crown[3], crown[0], crown[1]))
    for index, (px, py, crown_radius, crown_height, _green, _brightness) in enumerate(expanded, start=1):
        box = (
            px - crown_radius * ppm_x,
            py - crown_radius * ppm_y,
            px + crown_radius * ppm_x,
            py + crown_radius * ppm_y,
        )
        cdraw.ellipse(box, fill=crown_height)
        idraw.ellipse(box, fill=index)
    crown_h = np.asarray(crown_img, dtype=np.float32)
    cid = np.asarray(crown_id, dtype=np.int32)
    on_building = ids > 0
    crown_h = np.where(on_building, 0.0, crown_h)
    crown_table = np.array([(0.0, 0.0, 0.0, 0.0, 0.0, 1.0)] + expanded, dtype=np.float32)
    grid_y, grid_x = np.mgrid[0:rows, 0:cols]
    distance = np.hypot((grid_x - crown_table[cid, 0]) / ppm_x, (grid_y - crown_table[cid, 1]) / ppm_y) / np.maximum(
        crown_table[cid, 2], 1e-3
    )
    crown_h = np.where(crown_h > 0, crown_h * (0.78 + 0.22 * np.sqrt(np.clip(1 - distance**2, 0, 1))), 0.0)
    lawn = (np.asarray(lawn_img) > 0) & ~on_building
    crown_lut = np.array([(0.0, 0.0, 0.0, 0.0, 0.0, 1.0)] + expanded, dtype=np.float32)

    # Crown colour: a green per tree, lit from the top of the crown, textured by the photo.
    greens = np.array(
        [
            (70, 130, 50),
            (88, 146, 56),
            (58, 112, 46),
            (104, 154, 62),
            (78, 122, 44),
            (96, 136, 52),
            (64, 124, 60),
        ],
        dtype=np.float32,
    )
    lum = orto.astype(np.float32).mean(axis=2)
    # Fixed photo statistics (Göteborg 2025, two cells) instead of this crop's own,
    # so a crown gets the same texture in every cell that sees it.
    texture = np.clip(0.8 + 0.4 * (lum - PHOTO_LUM_MEAN) / PHOTO_LUM_STD * 0.5, 0.65, 1.25)
    yy, xx = np.mgrid[0:rows, 0:cols]
    cx, cy, cr = crown_lut[cid, 0], crown_lut[cid, 1], crown_lut[cid, 2]
    dist = np.hypot((xx - cx) / ppm_x, (yy - cy) / ppm_y) / np.maximum(cr, 1e-3)
    north_side = np.clip((cy - yy) / ppm_y / np.maximum(cr, 1e-3), -1, 1)
    light = 1.0 + 0.18 * north_side - 0.25 * dist**2
    crown_rgb = greens[crown_lut[cid, 4].astype(np.int32)] * (crown_lut[cid, 5] * light * texture)[..., None]

    # Lawn tint keeps the photo's texture but moves it to summer grass.
    photo = orto.astype(np.float32)
    grass = np.array((118, 162, 76), dtype=np.float32) * (0.75 + 0.5 * (lum / 255.0))[..., None]
    photo = np.where(lawn[..., None], photo * 0.35 + grass * 0.65, photo)

    # Roofs: warmer and more saturated than an April photo.
    red, green, blue = photo[..., 0], photo[..., 1], photo[..., 2]
    grey = photo.mean(axis=2, keepdims=True)
    tile = (red > green + 12) & (red > blue + 20)
    copper = (green > red + 4) & (green >= blue - 6)
    tile_colour = (grey + (photo - grey) * 1.9) * np.array((1.16, 0.9, 0.7), dtype=np.float32)
    copper_colour = (grey + (photo - grey) * 1.5) * np.array((0.92, 1.08, 1.0), dtype=np.float32)
    other_colour = (grey + (photo - grey) * 1.15) * np.array((1.03, 1.0, 0.95), dtype=np.float32)
    graded = np.where(tile[..., None], tile_colour, np.where(copper[..., None], copper_colour, other_colour))
    photo = np.where(on_building[..., None], graded, photo)
    # A warmer summer light everywhere; the April photo leans cold.
    photo = photo * np.array((1.04, 1.01, 0.93), dtype=np.float32)
    top_rgb = np.where((crown_h > 0)[..., None], crown_rgb, photo)
    top_rgb = np.clip(top_rgb, 0, 255).astype(np.uint8)
    height = np.where(on_building, height, crown_h)

    lon_c = west + (np.arange(cols) + 0.5) / cols * (east - west)
    lat_r = north - (np.arange(rows) + 0.5) / rows * (north - south)
    x_m = (lon_c - lon) * 111320.0 * cos_ref
    y_m = (lat_r - lat) * 111320.0
    # Pixel centres land on half-pixel screen positions when the box is snapped to
    # the sheet grid, so floor (the column that holds the centre), never round.
    sx = np.floor(SIDE / 2 + x_m / MPP).astype(np.int32)
    col_ok = (sx >= 0) & (sx < SIDE)

    canvas = np.zeros((SIDE, SIDE, 3), dtype=np.uint8)
    filled = np.zeros((SIDE, SIDE), dtype=bool)

    for row_index in range(rows):
        base = SIDE / 2 - (y_m[row_index] + lift_m) * T / MPP
        row_height = height[row_index]
        lift = row_height * T / MPP
        top = np.round(base - lift).astype(np.int32)
        base_i = int(round(base))
        tree_cols = np.nonzero((crown_h[row_index] > 0) & col_ok)[0]
        if tree_cols.size:
            if 0 <= base_i < SIDE:
                canvas[base_i, sx[tree_cols]] = np.clip(photo[row_index, tree_cols], 0, 255).astype(np.uint8)
                filled[base_i, sx[tree_cols]] = True
            side = (crown_rgb[row_index, tree_cols] * 0.62).astype(np.uint8)
            tree_lift = lift[tree_cols]
            for k in range(int(tree_lift.min() * 0.62), int(tree_lift.max()) + 1):
                screen_y = base_i - k
                if screen_y < 0 or screen_y >= SIDE:
                    continue
                selected = (tree_lift >= k) & (k >= tree_lift * 0.62)
                if selected.any():
                    canvas[screen_y, sx[tree_cols[selected]]] = side[selected]
                    filled[screen_y, sx[tree_cols[selected]]] = True
        wall_cols = np.nonzero((row_height > 0) & col_ok & (ids[row_index] > 0))[0]
        if wall_cols.size:
            kmax = int(lift[wall_cols].max())
            for k in range(0, kmax + 1):
                screen_y = base_i - k
                if screen_y < 0 or screen_y >= SIDE:
                    continue
                selected_cols = wall_cols[lift[wall_cols] >= k]
                if selected_cols.size == 0:
                    continue
                metres = np.full(selected_cols.size, k * MPP / T, dtype=np.float32)
                colour = wall_pixels(
                    metres,
                    sx[selected_cols] + sheet_x0,
                    plaster_lut[ids[row_index, selected_cols]],
                    row_height[selected_cols],
                )
                # Shade against each wall's own height, never the tallest wall in the
                # row: the row reaches outside the window and differs between cells.
                shade = 0.9 + 0.1 * (k / np.maximum(1.0, lift[selected_cols]))
                canvas[screen_y, sx[selected_cols]] = np.clip(colour * shade[:, None], 0, 255).astype(np.uint8)
                filled[screen_y, sx[selected_cols]] = True
        ok = col_ok & (top >= 0) & (top < SIDE)
        idx = np.nonzero(ok)[0]
        canvas[top[idx], sx[idx]] = top_rgb[row_index, idx]
        filled[top[idx], sx[idx]] = True

    # Columns the orthophoto grid skipped: copy from the left neighbour.
    for column in range(1, SIDE):
        gap = ~filled[:, column] & filled[:, column - 1]
        canvas[gap, column] = canvas[gap, column - 1]
        filled[gap, column] = True
    for screen_y in range(1, SIDE):
        gap = ~filled[screen_y]
        canvas[screen_y, gap] = canvas[screen_y - 1, gap]
    return Image.fromarray(canvas)
