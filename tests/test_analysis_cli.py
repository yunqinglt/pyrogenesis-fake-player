from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pyrogenesis_analysis import cli
from pyrogenesis_analysis.analysis import DEFAULT_RULES


def write_rules(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_rules_roundtrip(tmp_path):
    rules = dict(DEFAULT_RULES, recorded_on="2026-11-06")
    assert cli.load_rules(write_rules(tmp_path / "rules.json", rules)) == rules


@pytest.mark.parametrize("key,value", [
    ("schema_version", True), ("schema_version", 2),
    ("opponents", []), ("visibility_modes", "revealed"),
    ("visibility_modes", ["cheat"]), ("visibility_modes", []),
    ("ceasefire_minutes", True), ("ceasefire_minutes", -1),
    ("ceasefire_minutes", float("nan")), ("ceasefire_minutes", float("inf")),
    ("victory_conditions", "conquest_units"), ("victory_conditions", [None]),
    ("victory_unit_expression", "Structure"),
])
def test_invalid_rule_types_fail_clearly(tmp_path, key, value):
    rules = dict(DEFAULT_RULES, **{key: value})
    with pytest.raises(ValueError):
        cli.load_rules(write_rules(tmp_path / "rules.json", rules))


def test_missing_fields_and_duplicate_rules_rejected(tmp_path):
    path = write_rules(tmp_path / "rules.json", {})
    with pytest.raises(ValueError, match="Missing rules field"):
        cli.load_rules(path)
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate rules key"):
        cli.load_rules(path)


def test_cli_wires_readonly_pipeline(tmp_path, monkeypatch, capsys):
    source = tmp_path / "sample.0adsave"
    source.write_bytes(b"unchanged")
    output = tmp_path / "result"
    archive = SimpleNamespace(path=source, metadata={"engine_serialization_version": "0.28.0"}, simulation=b"snapshot")
    calls = {}

    class Catalog:
        def __init__(self, sources, *, expected_version):
            calls["sources"] = sources
            calls["version"] = expected_version

        def __enter__(self):
            return self

        def __exit__(self, *_):
            calls["closed"] = True

        def obstruction_type(self, name):
            return "unit"

    def decode(data, version, *, obstruction_types):
        assert data == b"snapshot" and version == "0.28.0"
        assert obstruction_types("anything") == "unit"
        return {"complete": True}

    def analyze(found_archive, decoded, *, catalog, focus_player, rules):
        assert found_archive is archive and decoded == {"complete": True}
        assert isinstance(catalog, Catalog)
        calls["focus"] = focus_player
        return {"scope": {"focus_player": focus_player}, "players": [], "entities": []}

    monkeypatch.setattr(cli, "read_save", lambda path: archive)
    monkeypatch.setattr(cli, "TemplateCatalog", Catalog)
    monkeypatch.setattr(cli, "decode_simulation", decode)
    monkeypatch.setattr(cli, "analyze_save", analyze)
    assert cli.main([str(source), "--output", str(output), "--player", "2", "--templates", "a.zip", "--templates", "b"]) == 0
    assert calls["focus"] == 2 and calls["closed"] is True
    assert [str(path) for path in calls["sources"]] == ["a.zip", "b"]
    assert json.loads((output / "report.json").read_text(encoding="utf-8"))["scope"]["focus_player"] == 2
    assert source.read_bytes() == b"unchanged"
    assert "已生成离线分析" in capsys.readouterr().out


def test_existing_output_rejected_before_save_read(tmp_path, monkeypatch, capsys):
    output = tmp_path / "result"
    output.mkdir()
    monkeypatch.setattr(cli, "read_save", lambda _: pytest.fail("Must reject output first"))
    assert cli.main([str(tmp_path / "source"), "-o", str(output)]) == 1
    assert "already exists" in capsys.readouterr().err


def test_decode_failure_leaves_no_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_rules", lambda _: dict(DEFAULT_RULES))
    monkeypatch.setattr(cli, "read_save", lambda _: (_ for _ in ()).throw(ValueError("unsupported component")))
    assert cli.main([str(tmp_path / "source"), "-o", str(tmp_path / "result")]) == 1
    assert not (tmp_path / "result").exists()
    assert "unsupported component" in capsys.readouterr().err


def test_invalid_rules_produce_error_without_traceback(tmp_path, capsys):
    rules = write_rules(tmp_path / "rules.json", {"schema_version": 1})
    assert cli.main([str(tmp_path / "source"), "-o", str(tmp_path / "result"), "--rules", str(rules)]) == 1
    error = capsys.readouterr().err
    assert "Missing rules field" in error and "Traceback" not in error
    assert not (tmp_path / "result").exists()
