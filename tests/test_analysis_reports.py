from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from pyrogenesis_analysis.reports import render_map, render_markdown, validate_output_path, write_reports


@pytest.fixture
def report():
    return {
        "source": {"file": "example.0adsave", "sha256": "abc", "engine_version": "0.28.0", "serialization_version": "0.28.0"},
        "scope": {"focus_player": 2, "fog_reconstructed": False},
        "match": {"map": "平原", "game_time_ms": 120000, "world_size": 512,
                  "visibility_mode": "fog_unexplored", "ceasefire_minutes": 45,
                  "estimated_ceasefire_remaining_ms": 2580000,
                  "ceasefire_actual": {"active": True, "remaining_ms": 2580000}},
        "players": [{"id": 2, "name": "测试", "civ": "ptol", "phase": "town",
                     "population": {"count": 3, "limit": 10, "max": 300},
                     "resources": {"food": 200, "wood": 100, "stone": None, "metal": 0},
                     "units": 3, "buildings": 1, "foundations": 1, "workers": 2,
                     "idle_worker_ids": [6], "researched_technologies": ["phase_town"],
                     "gatherers": {"food": 1, "wood": 0, "stone": 0, "metal": 0},
                     "research_queue": [], "busy_building_ids": [3]}],
        "entities": [{"id": 6, "owner": 2, "template": "units/ptol/worker",
                      "name": {"generic": "Worker"}, "classes": ["Unit", "Worker"],
                      "representation": "simulation", "position": {"x": 60, "z": 70}}],
        "rules_audit": {"matches": True, "mismatches": []},
        "findings": [{"player": 2, "message": "一名工人空闲", "evidence": {"entity_ids": [6]}}],
        "warnings": [],
    }


def test_markdown_keeps_snapshot_limits_and_saved_state(report):
    result = render_markdown(report)
    assert "全知离线快照" in result
    assert "没有还原玩家迷雾" in result
    assert "不代表地形" in result
    assert "ptol / 城镇" in result
    assert "3 / 10 / 300" in result
    assert "200 / 100 / 未知 / 0" in result
    assert "空闲 Worker ID：6" in result
    assert "存档停战状态：True；剩余：43:00" in result
    assert "entity_ids" in result


def test_report_injection_stays_inert_and_json_round_trips(report):
    malicious = '</script><img src=x onerror="alert(1)">\u2028&<b>|\nunsafe'
    report["players"][0]["name"] = malicious
    report["entities"][0]["name"]["generic"] = malicious
    rendered = render_map(report)
    assert malicious not in rendered
    assert "<img" not in rendered
    payload = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', rendered, re.S).group(1)
    assert json.loads(payload) == report
    assert "innerHTML" not in rendered
    assert "textContent" in rendered
    assert "https://" not in rendered and "http://" not in rendered.replace("http://www.w3.org/2000/svg", "")
    markdown = render_markdown(report)
    assert "<img" not in markdown and "&lt;img" in markdown
    assert "\\|" in markdown


def test_map_has_independent_filters_and_hidden_extras(report):
    rendered = render_map(report)
    for control in ('id="owner"', 'id="category"', 'id="search"', 'id="extras"', 'id="focus"', 'id="fit"'):
        assert control in rendered
    assert "mirage|" in rendered and "actor|" in rendered
    assert "addEventListener('click'" in rendered
    assert "addEventListener('keydown'" in rendered
    assert "JSON.stringify(e,null,2)" in rendered
    assert 'src="' not in rendered and 'href="' not in rendered


def test_write_new_reports_does_not_change_source(report, tmp_path):
    source = tmp_path / "save.0adsave"
    source.write_bytes(b"original archive")
    target = write_reports(report, tmp_path / "reports" / "case", source)
    assert source.read_bytes() == b"original archive"
    assert {path.name for path in target.iterdir()} == {"report.json", "report.md", "map.html"}
    assert json.loads((target / "report.json").read_text(encoding="utf-8")) == report
    with pytest.raises(ValueError, match="already exists"):
        write_reports(report, target, source)
    assert source.read_bytes() == b"original archive"


def test_reject_containing_or_replacing_save(tmp_path):
    source = tmp_path / "save.0adsave"
    with pytest.raises(ValueError, match="contain or replace"):
        validate_output_path(source, source)
    with pytest.raises(ValueError, match="contain or replace"):
        validate_output_path(tmp_path, source)


def test_failed_render_creates_no_output(report, tmp_path):
    report["invalid"] = float("nan")
    with pytest.raises(ValueError):
        write_reports(report, tmp_path / "result", tmp_path / "source")
    assert not (tmp_path / "result").exists()


def test_write_failure_removes_partial_report(report, tmp_path, monkeypatch):
    original = Path.open

    def failing_open(path, *args, **kwargs):
        if path.name == "map.html":
            raise OSError("simulated disk error")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    with pytest.raises(OSError, match="simulated disk error"):
        write_reports(report, tmp_path / "result", tmp_path / "source")
    assert not (tmp_path / "result").exists()
