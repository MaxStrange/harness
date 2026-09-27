from __future__ import annotations

import json

import pytest

from harness import kicad
from harness.config import Config
from harness.model.types import ToolCall
from harness.skills.base import Services, SkillContext
from harness.skills.registry import SkillRegistry
from harness.skills.runner import SkillRunner, auto_approve_broker

ROOT_SCH = """(kicad_sch (version 20231120) (generator "eeschema")
  (uuid "a") (paper "A4")
  (lib_symbols (symbol "Device:R" (pin_numbers hide) (property "Reference" "R" (at 0 0 0))))
  (symbol (lib_id "Device:R") (at 100 50 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "R1" (at 0 0 0)) (property "Value" "10k" (at 0 0 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0)) (pin "1" (uuid "p1")))
  (symbol (lib_id "Device:R") (at 120 50 0) (unit 1)
    (property "Reference" "R2" (at 0 0 0)) (property "Value" "10k" (at 0 0 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0)))
  (symbol (lib_id "Device:C") (at 140 50 0) (unit 1) (dnp yes)
    (property "Reference" "C1" (at 0 0 0)) (property "Value" "100n" (at 0 0 0))
    (property "Footprint" "Capacitor_SMD:C_0603_1608Metric" (at 0 0 0)))
  (symbol (lib_id "power:GND") (at 100 80 0) (unit 1)
    (property "Reference" "#PWR01" (at 0 0 0)) (property "Value" "GND" (at 0 0 0)))
  (wire (pts (xy 1 1) (xy 2 2))) (wire (pts (xy 2 2) (xy 3 3)))
  (junction (at 2 2)) (no_connect (at 9 9))
  (label "SDA" (at 1 1 0)) (global_label "VBUS" (at 1 1 0)) (hierarchical_label "EN" (at 1 1 0))
  (sheet (at 10 10) (size 20 20) (property "Sheetname" "Power" (at 0 0 0)) (property "Sheetfile" "power.kicad_sch" (at 0 0 0)))
)"""

SUB_SCH = """(kicad_sch (version 20231120)
  (symbol (lib_id "Regulator_Linear:AMS1117-3.3") (at 10 10 0) (unit 1)
    (property "Reference" "U1" (at 0 0 0)) (property "Value" "AMS1117-3.3" (at 0 0 0))
    (property "Footprint" "Package_TO_SOT_SMD:SOT-223-3_TabPin2" (at 0 0 0)))
)"""

PCB = """(kicad_pcb (version 20240108) (generator "pcbnew")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (net 0 "") (net 1 "GND") (net 2 "VBUS") (net 3 "SDA")
  (footprint "Resistor_SMD:R_0603_1608Metric" (layer "F.Cu") (uuid "f1") (at 10 20 90)
    (property "Reference" "R1" (at 0 0 0)) (property "Value" "10k" (at 0 0 0))
    (pad "1" smd roundrect (at -0.8 0) (size 1 1) (layers "F.Cu") (net 1 "GND"))
    (pad "2" smd roundrect (at 0.8 0) (size 1 1) (layers "F.Cu") (net 3 "SDA")))
  (footprint "Resistor_SMD:R_0603_1608Metric" (layer "B.Cu") (uuid "f2") (at 15 20 0)
    (property "Reference" "R2" (at 0 0 0)) (property "Value" "10k" (at 0 0 0))
    (pad "1" smd roundrect (at -0.8 0) (size 1 1) (layers "B.Cu") (net 2 "VBUS"))
    (pad "2" smd roundrect (at 0.8 0) (size 1 1) (layers "B.Cu") (net 3 "SDA")))
  (segment (start 10 20) (end 15 20) (width 0.25) (layer "F.Cu") (net 3))
  (via (at 12 20) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 3))
  (gr_rect (start 0 0) (end 50 30) (layer "Edge.Cuts") (width 0.1))
  (zone (net 1) (net_name "GND") (layer "B.Cu") (polygon (pts (xy 0 0))))
)"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "widget.kicad_pro").write_text(
        json.dumps({"meta": {"filename": "widget.kicad_pro"}, "text_variables": {"REV": "A"}})
    )
    (tmp_path / "widget.kicad_sch").write_text(ROOT_SCH)
    (tmp_path / "power.kicad_sch").write_text(SUB_SCH)
    (tmp_path / "widget.kicad_pcb").write_text(PCB)
    return tmp_path


def test_sexpr_parser_handles_strings_numbers_and_errors():
    node = kicad.parse_sexpr('(a "quoted \\"x\\"" 1 -2.5 sym (b (c 3e2)))')
    assert node == ["a", 'quoted "x"', 1, -2.5, "sym", ["b", ["c", 300.0]]]
    with pytest.raises(kicad.SExprError):
        kicad.parse_sexpr("(a (b)")
    with pytest.raises(kicad.SExprError):
        kicad.parse_sexpr("")


def test_schematic_hierarchy_labels_and_bom(project):
    sch = kicad.load_schematic(project / "widget.kicad_sch")
    assert sch.version == 20231120 and sch.errors == []
    refs = {c.reference: c for c in sch.components}
    assert set(refs) == {"R1", "R2", "C1", "#PWR01", "U1"}
    assert refs["U1"].sheet == "/Power" and refs["R1"].sheet == "/"
    assert refs["C1"].dnp and refs["#PWR01"].is_power
    assert sch.labels == {"label": ["SDA"], "global_label": ["VBUS"], "hierarchical_label": ["EN"]}
    assert (sch.wires, sch.junctions, sch.no_connects) == (2, 1, 1)
    assert [s.name for s in sch.sheets] == ["Power"]
    bom = kicad.bill_of_materials(sch)
    assert bom == [
        {
            "value": "10k",
            "footprint": "Resistor_SMD:R_0603_1608Metric",
            "quantity": 2,
            "references": ["R1", "R2"],
        },
        {
            "value": "AMS1117-3.3",
            "footprint": "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
            "quantity": 1,
            "references": ["U1"],
        },
    ]


def test_board_and_nets(project):
    board = kicad.load_pcb(project / "widget.kicad_pcb")
    assert board.errors == [] and board.layers == ["F.Cu", "B.Cu", "Edge.Cuts"]
    assert board.thickness == 1.6 and board.size_mm == (50.0, 30.0)
    assert [f.reference for f in board.footprints] == ["R1", "R2"]
    assert board.footprints[0].at == (10.0, 20.0, 90.0) and board.footprints[1].layer == "B.Cu"
    assert (board.tracks, board.vias) == (1, 1) and board.zones == [("GND", "B.Cu")]
    assert kicad.board_nets(board) == {"GND": ["R1.1"], "SDA": ["R1.2", "R2.2"], "VBUS": ["R2.1"]}


def test_find_project_from_any_file(project):
    for start in (
        project,
        project / "widget.kicad_pro",
        project / "power.kicad_sch",
        project / "widget.kicad_pcb",
    ):
        found = kicad.find_project(start)
        assert found.project_file == str(project / "widget.kicad_pro")
        assert found.schematic == str(project / "widget.kicad_sch")
        assert found.pcb == str(project / "widget.kicad_pcb")
    assert found.text_variables == {"REV": "A"} and found.other_schematics == [
        str(project / "power.kicad_sch")
    ]


def test_skill_actions(project):
    registry = SkillRegistry()
    registry.load_builtin()
    runner = SkillRunner(registry, auto_approve_broker())
    ctx = SkillContext(cwd=project, config=Config(), services=Services())

    def run(**args):
        out = runner.execute(ToolCall("c", "kicad_inspect", args), ctx)
        assert out.result.ok, out.result.content
        return out.result

    overview = run(path=".", action="overview")
    assert (
        "4 components, 1 power symbols" in overview.content and "50.0 x 30.0 mm" in overview.content
    )
    assert overview.handoff.action == "default_app" and overview.handoff.target.endswith(
        "widget.kicad_pro"
    )
    schematic = run(path="widget.kicad_pro", action="schematic", filter="10k")
    assert "R1" in schematic.content and "R2" in schematic.content and "C1" not in schematic.content
    bom = run(path=".", action="bom")
    assert "R1, R2" in bom.content and "C1" not in bom.content and "U1" in bom.content
    pcb = run(path="widget.kicad_pcb", action="pcb", format="json")
    data = json.loads(pcb.content)
    assert data["size_mm"] == [50.0, 30.0] and len(data["footprints"]) == 2
    nets = run(path=".", action="nets", filter="sda")
    assert nets.content.strip().endswith("SDA: R1.2, R2.2")
    missing = runner.execute(ToolCall("c", "kicad_inspect", {"path": "nope", "action": "bom"}), ctx)
    assert not missing.result.ok and "does not exist" in missing.result.content


REUSED_ROOT = """(kicad_sch (version 20250114) (generator "eeschema") (generator_version "9.0") (uuid "root-uuid")
  (sheet (at 0 0) (size 10 10) (uuid "sheet-a") (property "Sheetname" "left" (at 0 0 0)) (property "Sheetfile" "amp.kicad_sch" (at 0 0 0)))
  (sheet (at 20 0) (size 10 10) (uuid "sheet-b") (property "Sheetname" "right" (at 0 0 0)) (property "Sheetfile" "amp.kicad_sch" (at 0 0 0)))
  (label "VPP{slash}MCLR" (at 1 1 0))
)"""

REUSED_AMP = """(kicad_sch (version 20250114) (uuid "amp-uuid")
  (symbol (lib_id "Device:R") (at 1 1 0) (unit 1)
    (property "Reference" "R201" (at 0 0 0)) (property "Value" "1k" (at 0 0 0)) (property "Footprint" "R_0603" (at 0 0 0))
    (instances (project "reused"
      (path "/root-uuid/sheet-a" (reference "R201") (unit 1))
      (path "/root-uuid/sheet-b" (reference "R301") (unit 1)))))
)"""

TEARDROP_PCB = """(kicad_pcb (version 20241229) (generator "pcbnew") (generator_version "9.0")
  (general (thickness 1.6) (legacy_teardrops no)) (layers (0 "F.Cu" signal) (2 "B.Cu" signal))
  (net 0 "") (net 1 "VPP{slash}MCLR")
  (footprint "R" (layer "F.Cu") (at 1 1) (property "Reference" "R1" (at 0 0 0)) (property "Value" "1k" (at 0 0 0))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VPP{slash}MCLR")))
  (zone (net 1) (net_name "VPP{slash}MCLR") (layer "F.Cu") (uuid "z1") (name "$teardrop_padvia$") (attr (teardrop (type padvia))) (polygon (pts (xy 0 0))))
  (zone (net 1) (net_name "VPP{slash}MCLR") (layer "B.Cu") (uuid "z2") (polygon (pts (xy 0 0))))
  (zone (net 1) (net_name "VPP{slash}MCLR") (layer "B.Cu") (uuid "z3") (polygon (pts (xy 0 0))))
)"""


def test_kicad9_reused_sheets_teardrops_and_slash(tmp_path):
    (tmp_path / "reused.kicad_sch").write_text(REUSED_ROOT)
    (tmp_path / "amp.kicad_sch").write_text(REUSED_AMP)
    sch = kicad.load_schematic(tmp_path / "reused.kicad_sch")
    assert sch.errors == []
    assert sorted((c.reference, c.sheet) for c in sch.components) == [
        ("R201", "/left"),
        ("R301", "/right"),
    ]
    assert kicad.bill_of_materials(sch)[0]["references"] == ["R201", "R301"]
    assert sch.labels == {"label": ["VPP/MCLR"]}
    (tmp_path / "reused.kicad_pcb").write_text(TEARDROP_PCB)
    board = kicad.load_pcb(tmp_path / "reused.kicad_pcb")
    assert board.teardrops == 1 and board.zones == [("VPP/MCLR", "B.Cu"), ("VPP/MCLR", "B.Cu")]
    assert kicad.board_nets(board) == {"VPP/MCLR": ["R1.1"]}
