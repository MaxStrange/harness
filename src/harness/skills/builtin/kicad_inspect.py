"""Read a KiCad project (schematic hierarchy, board, BOM, nets) without ad-hoc scripts."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from harness import kicad
from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult

ACTIONS = ["overview", "schematic", "bom", "pcb", "nets"]


class KicadInspectSkill(Skill):
    name = "kicad_inspect"
    description = (
        "Read a KiCad project (v5-v8) directly from its files. Actions: overview (which files exist, "
        "counts), schematic (every component with reference, value, footprint, sheet; labels; sheets), "
        "bom (grouped by value and footprint), pcb (layers, size, footprints with positions, track and "
        "via counts, zones), nets (each board net and the pads on it). Give the project directory, "
        ".kicad_pro, .kicad_sch or .kicad_pcb. Use filter to narrow to a reference, value or net name."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project directory or a KiCad file in it."},
            "action": {"type": "string", "enum": ACTIONS},
            "filter": {
                "type": "string",
                "description": "Case-insensitive substring on references, values, footprints or net names.",
            },
            "format": {"type": "string", "enum": ["text", "json"], "description": "Default text."},
        },
        "required": ["path", "action"],
    }
    handoff_description = "The project opens in KiCad (the system's default application for it)."

    def run(self, args, ctx: SkillContext) -> SkillResult:
        path = ctx.resolve(args["path"])
        if not path.exists():
            raise SkillError(f"{path} does not exist")
        project = kicad.find_project(path)
        target = project.project_file or project.schematic or project.pcb or str(path)
        handoff = Handoff.default_app(target, label="Open in KiCad")
        action = args["action"]
        needle = (args.get("filter") or "").strip().lower()
        as_json = args.get("format") == "json"

        def matches(*fields: str) -> bool:
            return not needle or any(needle in (f or "").lower() for f in fields)

        if action == "overview":
            data = self._overview(project, path)
            text = _format_overview(data)
        elif action in ("schematic", "bom"):
            if project.schematic is None:
                raise SkillError(f"no .kicad_sch found for {path}")
            if path.suffix == ".kicad_sch":
                project.schematic = str(path)
            sch = kicad.load_schematic(project.schematic)
            if action == "schematic":
                comps = [
                    c
                    for c in sch.components
                    if matches(c.reference, c.value, c.footprint, c.lib_id)
                ]
                data = {
                    "schematic": sch.path,
                    "version": sch.version,
                    "components": [asdict(c) for c in comps],
                    "sheets": [asdict(s) for s in sch.sheets],
                    "labels": sch.labels,
                    "wires": sch.wires,
                    "junctions": sch.junctions,
                    "no_connects": sch.no_connects,
                    "errors": sch.errors,
                }
                text = _format_schematic(sch, comps)
            else:
                rows = [
                    r
                    for r in kicad.bill_of_materials(sch)
                    if matches(r["value"], r["footprint"], *r["references"])
                ]
                data = {"schematic": sch.path, "bom": rows, "errors": sch.errors}
                text = _format_bom(sch, rows)
        else:
            if project.pcb is None:
                raise SkillError(f"no .kicad_pcb found for {path}")
            if path.suffix == ".kicad_pcb":
                project.pcb = str(path)
            board = kicad.load_pcb(project.pcb)
            if board.errors:
                raise SkillError("; ".join(board.errors))
            if action == "pcb":
                fps = [
                    f for f in board.footprints if matches(f.reference, f.value, f.library, f.layer)
                ]
                data = {
                    "pcb": board.path,
                    "version": board.version,
                    "layers": board.layers,
                    "thickness_mm": board.thickness,
                    "size_mm": board.size_mm,
                    "outline": board.outline,
                    "footprints": [asdict(f) for f in fps],
                    "tracks": board.tracks,
                    "vias": board.vias,
                    "zones": board.zones,
                    "net_count": len([n for n in board.nets.values() if n]),
                }
                text = _format_pcb(board, fps)
            else:
                nets = {
                    name: pads
                    for name, pads in kicad.board_nets(board).items()
                    if matches(name, *pads)
                }
                data = {"pcb": board.path, "nets": nets}
                text = _format_nets(board, nets)
        content = json.dumps(data, indent=1) if as_json else text
        return SkillResult(content, handoff, data={"project": asdict(project)})

    @staticmethod
    def _overview(project: kicad.Project, path: Path) -> dict:
        data: dict = {"project": asdict(project)}
        if project.schematic:
            sch = kicad.load_schematic(project.schematic)
            real = [c for c in sch.components if not c.is_power]
            data["schematic"] = {
                "components": len({c.reference for c in real}),
                "power_symbols": len(sch.components) - len(real),
                "sheets": [s.name for s in sch.sheets],
                "labels": {k: len(v) for k, v in sch.labels.items()},
                "wires": sch.wires,
                "errors": sch.errors,
            }
        if project.pcb:
            board = kicad.load_pcb(project.pcb)
            data["pcb"] = {
                "version": board.version,
                "layers": len(board.layers),
                "copper_layers": len([lay for lay in board.layers if lay.endswith(".Cu")]),
                "footprints": len(board.footprints),
                "nets": len([n for n in board.nets.values() if n]),
                "tracks": board.tracks,
                "vias": board.vias,
                "zones": len(board.zones),
                "size_mm": board.size_mm,
                "thickness_mm": board.thickness,
                "errors": board.errors,
            }
        return data


def _format_overview(data: dict) -> str:
    project = data["project"]
    lines = [f"KiCad project in {project['directory']}"]
    lines.append(f"  project file: {project['project_file'] or '(none)'}")
    lines.append(f"  root schematic: {project['schematic'] or '(none)'}")
    lines.append(f"  board: {project['pcb'] or '(none)'}")
    if project["other_schematics"]:
        lines.append(
            f"  other schematic files: {', '.join(Path(p).name for p in project['other_schematics'])}"
        )
    if project["text_variables"]:
        lines.append(
            "  text variables: "
            + ", ".join(f"{k}={v}" for k, v in project["text_variables"].items())
        )
    sch = data.get("schematic")
    if sch:
        lines.append(
            f"schematic: {sch['components']} components, {sch['power_symbols']} power symbols, {sch['wires']} wires, sheets: {', '.join(sch['sheets']) or '(flat)'}, labels: {sch['labels'] or 'none'}"
        )
        lines += [f"  problem: {e}" for e in sch["errors"]]
    pcb = data.get("pcb")
    if pcb:
        size = (
            f"{pcb['size_mm'][0]:.1f} x {pcb['size_mm'][1]:.1f} mm"
            if pcb["size_mm"]
            else "unknown size"
        )
        lines.append(
            f"board: {size}, {pcb['copper_layers']} copper layers ({pcb['layers']} total), {pcb['footprints']} footprints, {pcb['nets']} nets, {pcb['tracks']} track segments, {pcb['vias']} vias, {pcb['zones']} zones"
        )
        lines += [f"  problem: {e}" for e in pcb["errors"]]
    return "\n".join(lines)


def _format_schematic(sch: kicad.Schematic, comps: list[kicad.Component]) -> str:
    lines = [f"{sch.path} (format version {sch.version}), {len(comps)} component(s) shown"]
    if sch.sheets:
        lines.append("sheets: " + ", ".join(f"{s.name} ({s.file})" for s in sch.sheets))
    for kind, names in sch.labels.items():
        lines.append(f"{kind}s: " + ", ".join(sorted(set(names))))
    lines.append(f"wires: {sch.wires}, junctions: {sch.junctions}, no-connects: {sch.no_connects}")
    lines.append(f"{'REF':<8} {'VALUE':<20} {'FOOTPRINT':<36} {'LIB':<24} SHEET")
    for c in sorted(comps, key=lambda c: kicad.natural_key(c.reference)):
        flags = " DNP" if c.dnp else "" + ("" if c.in_bom else " (not in BOM)")
        unit = f" u{c.unit}" if c.unit > 1 else ""
        lines.append(
            f"{c.reference + unit:<8} {c.value[:20]:<20} {c.footprint[:36]:<36} {c.lib_id[:24]:<24} {c.sheet}{flags}"
        )
    lines += [f"problem: {e}" for e in sch.errors]
    return "\n".join(lines)


def _format_bom(sch: kicad.Schematic, rows: list[dict]) -> str:
    total = sum(r["quantity"] for r in rows)
    lines = [
        f"BOM for {sch.path}: {len(rows)} line(s), {total} part(s)",
        f"{'QTY':>4}  {'VALUE':<22} {'FOOTPRINT':<40} REFERENCES",
    ]
    for r in rows:
        lines.append(
            f"{r['quantity']:>4}  {r['value'][:22]:<22} {r['footprint'][:40]:<40} {', '.join(r['references'])}"
        )
    lines += [f"problem: {e}" for e in sch.errors]
    return "\n".join(lines)


def _format_pcb(board: kicad.Board, fps: list[kicad.Footprint]) -> str:
    size = (
        f"{board.size_mm[0]:.2f} x {board.size_mm[1]:.2f} mm"
        if board.size_mm
        else "outline not found"
    )
    lines = [
        f"{board.path} (format version {board.version}): {size}, thickness {board.thickness} mm",
        f"layers: {', '.join(board.layers)}",
        f"{len(board.footprints)} footprints, {len([n for n in board.nets.values() if n])} nets, {board.tracks} track segments, {board.vias} vias",
    ]
    if board.zones:
        lines.append(
            "zones: " + ", ".join(f"{net or '?'} on {layer}" for net, layer in board.zones)
        )
    lines.append(
        f"{'REF':<8} {'VALUE':<18} {'FOOTPRINT':<40} {'LAYER':<6} {'X':>8} {'Y':>8} {'ROT':>5}  PADS"
    )
    for f in sorted(fps, key=lambda f: kicad.natural_key(f.reference)):
        lines.append(
            f"{f.reference:<8} {f.value[:18]:<18} {f.library[:40]:<40} {f.layer:<6} {f.at[0]:>8.2f} {f.at[1]:>8.2f} {f.at[2]:>5.0f}  {len(f.pads)}"
        )
    return "\n".join(lines)


def _format_nets(board: kicad.Board, nets: dict[str, list[str]]) -> str:
    lines = [f"{board.path}: {len(nets)} net(s) shown"]
    for name, pads in nets.items():
        lines.append(f"{name}: {', '.join(pads)}")
    return "\n".join(lines)
