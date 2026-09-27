"""Reading KiCad projects (v5 to v8): S-expression schematics and boards, JSON project files.

Pure functions, no KiCad installation needed. Used by the ``kicad_inspect`` skill.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# -- S-expressions --------------------------------------------------------------

_TOKEN_RE = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.DOTALL)


class SExprError(ValueError):
    pass


def parse_sexpr(text: str) -> list:
    """Parse one S-expression into nested lists. Quoted strings and symbols become str,
    numbers become int or float."""
    stack: list[list] = [[]]
    pos = 0
    length = len(text)
    while pos < length:
        match = _TOKEN_RE.match(text, pos)
        if match is None or match.end() == pos:
            if text[pos:].strip():
                raise SExprError(f"unexpected character at offset {pos}: {text[pos : pos + 20]!r}")
            break
        pos = match.end()
        open_, close, quoted, bare = match.groups()
        if open_:
            stack.append([])
        elif close:
            if len(stack) < 2:
                raise SExprError("unbalanced ')'")
            node = stack.pop()
            stack[-1].append(node)
        elif quoted is not None:
            stack[-1].append(quoted.replace('\\"', '"').replace("\\\\", "\\"))
        else:
            stack[-1].append(_atom(bare))
    if len(stack) != 1:
        raise SExprError("unbalanced '('")
    if not stack[0]:
        raise SExprError("empty file")
    return stack[0][0]


def _atom(token: str) -> Any:
    try:
        if re.fullmatch(r"-?\d+", token):
            return int(token)
        if re.fullmatch(r"-?\d*\.\d+(e-?\d+)?|-?\d+e-?\d+", token, re.IGNORECASE):
            return float(token)
    except ValueError:
        pass
    return token


def children(node: list, tag: str) -> list[list]:
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]


def child(node: list, tag: str) -> list | None:
    found = children(node, tag)
    return found[0] if found else None


def prop(node: list, name: str) -> str | None:
    """A KiCad ``(property "Name" "Value" ...)`` (v6+) or v5 ``(fp_text reference R1 ...)``."""
    for c in children(node, "property"):
        if len(c) >= 3 and c[1] == name:
            return str(c[2])
    legacy = {"Reference": "reference", "Value": "value"}.get(name)
    if legacy:
        for c in children(node, "fp_text"):
            if len(c) >= 3 and c[1] == legacy:
                return str(c[2])
    return None


def _at(node: list) -> tuple[float, float, float]:
    at = child(node, "at")
    if not at:
        return (0.0, 0.0, 0.0)
    nums = [float(v) for v in at[1:4] if isinstance(v, (int, float))]
    while len(nums) < 3:
        nums.append(0.0)
    return (nums[0], nums[1], nums[2])


def _flag(node: list, tag: str) -> bool | None:
    c = child(node, tag)
    if c is None:
        return None
    return len(c) < 2 or c[1] in ("yes", True)


# -- schematic -------------------------------------------------------------------


@dataclass
class Component:
    reference: str
    value: str
    footprint: str
    lib_id: str
    sheet: str
    unit: int = 1
    at: tuple[float, float, float] = (0.0, 0.0, 0.0)
    dnp: bool = False
    in_bom: bool = True

    @property
    def is_power(self) -> bool:
        return self.reference.startswith("#") or self.lib_id.startswith("power:")


@dataclass
class Sheet:
    name: str
    file: str
    path: str


@dataclass
class Schematic:
    path: str
    version: int | None
    components: list[Component] = field(default_factory=list)
    sheets: list[Sheet] = field(default_factory=list)
    labels: dict[str, list[str]] = field(default_factory=dict)  # kind -> names
    wires: int = 0
    junctions: int = 0
    no_connects: int = 0
    errors: list[str] = field(default_factory=list)


def load_schematic(path: str | Path, *, follow_sheets: bool = True) -> Schematic:
    """Read a schematic and (by default) the hierarchy below it."""
    root = Path(path)
    result = Schematic(str(root), None)
    seen: set[Path] = set()

    def visit(file: Path, sheet_name: str) -> None:
        try:
            resolved = file.resolve()
        except OSError:
            resolved = file
        if resolved in seen:
            return
        seen.add(resolved)
        try:
            node = parse_sexpr(file.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SExprError) as exc:
            result.errors.append(f"{file}: {exc}")
            return
        if node[0] != "kicad_sch":
            result.errors.append(f"{file}: not a KiCad schematic (starts with {node[0]!r})")
            return
        version = child(node, "version")
        if result.version is None and version and len(version) > 1:
            result.version = int(version[1])
        for sym in children(node, "symbol"):
            lib_id = child(sym, "lib_id")
            if lib_id is None:
                continue  # entries inside (lib_symbols ...) are handled as symbols, not instances
            unit = child(sym, "unit")
            result.components.append(
                Component(
                    reference=prop(sym, "Reference") or "?",
                    value=prop(sym, "Value") or "",
                    footprint=prop(sym, "Footprint") or "",
                    lib_id=str(lib_id[1]) if len(lib_id) > 1 else "",
                    sheet=sheet_name,
                    unit=int(unit[1]) if unit and len(unit) > 1 else 1,
                    at=_at(sym),
                    dnp=bool(_flag(sym, "dnp")),
                    in_bom=_flag(sym, "in_bom") is not False,
                )
            )
        for kind in ("label", "global_label", "hierarchical_label", "power"):
            for lab in children(node, kind):
                if len(lab) > 1:
                    result.labels.setdefault(kind, []).append(str(lab[1]))
        result.wires += len(children(node, "wire"))
        result.junctions += len(children(node, "junction"))
        result.no_connects += len(children(node, "no_connect"))
        for sheet in children(node, "sheet"):
            name = prop(sheet, "Sheetname") or prop(sheet, "Sheet name") or "?"
            file_name = prop(sheet, "Sheetfile") or prop(sheet, "Sheet file") or ""
            sub_path = file.parent / file_name if file_name else file
            result.sheets.append(Sheet(name, file_name, str(sub_path)))
            if follow_sheets and file_name:
                visit(sub_path, f"{sheet_name}/{name}" if sheet_name != "/" else f"/{name}")

    visit(root, "/")
    return result


# -- board -----------------------------------------------------------------------


@dataclass
class Pad:
    number: str
    net: str
    kind: str


@dataclass
class Footprint:
    reference: str
    value: str
    library: str
    layer: str
    at: tuple[float, float, float]
    pads: list[Pad] = field(default_factory=list)


@dataclass
class Board:
    path: str
    version: int | None
    layers: list[str] = field(default_factory=list)
    thickness: float | None = None
    nets: dict[int, str] = field(default_factory=dict)
    footprints: list[Footprint] = field(default_factory=list)
    tracks: int = 0
    vias: int = 0
    zones: list[tuple[str, str]] = field(default_factory=list)  # (net name, layer)
    outline: tuple[float, float, float, float] | None = None  # min x, min y, max x, max y
    errors: list[str] = field(default_factory=list)

    @property
    def size_mm(self) -> tuple[float, float] | None:
        if self.outline is None:
            return None
        return (self.outline[2] - self.outline[0], self.outline[3] - self.outline[1])


def load_pcb(path: str | Path) -> Board:
    board = Board(str(path), None)
    try:
        node = parse_sexpr(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, SExprError) as exc:
        board.errors.append(f"{path}: {exc}")
        return board
    if node[0] != "kicad_pcb":
        board.errors.append(f"{path}: not a KiCad board (starts with {node[0]!r})")
        return board
    version = child(node, "version")
    board.version = int(version[1]) if version and len(version) > 1 else None
    layers = child(node, "layers")
    if layers:
        board.layers = [str(lay[1]) for lay in layers[1:] if isinstance(lay, list) and len(lay) > 1]
    general = child(node, "general")
    if general:
        thickness = child(general, "thickness")
        if thickness and len(thickness) > 1:
            board.thickness = float(thickness[1])
    for net in children(node, "net"):
        if len(net) > 2:
            board.nets[int(net[1])] = str(net[2])
    for fp in children(node, "footprint") + children(node, "module"):
        layer = child(fp, "layer")
        footprint = Footprint(
            reference=prop(fp, "Reference") or "?",
            value=prop(fp, "Value") or "",
            library=str(fp[1]) if len(fp) > 1 and isinstance(fp[1], str) else "",
            layer=str(layer[1]) if layer and len(layer) > 1 else "",
            at=_at(fp),
        )
        for pad in children(fp, "pad"):
            net = child(pad, "net")
            footprint.pads.append(
                Pad(
                    number=str(pad[1]) if len(pad) > 1 else "",
                    net=str(net[2]) if net and len(net) > 2 else "",
                    kind=str(pad[2]) if len(pad) > 2 else "",
                )
            )
        board.footprints.append(footprint)
    board.tracks = len(children(node, "segment")) + len(children(node, "arc"))
    board.vias = len(children(node, "via"))
    for zone in children(node, "zone"):
        net_name = child(zone, "net_name")
        layer = child(zone, "layer") or child(zone, "layers")
        board.zones.append(
            (
                str(net_name[1]) if net_name and len(net_name) > 1 else "",
                " ".join(str(x) for x in layer[1:]) if layer else "",
            )
        )
    board.outline = _edge_cuts_bbox(node)
    return board


def _edge_cuts_bbox(node: list) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for tag in ("gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly", "gr_curve"):
        for item in children(node, tag):
            layer = child(item, "layer")
            if not layer or len(layer) < 2 or layer[1] != "Edge.Cuts":
                continue
            for point_tag in ("start", "end", "mid", "center"):
                point = child(item, point_tag)
                if point and len(point) > 2:
                    xs.append(float(point[1]))
                    ys.append(float(point[2]))
            pts = child(item, "pts")
            if pts:
                for xy in children(pts, "xy"):
                    if len(xy) > 2:
                        xs.append(float(xy[1]))
                        ys.append(float(xy[2]))
            if tag == "gr_circle":
                center, end = child(item, "center"), child(item, "end")
                if center and end and len(center) > 2 and len(end) > 2:
                    r = (
                        (float(end[1]) - float(center[1])) ** 2
                        + (float(end[2]) - float(center[2])) ** 2
                    ) ** 0.5
                    xs += [float(center[1]) - r, float(center[1]) + r]
                    ys += [float(center[2]) - r, float(center[2]) + r]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


# -- project ---------------------------------------------------------------------


@dataclass
class Project:
    directory: str
    project_file: str | None
    schematic: str | None
    pcb: str | None
    other_schematics: list[str] = field(default_factory=list)
    text_variables: dict[str, str] = field(default_factory=dict)


def find_project(path: str | Path) -> Project:
    """From a directory or any KiCad file, locate the project's files."""
    given = Path(path)
    directory = given if given.is_dir() else given.parent
    pro_files = sorted(directory.glob("*.kicad_pro")) + sorted(directory.glob("*.pro"))
    project_file: Path | None = None
    if given.suffix in (".kicad_pro", ".pro"):
        project_file = given
    elif (
        given.suffix in (".kicad_sch", ".kicad_pcb")
        and (directory / (given.stem + ".kicad_pro")).exists()
    ):
        project_file = directory / (given.stem + ".kicad_pro")
    elif pro_files:
        project_file = pro_files[0]
    stem = project_file.stem if project_file else given.stem if given.is_file() else None
    schematic = directory / f"{stem}.kicad_sch" if stem else None
    pcb = directory / f"{stem}.kicad_pcb" if stem else None
    if schematic is not None and not schematic.exists():
        candidates = sorted(directory.glob("*.kicad_sch"))
        schematic = candidates[0] if candidates else None
    if pcb is not None and not pcb.exists():
        candidates = sorted(directory.glob("*.kicad_pcb"))
        pcb = candidates[0] if candidates else None
    others = [
        str(p) for p in sorted(directory.glob("*.kicad_sch")) if schematic is None or p != schematic
    ]
    variables: dict[str, str] = {}
    if project_file and project_file.suffix == ".kicad_pro":
        try:
            data = json.loads(project_file.read_text(encoding="utf-8"))
            variables = {str(k): str(v) for k, v in (data.get("text_variables") or {}).items()}
        except (OSError, ValueError):
            pass
    return Project(
        str(directory),
        str(project_file) if project_file else None,
        str(schematic) if schematic else None,
        str(pcb) if pcb else None,
        others,
        variables,
    )


# -- derived views -----------------------------------------------------------------


def natural_key(reference: str) -> tuple:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", reference))


def bill_of_materials(schematic: Schematic) -> list[dict[str, Any]]:
    """Components grouped by value and footprint, power symbols and DNP parts left out."""
    groups: dict[tuple[str, str], list[str]] = {}
    seen: set[str] = set()
    for comp in schematic.components:
        if comp.is_power or comp.dnp or not comp.in_bom or comp.reference in seen:
            continue
        seen.add(comp.reference)  # multi-unit parts appear once
        groups.setdefault((comp.value, comp.footprint), []).append(comp.reference)
    rows = []
    for (value, footprint), refs in groups.items():
        refs.sort(key=natural_key)
        rows.append(
            {"value": value, "footprint": footprint, "quantity": len(refs), "references": refs}
        )
    rows.sort(key=lambda r: natural_key(r["references"][0]))
    return rows


def board_nets(board: Board) -> dict[str, list[str]]:
    """Net name -> the pads on it, as ``REF.PAD``."""
    nets: dict[str, list[str]] = {}
    for fp in board.footprints:
        for pad in fp.pads:
            if pad.net:
                nets.setdefault(pad.net, []).append(f"{fp.reference}.{pad.number}")
    for pads in nets.values():
        pads.sort(key=natural_key)
    return dict(sorted(nets.items()))
