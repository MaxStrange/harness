from __future__ import annotations

from harness.config import Config
from harness.model.types import ToolCall
from harness.skills.base import Services, SkillContext
from harness.skills.registry import SkillRegistry
from harness.skills.runner import SkillRunner, auto_approve_broker


def run(cwd, **args):
    reg = SkillRegistry()
    reg.load_builtin()
    runner = SkillRunner(reg, auto_approve_broker())
    return runner.execute(
        ToolCall("c", "directory_tree", args),
        SkillContext(cwd=cwd, config=Config(), services=Services()),
    ).result


def test_tree_depth_summaries_and_truncation(tmp_path):
    (tmp_path / "src" / "pkg" / "deep" / "deeper").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "a.py").write_text("x")
    (tmp_path / "src" / "pkg" / "deep" / "b.py").write_text("x")
    (tmp_path / "src" / "pkg" / "deep" / "deeper" / "c.py").write_text("x")
    (tmp_path / "node_modules" / "left-pad").mkdir(parents=True)
    (tmp_path / "node_modules" / "left-pad" / "index.js").write_text("x")
    big = tmp_path / "big"
    big.mkdir()
    for i in range(60):
        (big / f"f{i:03d}.txt").write_text("x")
    (tmp_path / ".secret").write_text("x")
    (tmp_path / "README.md").write_text("x")
    result = run(tmp_path, max_depth=3, max_entries=5)
    assert result.ok
    text = result.content
    assert text.startswith(str(tmp_path))
    assert "├── big/" in text and "└── ... 55 more (55 files)" in text
    assert "node_modules/  (1 dir, 1 file, not expanded)" in text
    assert "deep/  (1 dir, 2 files, below max_depth)" in text  # level 3 is summarized
    assert "deeper" not in text.replace("deep/  (1 dir", "") and ".secret" not in text
    assert "README.md" in text
    assert result.handoff.action == "explorer" and result.handoff.target == str(tmp_path)
    dirs_only = run(tmp_path, dirs_only=True).content
    assert "README.md" not in dirs_only and "src/" in dirs_only
    hidden = run(tmp_path, include_hidden=True, max_depth=4).content
    assert ".secret" in hidden and "left-pad/" in hidden
    assert "does not exist" in run(tmp_path, path="nope").content
