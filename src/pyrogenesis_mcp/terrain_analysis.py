"""LOS-safe mathematical summaries for an already projected map grid.

This module never reads the engine and never fills an unknown sample.  It only
derives local values from the height samples present in a ``player-los-map-v1``
response.  The result is deliberately an approximation: it describes sampled
terrain and cannot establish native placement legality, collision, territory,
or pathability.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right

from .map_observation import MAP_POLICY, REGION_KEYS, validate_region
from .privacy import ObservationError, number


ANALYSIS_POLICY = "observed-terrain-analysis-v1"
VISIBILITY_LEGEND = {
    "0": "off_map",
    "1": "unexplored",
    "2": "fogged",
    "3": "visible",
}
MAX_FOOTPRINT = 4096
MAX_FOOTPRINT_SAMPLE_WORK = 1_000_000


def _finite(value, *, low=-10**12, high=10**12, description="value"):
    try:
        return number(value, low=low, high=high)
    except ObservationError as error:
        raise ObservationError(f"Invalid {description}") from error


def _dimension(value, description: str) -> float:
    return _finite(value, low=1, high=MAX_FOOTPRINT, description=description)


def _terrain(cell: dict) -> dict | None:
    value = cell.get("terrain")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ObservationError("Invalid projected map terrain")
    height = _finite(value.get("height"), low=-100000, high=100000,
                     description="terrain height")
    water = value.get("water_level")
    if water is not None:
        water = _finite(water, low=-100000, high=100000,
                        description="terrain water level")
    last_seen = _finite(value.get("last_seen"), high=10**12,
                        description="terrain observation time")
    return {"height": height, "water_level": water, "last_seen": last_seen}


def _visibility(cell: dict) -> int:
    in_bounds = cell.get("in_bounds")
    explored = cell.get("explored")
    visible = cell.get("currently_visible")
    if type(in_bounds) is not bool or type(explored) is not bool or type(visible) is not bool:
        raise ObservationError("Invalid projected map visibility flags")
    if visible and not explored or not in_bounds and (visible or explored):
        raise ObservationError("Contradictory projected map visibility flags")
    if not in_bounds:
        return 0
    if not explored:
        return 1
    return 3 if visible else 2


def _derivative(left, center, right):
    """Derivative through three points, with one-sided fallback.

    ``left``/``right`` are ``(coordinate, height)`` pairs or ``None``.  The
    three-point formula is the derivative of the interpolating quadratic and
    therefore remains correct when a clipped edge creates unequal spacing.
    """
    coordinate, height = center
    if left is not None and right is not None:
        left_coordinate, left_height = left
        right_coordinate, right_height = right
        left_distance = coordinate - left_coordinate
        right_distance = right_coordinate - coordinate
        span = right_coordinate - left_coordinate
        if left_distance <= 0 or right_distance <= 0 or span <= 0:
            return None
        return (
            -right_distance * left_height / (left_distance * span)
            + (right_distance - left_distance) * height / (left_distance * right_distance)
            + left_distance * right_height / (span * right_distance)
        )
    if left is not None:
        distance = coordinate - left[0]
        return (height - left[1]) / distance if distance > 0 else None
    if right is not None:
        distance = right[0] - coordinate
        return (right[1] - height) / distance if distance > 0 else None
    return None


def _region_coverage(x, z, region, width, depth) -> float:
    half_width = width / 2
    half_depth = depth / 2
    x_overlap = max(0.0, min(x + half_width, region["x_max"]) -
                    max(x - half_width, region["x_min"]))
    z_overlap = max(0.0, min(z + half_depth, region["z_max"]) -
                    max(z - half_depth, region["z_min"]))
    area = width * depth
    return (x_overlap * z_overlap) / area if area > 0 else 0.0


def _footprint_metrics(index, columns, xs, zs, records, region, width, depth):
    center_x = xs[index % columns]
    center_z = zs[index // columns]
    half_width = width / 2
    half_depth = depth / 2
    x_min, x_max = center_x - half_width, center_x + half_width
    z_min, z_max = center_z - half_depth, center_z + half_depth
    first_column, last_column = bisect_left(xs, x_min), bisect_right(xs, x_max)
    first_row, last_row = bisect_left(zs, z_min), bisect_right(zs, z_max)
    samples = [records[row * columns + column]
               for row in range(first_row, last_row)
               for column in range(first_column, last_column)]
    heights = [item["height"] for item in samples if item["height"] is not None]
    expected = len(samples)
    known = len(heights)
    coverage = known / expected if expected else None
    height_min = min(heights) if heights else None
    height_max = max(heights) if heights else None
    height_range = height_max - height_min if heights else None
    diagonal = math.hypot(width, depth)
    return {
        "expected_samples": expected,
        "known_samples": known,
        "coverage": coverage,
        "region_coverage": _region_coverage(center_x, center_z, region, width, depth),
        "complete": bool(expected and known == expected and
                          _region_coverage(center_x, center_z, region, width, depth) == 1),
        "height_min": height_min,
        "height_max": height_max,
        "height_range": height_range,
        "height_range_ratio": height_range / diagonal if height_range is not None else None,
        "mean_height": sum(heights) / known if known else None,
    }


def analyze_terrain(map_data: dict, footprint_width: float, footprint_depth: float | None = None) -> dict:
    """Return compact, row-major terrain derivatives and footprint summaries.

    ``map_data`` must be the projected response from ``observe_map_region``.
    Heights are never inferred across null or off-map cells.  ``footprint``
    metrics describe the requested sampled rectangle around every grid center;
    ``complete`` requires the rectangle to fit inside the requested region and
    every grid-center sample in it to have a known height.
    """
    if not isinstance(map_data, dict) or map_data.get("policy") != MAP_POLICY:
        raise ObservationError("Terrain analysis requires a projected map response")
    width = _dimension(footprint_width, "footprint width")
    depth = width if footprint_depth is None else _dimension(footprint_depth, "footprint depth")
    region = map_data.get("region")
    if not isinstance(region, dict) or set(region) != set(REGION_KEYS):
        raise ObservationError("Invalid projected map region")
    raw_region = {
        "x_min": _finite(region["x_min"], high=100000, description="region x minimum"),
        "z_min": _finite(region["z_min"], high=100000, description="region z minimum"),
        "x_max": _finite(region["x_max"], high=100000, description="region x maximum"),
        "z_max": _finite(region["z_max"], high=100000, description="region z maximum"),
        "cell_size": _finite(region["cell_size"], low=4, high=256,
                               description="region cell size"),
    }
    map_size = max(raw_region["x_max"], raw_region["z_max"])
    region = validate_region(**raw_region, map_size=map_size)
    columns = map_data.get("columns")
    rows = map_data.get("rows")
    if type(columns) is not int or type(rows) is not int or columns <= 0 or rows <= 0:
        raise ObservationError("Invalid projected map dimensions")
    expected_columns = math.ceil((region["x_max"] - region["x_min"]) / region["cell_size"])
    expected_rows = math.ceil((region["z_max"] - region["z_min"]) / region["cell_size"])
    if columns != expected_columns or rows != expected_rows:
        raise ObservationError("Projected map dimensions do not match its region")
    cells = map_data.get("cells")
    if not isinstance(cells, list) or len(cells) != columns * rows:
        raise ObservationError("Invalid projected map cells")
    time = _finite(map_data.get("time"), high=10**12, description="map observation time")
    player = map_data.get("player")
    if type(player) is not int or not 1 <= player <= 8:
        raise ObservationError("Invalid projected map player")

    xs = []
    zs = []
    records = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise ObservationError("Invalid projected map cell")
        x = _finite(cell.get("x"), high=100000, description="map x coordinate")
        z = _finite(cell.get("z"), high=100000, description="map z coordinate")
        column, row = index % columns, index // columns
        x_start = region["x_min"] + column * region["cell_size"]
        z_start = region["z_min"] + row * region["cell_size"]
        expected_x = (x_start + min(x_start + region["cell_size"], region["x_max"])) / 2
        expected_z = (z_start + min(z_start + region["cell_size"], region["z_max"])) / 2
        if x != expected_x or z != expected_z:
            raise ObservationError("Projected map coordinate does not match its grid")
        if index % columns == 0:
            zs.append(z)
        elif z != zs[-1]:
            raise ObservationError("Projected map rows are not aligned")
        if index < columns:
            xs.append(x)
        elif x != xs[index % columns]:
            raise ObservationError("Projected map columns are not aligned")
        visibility = _visibility(cell)
        terrain = _terrain(cell) if visibility in (2, 3) else None
        if visibility == 3 and terrain is not None and terrain["last_seen"] != time:
            raise ObservationError("Visible projected map terrain has an invalid observation time")
        if terrain is not None and terrain["last_seen"] > time:
            raise ObservationError("Projected map terrain comes from a future observation")
        records.append({
            "x": x, "z": z, "visibility": visibility,
            "height": terrain["height"] if terrain is not None else None,
            "water_level": terrain["water_level"] if terrain is not None else None,
            "last_seen": terrain["last_seen"] if terrain is not None else None,
        })

    def known(index):
        record = records[index]
        return record["height"] is not None and record["visibility"] != 0

    slopes = []
    for index, record in enumerate(records):
        if not known(index):
            slopes.append(None)
            continue
        row, column = divmod(index, columns)
        center = (record["x"], record["height"])
        center_z = (record["z"], record["height"])
        left_pair = ((records[index - 1]["x"], records[index - 1]["height"])
                     if column and known(index - 1) else None)
        right_pair = ((records[index + 1]["x"], records[index + 1]["height"])
                      if column + 1 < columns and known(index + 1) else None)
        above_pair = ((records[index - columns]["z"], records[index - columns]["height"])
                      if row and known(index - columns) else None)
        below_pair = ((records[index + columns]["z"], records[index + columns]["height"])
                      if row + 1 < rows and known(index + columns) else None)
        gx = (_derivative(left_pair, center, right_pair)
              if column in (0, columns - 1) or left_pair is not None and right_pair is not None
              else None)
        gz = (_derivative(above_pair, center_z, below_pair)
              if row in (0, rows - 1) or above_pair is not None and below_pair is not None
              else None)
        if gx is None or gz is None:
            slopes.append(None)
            continue
        magnitude = math.hypot(gx, gz)
        slopes.append(math.degrees(math.atan(magnitude)))

    footprint = {key: [] for key in (
        "coverage", "region_coverage", "complete", "height_range", "mean_height",
    )}
    footprint_work = 0
    for index in range(len(records)):
        metrics = _footprint_metrics(index, columns, xs, zs, records,
                                     region, width, depth)
        footprint_work += metrics["expected_samples"]
        if footprint_work > MAX_FOOTPRINT_SAMPLE_WORK:
            raise ObservationError("Terrain footprint analysis exceeds the work limit")
        for key, value in metrics.items():
            if key in footprint:
                footprint[key].append(value)

    return {
        "policy": ANALYSIS_POLICY,
        "source_policy": MAP_POLICY,
        "assessment": "sampled-terrain-only",
        "approximate": True,
        "player": player,
        "time": time,
        "region": dict(region),
        "visibility_legend": dict(VISIBILITY_LEGEND),
        "grid": {
            "columns": columns,
            "rows": rows,
            "x_centers": xs,
            "z_centers": zs,
            "visibility": [record["visibility"] for record in records],
            "height": [record["height"] for record in records],
            "water_level": [record["water_level"] for record in records],
            "last_seen": [record["last_seen"] for record in records],
            "slope_degrees": slopes,
        },
        "footprint": {
            "width": width,
            "depth": depth,
            **footprint,
        },
    }


analyze_terrain_region = analyze_terrain
