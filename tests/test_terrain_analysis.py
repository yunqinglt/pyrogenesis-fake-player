"""Terrain derivatives use observed world coordinates and preserve unknowns."""

import math

import pytest

from pyrogenesis_mcp.map_observation import MAP_POLICY, project_map_region
from pyrogenesis_mcp.privacy import ObservationError
from pyrogenesis_mcp.terrain_analysis import ANALYSIS_POLICY, analyze_terrain


def projected(*, x_max=20, z_min=0, z_max=20, cell_size=8, height=lambda x, z: 0,
              missing=(), off_map=()):
    region = {"x_min": 0, "z_min": z_min, "x_max": x_max, "z_max": z_max,
              "cell_size": cell_size}
    columns = math.ceil(x_max / cell_size)
    rows = math.ceil((z_max - z_min) / cell_size)
    cells = []
    for row in range(rows):
        z0 = z_min + row * cell_size
        z = (z0 + min(z_max, z0 + cell_size)) / 2
        for column in range(columns):
            x0 = column * cell_size
            x = (x0 + min(x_max, x0 + cell_size)) / 2
            coords = (x, z)
            in_bounds = coords not in off_map
            explored = in_bounds and coords not in missing
            terrain = ({"height": height(x, z), "water_level": 0, "last_seen": 1000}
                       if explored else None)
            cells.append({"x": x, "z": z, "in_bounds": in_bounds,
                          "explored": explored, "currently_visible": explored,
                          "terrain": terrain})
    raw = {"policy": MAP_POLICY, "player": 1, "time": 1000, "region": region,
           "columns": columns, "rows": rows, "cells": cells,
           "objects": [], "truncated": False}
    return project_map_region(raw, region, 1, max(x_max, z_max))


def test_plane_has_coordinate_aware_slope():
    source = projected(x_max=32, z_min=40, z_max=64, cell_size=8,
                       height=lambda x, z: 2 * x + 3 * z + 7)
    result = analyze_terrain(source, 8, 8)
    grid = result["grid"]
    slope = math.degrees(math.atan(math.sqrt(2**2 + 3**2)))
    assert result["policy"] == ANALYSIS_POLICY
    assert result["assessment"] == "sampled-terrain-only"
    assert result["region"]["cell_size"] == 8
    assert grid["x_centers"] == [4, 12, 20, 28]
    assert grid["z_centers"] == [44, 52, 60]
    assert grid["height"] == [2 * x + 3 * z + 7 for z in grid["z_centers"] for x in grid["x_centers"]]
    assert all(value == pytest.approx(slope) for value in grid["slope_degrees"])


def test_clipped_final_cell_uses_its_real_spacing_for_quadratic_derivative():
    source = projected(height=lambda x, z: x * x)
    result = analyze_terrain(source, 8)
    grid = result["grid"]
    middle_column = 1
    middle_row = 1
    index = middle_row * grid["columns"] + middle_column
    assert grid["x_centers"] == [4, 12, 18]
    # Slope is finite-difference based and uses the clipped 6-unit edge.
    assert grid["slope_degrees"][index] == pytest.approx(math.degrees(math.atan(24)))


def test_unknown_gap_does_not_create_a_gradient_or_footprint_confidence():
    source = projected(x_max=32, z_max=32, cell_size=8,
                       height=lambda x, z: x + z, missing={(12, 12)})
    result = analyze_terrain(source, 16, 16)
    grid = result["grid"]
    gap = grid["z_centers"].index(12) * grid["columns"] + grid["x_centers"].index(12)
    assert grid["visibility"][gap] == 1
    assert grid["height"][gap] is None
    assert grid["slope_degrees"][gap] is None
    # The cell immediately beyond the gap has no central x difference.
    beyond = gap + 1
    assert grid["slope_degrees"][beyond] is None
    footprint = result["footprint"]
    assert footprint["coverage"][gap] == pytest.approx(8 / 9)
    assert footprint["height_range"][gap] == 32
    assert footprint["complete"][gap] is False


def test_fogged_height_retains_last_seen_and_off_map_is_distinct():
    region = {"x_min": 0, "z_min": 0, "x_max": 16, "z_max": 8, "cell_size": 8}
    raw = {
        "policy": MAP_POLICY, "player": 1, "time": 1000, "region": region,
        "columns": 2, "rows": 1,
        "cells": [
            {"x": 4, "z": 4, "in_bounds": True, "explored": True,
             "currently_visible": False,
             "terrain": {"height": 5, "water_level": None, "last_seen": 700}},
            {"x": 12, "z": 4, "in_bounds": False, "explored": False,
             "currently_visible": False, "terrain": None},
        ], "objects": [], "truncated": False,
    }
    result = analyze_terrain(project_map_region(raw, region, 1, 16), 8)
    assert result["visibility_legend"] == {
        "0": "off_map", "1": "unexplored", "2": "fogged", "3": "visible"}
    assert result["grid"]["visibility"] == [2, 0]
    assert result["grid"]["height"] == [5, None]
    assert result["grid"]["last_seen"] == [700, None]
    assert result["grid"]["water_level"] == [None, None]


@pytest.mark.parametrize("last_seen", [999, 1001])
def test_visible_height_must_match_map_time(last_seen):
    source = projected()
    source["cells"][0]["terrain"]["last_seen"] = last_seen
    with pytest.raises(ObservationError, match="observation time"):
        analyze_terrain(source, 8)


def test_visible_sample_with_unavailable_height_stays_unknown():
    source = projected()
    source["cells"][0]["terrain"] = None
    result = analyze_terrain(source, 8)
    assert result["grid"]["visibility"][0] == 3
    assert result["grid"]["height"][0] is None
    assert result["grid"]["slope_degrees"][0] is None


def test_footprint_at_region_edge_is_incomplete_even_with_known_samples():
    result = analyze_terrain(projected(), 16, 16)
    footprint = result["footprint"]
    assert footprint["region_coverage"][0] == pytest.approx(0.5625)
    assert footprint["complete"][0] is False


@pytest.mark.parametrize("width,depth", [(True, 8), (0, 8), (4097, 8), (8, float("nan"))])
def test_invalid_footprint_dimensions_are_rejected(width, depth):
    with pytest.raises(ObservationError):
        analyze_terrain(projected(), width, depth)


def test_bad_or_misaligned_map_input_is_rejected():
    source = projected()
    source["cells"][0]["x"] += 1
    with pytest.raises(ObservationError, match="coordinate"):
        analyze_terrain(source, 8)


def test_analysis_result_is_compact_and_contains_no_objects():
    result = analyze_terrain(projected(), 8, 12)
    assert "cells" not in result and "objects" not in result
    for key, values in result["grid"].items():
        if key not in ("columns", "rows", "x_centers", "z_centers"):
            assert len(values) == result["grid"]["columns"] * result["grid"]["rows"]
    for values in result["footprint"].values():
        if isinstance(values, list):
            assert len(values) == result["grid"]["columns"] * result["grid"]["rows"]
