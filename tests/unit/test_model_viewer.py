from __future__ import annotations

import json
import os
import struct
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QPoint, Qt  # noqa: E402

from harness.config import Config  # noqa: E402
from harness.model.types import ToolCall  # noqa: E402
from harness.skills.base import RecordingUi, Services, SkillContext  # noqa: E402
from harness.skills.registry import SkillRegistry  # noqa: E402
from harness.skills.runner import SkillRunner, auto_approve_broker  # noqa: E402


def write_stl(path, boxes):
    """A binary STL made of axis-aligned boxes (Z-up, like a CAD export)."""
    tris = []
    for x0, y0, z0, x1, y1, z1 in boxes:
        v = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
        for a, b, c, d in [
            (0, 1, 3, 2),
            (4, 6, 7, 5),
            (0, 4, 5, 1),
            (2, 3, 7, 6),
            (0, 2, 6, 4),
            (1, 5, 7, 3),
        ]:
            tris += [(v[a], v[b], v[c]), (v[a], v[c], v[d])]
    with open(path, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(tris)))
        for t in tris:
            f.write(
                struct.pack("<3f", 0, 0, 0) + b"".join(struct.pack("<3f", *p) for p in t) + b"\0\0"
            )


# -- the skill -------------------------------------------------------------------------------


def run(tmp_path, **args):
    registry = SkillRegistry()
    registry.load_builtin()
    ui = RecordingUi()
    ctx = SkillContext(cwd=tmp_path, config=Config(), services=Services(ui=ui))
    return SkillRunner(registry, auto_approve_broker()).execute(
        ToolCall("c", "view_3d_model", args), ctx
    ).result, ui


def test_view_3d_model_skill(tmp_path):
    write_stl(tmp_path / "bracket.stl", [(0, 0, 0, 40, 20, 4)])
    out, ui = run(tmp_path, path="bracket.stl")
    assert out.ok and out.handoff.action == "model" and out.handoff.target.endswith("bracket.stl")
    assert ui.opened and ui.opened[0].action == "model"  # shown straight away
    (tmp_path / "part.step").write_text("ISO-10303-21;")
    out, _ = run(tmp_path, path="part.step")
    assert not out.ok and "STEP files are not supported" in out.content
    out, _ = run(tmp_path, path="nothing.glb")
    assert not out.ok and "does not exist" in out.content


# -- the explorer ---------------------------------------------------------------------------------


def test_explorer_offers_the_viewer_for_model_files_only(qtbot, tmp_path):
    from harness.ui.orbit_explorer import OrbitExplorer

    write_stl(tmp_path / "a.stl", [(0, 0, 0, 1, 1, 1)])
    (tmp_path / "b.txt").write_text("x")
    explorer = OrbitExplorer(str(tmp_path), Config().ui.theme)
    qtbot.addWidget(explorer)
    explorer.resize(400, 300)
    explorer.settle()
    wanted = []
    explorer.model_requested.connect(wanted.append)

    def menu_for(name):
        ring = explorer.current
        index = next(i for i, e in enumerate(ring.entries) if e.name == name)
        explorer.bring_to_front(ring, index, animate=False)
        explorer.settle()
        item = max(explorer.item_geometry(ring), key=lambda g: g.depth)
        return {a.text(): a for a in explorer.context_menu(item.pos).actions() if a.text()}

    menu = menu_for("a.stl")
    menu["Open in 3D viewer"].trigger()
    assert wanted == [str(tmp_path / "a.stl")]
    assert "Open in 3D viewer" not in menu_for("b.txt")


# -- the viewer ----------------------------------------------------------------------------------


@pytest.fixture
def viewer(qtbot):
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from harness.ui.viewers.model_viewer import ModelViewer

    widget = ModelViewer(Config().ui.theme)
    qtbot.addWidget(widget)
    widget.resize(500, 400)
    widget.show()
    return widget


def test_viewer_refuses_what_it_cannot_show(viewer, tmp_path, monkeypatch):
    (tmp_path / "part.step").write_text("x")
    assert "not a model the viewer reads" in viewer.show_model(str(tmp_path / "part.step"))
    assert "cannot read" in viewer.show_model(str(tmp_path / "missing.glb"))
    import harness.ui.viewers.model_viewer as module

    monkeypatch.setattr(module, "MAX_MODEL_BYTES", 10)
    write_stl(tmp_path / "big.stl", [(0, 0, 0, 1, 1, 1)])
    assert "the viewer takes up to" in viewer.show_model(str(tmp_path / "big.stl"))


def test_viewer_loads_a_model_and_the_mouse_controls_work(viewer, qtbot, tmp_path):
    """In the real page: load, size report, left drag rotates, Shift+drag pans, reset."""
    try:
        qtbot.waitUntil(lambda: viewer.bridge.is_ready, timeout=20000)
    except Exception:
        pytest.skip("QtWebEngine did not load the viewer page here")
    write_stl(tmp_path / "bracket.stl", [(0, 0, 0, 40, 20, 4), (0, 0, 0, 4, 20, 30)])
    with qtbot.waitSignal(viewer.bridge.finished, timeout=20000) as signal:
        assert viewer.show_model(str(tmp_path / "bracket.stl")) is None
    ok, info = signal.args
    if not ok and "WebGL" in info:
        # Reported, not hung: e.g. after many windows in one test run Chromium refuses
        # more WebGL contexts. The panel says so instead of "Loading..." forever.
        assert "WebGL" in viewer.status.text()
        pytest.skip(info)
    # 30 tall along the file's Z: shown upright (Y up), so the height is the middle number.
    assert ok and info.startswith("40.00 x 30.00 x 20.00") and "24 triangles" in info
    assert "24 triangles" in viewer.status.text()

    def state():
        result = []
        viewer.view.page().runJavaScript(
            "JSON.stringify({p: camera.position.toArray(), t: controls.target.toArray()})",
            0,
            result.append,
        )
        qtbot.waitUntil(lambda: bool(result), timeout=5000)
        return json.loads(result[0])

    def drag(modifier):
        canvas = viewer.view.focusProxy()
        centre = QPoint(canvas.width() // 2, canvas.height() // 2)
        qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, modifier, centre)
        for step in range(1, 11):
            qtbot.mouseMove(canvas, QPoint(centre.x() + 12 * step, centre.y() + step))
            QCoreApplication.processEvents()
            time.sleep(0.02)
        qtbot.mouseRelease(
            canvas, Qt.MouseButton.LeftButton, modifier, QPoint(centre.x() + 120, centre.y() + 10)
        )
        qtbot.wait(800)

    def moved(a, b):
        return any(abs(x - y) > 1e-3 for x, y in zip(a, b, strict=True))

    start = state()
    drag(Qt.KeyboardModifier.NoModifier)
    rotated = state()
    assert moved(start["p"], rotated["p"]) and not moved(start["t"], rotated["t"])
    drag(Qt.KeyboardModifier.ShiftModifier)
    assert moved(rotated["t"], state()["t"])  # Shift+drag moves the pivot: a pan
    viewer.reset_button.click()
    qtbot.wait(800)
    back = state()
    assert not moved(start["p"], back["p"]) and not moved(start["t"], back["t"])
