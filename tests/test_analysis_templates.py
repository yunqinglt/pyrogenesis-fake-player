from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from pyrogenesis_analysis.templates import TemplateCatalog, TemplateError, TemplateNotFound


def write_template(root, name, xml):
    target = root / "simulation/templates" / f"{name}.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(xml, encoding="utf-8")


@pytest.fixture
def rules(tmp_path):
    (tmp_path / "mod.json").write_text(json.dumps({"version": "0.28.0"}), encoding="utf-8")
    write_template(tmp_path, "base", """<Entity>
      <Identity><GenericName>Unit</GenericName><Classes datatype="tokens">Unit Organic</Classes></Identity>
      <Cost><Population>1</Population><BuildTime>10</BuildTime><Resources><food>50</food></Resources></Cost>
      <Health><Max>100</Max></Health><Obstruction><Unit/></Obstruction><UnitAI/>
    </Entity>""")
    write_template(tmp_path, "mixins/worker", """<Entity>
      <Identity><VisibleClasses datatype="tokens">Worker</VisibleClasses></Identity><Builder/>
    </Entity>""")
    write_template(tmp_path, "units/test", """<Entity parent="worker|base">
      <Identity><GenericName>Worker</GenericName><SpecificName>Native</SpecificName><Civ>test</Civ>
      <Classes datatype="tokens">-Organic Citizen</Classes></Identity>
      <Health><Max op="mul">1.5</Max></Health>
      <Cost><Resources><wood>20</wood></Resources></Cost>
    </Entity>""")
    return tmp_path


def test_inherited_identity_cost_and_immutable_cache(rules):
    with TemplateCatalog([rules], expected_version="0.28.0") as catalog:
        data = catalog.describe("units/test")
        assert data["name"] == {"generic": "Worker", "specific": "Native"}
        assert data["classes"] == ["Unit", "Citizen", "Worker"]
        assert data["cost"] == {"food": 50, "wood": 20, "stone": None,
                                "metal": None, "population": 1, "time": 10}
        assert data["max_hitpoints"] == 150
        assert data["basis"] == "unmodified-template"
        assert data["translation"] == "source-language"
        assert data["source_versions"] == ["0.28.0"]
        assert catalog.obstruction_type("units/test") == "unit"
        catalog.resolve("units/test").find("Identity/GenericName").text = "Mutated"
        assert catalog.describe("units/test")["name"]["generic"] == "Worker"


def test_filter_disable_replace_and_merge(rules):
    write_template(rules, "special/filter/foundation", """<Entity filtered="">
      <Identity merge=""><Classes datatype="tokens">Foundation</Classes></Identity>
      <Health merge=""><Max op="add">10</Max></Health>
      <Missing merge=""/><Foundation/><Obstruction><Unit disable=""/><Static width="5" depth="6"/></Obstruction>
    </Entity>""")
    with TemplateCatalog([rules]) as catalog:
        entity = catalog.resolve("foundation|units/test")
        assert entity.find("UnitAI") is None
        assert entity.find("Missing") is None
        assert entity.find("Foundation") is not None
        assert entity.findtext("Health/Max") == "160"
        assert "Foundation" in catalog.describe("foundation|units/test")["classes"]
        assert catalog.obstruction_type("foundation|units/test") == "static"
    write_template(rules, "replace", """<Entity parent="base">
      <Identity replace=""><Classes datatype="tokens">Structure</Classes></Identity>
      <Obstruction replace=""><Obstructions><Main width="4" depth="5"/></Obstructions></Obstruction>
    </Entity>""")
    with TemplateCatalog([rules]) as catalog:
        data = catalog.describe("replace")
        assert data["classes"] == ["Structure"]
        assert data["name"]["generic"] is None
        assert catalog.obstruction_type("replace") == "cluster"


def test_zip_and_mod_precedence(rules, tmp_path):
    packed = tmp_path / "templates.zip"
    with zipfile.ZipFile(packed, "w") as archive:
        for path in rules.rglob("*.xml"):
            archive.write(path, path.relative_to(rules).as_posix())
        archive.writestr("mod.json", '{"version":"0.28.0"}')
    overlay = tmp_path / "override"
    write_template(overlay, "units/test", '<Entity parent="base"><Identity><GenericName>Override</GenericName></Identity></Entity>')
    with TemplateCatalog([packed, overlay]) as catalog:
        data = catalog.describe("units/test")
        assert data["name"]["generic"] == "Override"
        assert data["max_hitpoints"] == 100
        assert data["source_layers"][-1]["source"] == str(overlay.resolve())


def test_template_root_and_actor(rules):
    write_template(rules, "special/actor", "<Entity><Position/><VisualActor/></Entity>")
    with TemplateCatalog([rules / "simulation/templates"]) as catalog:
        assert catalog.describe("base")["name"]["generic"] == "Unit"
        assert catalog.resolve("actor|props/test.xml").findtext("VisualActor/Actor") == "props/test.xml"
        assert catalog.obstruction_type("actor|props/test.xml") is None


@pytest.mark.parametrize("name", ["../secret", "units/../../x", "C:/secret", "units\\test", "base||test",
                                  "actor|../../secret.xml", "base.xml", "", None])
def test_invalid_identifiers_fail_closed(rules, name):
    with TemplateCatalog([rules]) as catalog:
        with pytest.raises(TemplateError):
            catalog.resolve(name)


def test_cycles_unknown_ops_and_missing_templates(rules):
    write_template(rules, "a", '<Entity parent="b"/>')
    write_template(rules, "b", '<Entity parent="a"/>')
    write_template(rules, "badop", '<Entity parent="base"><Health><Max op="pow">2</Max></Health></Entity>')
    with TemplateCatalog([rules]) as catalog:
        with pytest.raises(TemplateError, match="cyclic"):
            catalog.resolve("a")
        with pytest.raises(TemplateError, match="Unsupported template operation"):
            catalog.resolve("badop")
        with pytest.raises(TemplateNotFound):
            catalog.resolve("not_found")


@pytest.mark.parametrize("xml", [
    '<!DOCTYPE Entity [<!ENTITY data "secret">]><Entity><Identity>&data;</Identity></Entity>',
    '<Entity><Health></Entity>',
    '<Other/>',
    '<Entity xmlns="urn:unknown"/>',
])
def test_untrusted_xml_rejected(rules, xml):
    write_template(rules, "bad", xml)
    with TemplateCatalog([rules]) as catalog:
        with pytest.raises(TemplateError):
            catalog.resolve("bad")


def test_version_mismatch_and_bad_archive(rules, tmp_path):
    with pytest.raises(TemplateError, match="does not match"):
        TemplateCatalog([rules], expected_version="0.27.0")
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip")
    with pytest.raises(TemplateError, match="Cannot read template archive"):
        TemplateCatalog([archive])


def test_auto_discovery_falls_back_without_merging_versions(rules, tmp_path, monkeypatch):
    from pyrogenesis_analysis import templates

    incompatible = tmp_path / "old"
    incompatible.mkdir()
    (incompatible / "mod.json").write_text('{"version":"0.27.0"}', encoding="utf-8")
    monkeypatch.setattr(templates, "discover_public_sources", lambda: [incompatible, rules])
    with TemplateCatalog(expected_version="0.28.0") as catalog:
        assert catalog.describe("base")["name"]["generic"] == "Unit"
        assert len(catalog.discovery_notes) == 1
        assert len(catalog.sources) == 1


def test_fixed_point_chained_arithmetic(rules):
    write_template(rules, "fraction", '<Entity parent="base"><Health><Max op="mul">0.1</Max></Health></Entity>')
    write_template(rules, "chained", '<Entity parent="fraction"><Health><Max op="mul">0.1</Max></Health></Entity>')
    write_template(rules, "rounded", '<Entity parent="base"><Health><Max op="mul_round">0.125</Max></Health></Entity>')
    with TemplateCatalog([rules]) as catalog:
        # Native 16.16 FromString(0.1) is 6553; integer/fraction operations
        # retain that truncation instead of assuming exact decimal arithmetic.
        assert catalog.describe("fraction")["max_hitpoints"] == 9.99909
        assert catalog.describe("chained")["max_hitpoints"] == 0.99982
        assert catalog.describe("rounded")["max_hitpoints"] == 13
