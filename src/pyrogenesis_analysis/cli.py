"""Command line entry point for the standalone, read-only save analyzer."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from .analysis import DEFAULT_RULES, analyze_save
from .archive import read_save
from .reports import validate_output_path, write_reports
from .serialization import decode_simulation
from .templates import TemplateCatalog


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate rules key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Non-finite rules number: {value}")


def load_rules(path: str | Path | None = None) -> dict:
    """Load a bounded rules object, validating all fields used by analysis."""
    project_default = Path(__file__).resolve().parents[2] / "analysis_rules.json"
    source = Path(path).expanduser().resolve() if path is not None else project_default
    if path is None and not source.is_file():
        return dict(DEFAULT_RULES)
    with source.open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("Rules file exceeds 64 KiB")
    try:
        rules = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"Invalid rules JSON: {exc}") from exc
    if not isinstance(rules, dict):
        raise ValueError("Rules must be a JSON object")
    for key in DEFAULT_RULES:
        if key not in rules:
            raise ValueError(f"Missing rules field: {key}")
    if type(rules["schema_version"]) is not int or rules["schema_version"] != 1:
        raise ValueError("Rules schema_version must be 1")
    if rules["opponents"] not in ("human_vs_ai", "human_vs_human", "other"):
        raise ValueError("Rules opponents must be human_vs_ai, human_vs_human, or other")
    modes = rules["visibility_modes"]
    if not isinstance(modes, list) or not modes or any(
            not isinstance(mode, str) or mode not in ("fog_unexplored", "fog_explored", "revealed") for mode in modes):
        raise ValueError("Rules visibility_modes must be a nonempty list of supported modes")
    minutes = rules["ceasefire_minutes"]
    if type(minutes) not in (int, float) or minutes < 0 or (
            type(minutes) is float and not math.isfinite(minutes)):
        raise ValueError("Rules ceasefire_minutes must be a finite, nonnegative number")
    victories = rules["victory_conditions"]
    if not isinstance(victories, list) or any(not isinstance(item, str) or not item for item in victories):
        raise ValueError("Rules victory_conditions must be a list of nonempty strings")
    if rules["victory_unit_expression"] != "Unit+!Animal":
        raise ValueError("Only the Unit+!Animal victory_unit_expression is supported")
    return rules


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="0 A.D. 0.28.0 存档离线分析（全知快照，不连接 MCP）")
    parser.add_argument("save", type=Path, help="原始 .0adsave 存档，只读")
    parser.add_argument("--output", "-o", type=Path, required=True, help="必须是尚不存在的新输出目录")
    parser.add_argument("--player", type=int, help="焦点玩家 ID；不还原迷雾，不限制原始数据")
    parser.add_argument("--templates", type=Path, action="append", help="模板目录或 ZIP，可多次提供，低优先级在前")
    parser.add_argument("--rules", type=Path, help="案例规则 JSON，默认项目 analysis_rules.json 或内置规则")
    args = parser.parse_args(argv)
    try:
        output = validate_output_path(args.output, args.save)
        rules = load_rules(args.rules)
        archive = read_save(args.save)
        version = archive.metadata.get("engine_serialization_version")
        if version != "0.28.0":
            raise ValueError(f"Unsupported serialization version {version!r}; expected 0.28.0")
        with TemplateCatalog(args.templates, expected_version=version) as catalog:
            decoded = decode_simulation(archive.simulation, version=version,
                                        obstruction_types=catalog.obstruction_type)
            report = analyze_save(archive, decoded, catalog=catalog,
                                  focus_player=args.player, rules=rules)
            write_reports(report, output, archive.path)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        print(f"分析失败：{exc}", file=sys.stderr)
        return 1
    print(f"已生成离线分析：{output}")
    print("report.json · report.md · map.html（全知快照；对象图不代表地形或玩家视野）")
    return 0
