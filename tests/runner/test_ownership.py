"""Tests for the P3 oracle-ownership arm."""

from pathlib import Path

import pytest

from cooperbench.runner.ownership import (
    _resources,
    contested_resources,
    maybe_apply,
    ownership_block,
)

PATCH_A = """diff --git a/src/mod.py b/src/mod.py
--- a/src/mod.py
+++ b/src/mod.py
@@ -10,6 +10,9 @@ def render_page(self, ctx):
     x = 1
+    y = 2
@@ -50,3 +50,7 @@ class Loader:
+def brand_new_helper(a):
+    return a
"""

PATCH_B = """diff --git a/src/mod.py b/src/mod.py
--- a/src/mod.py
+++ b/src/mod.py
@@ -12,6 +12,8 @@ def render_page(self, ctx):
     z = 3
@@ -80,3 +80,5 @@ def unrelated(self):
+    pass
"""

PATCH_GO = """diff --git a/mux.go b/mux.go
--- a/mux.go
+++ b/mux.go
@@ -454,6 +454,10 @@ func (mx *Mux) routeHTTP(w http.ResponseWriter, r *http.Request) {
+    n := 1
"""


def test_resources_extracts_enclosing_scope_and_new_decls():
    assert _resources(PATCH_A) == {"render_page", "Loader", "brand_new_helper"}


def test_resources_handles_go_method_receivers():
    assert _resources(PATCH_GO) == {"routeHTTP"}


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    for fid, content in ((1, PATCH_A), (2, PATCH_B)):
        d = tmp_path / f"feature{fid}"
        d.mkdir()
        (d / "feature.patch").write_text(content)
    return tmp_path


def test_only_shared_resources_are_contested_and_lowest_id_owns(task_dir: Path):
    # render_page is touched by both; Loader/brand_new_helper/unrelated are not.
    assert contested_resources(task_dir, [1, 2]) == {"render_page": 1}


def test_block_tells_owner_and_non_owner_different_things(task_dir: Path):
    owner = ownership_block(task_dir, [1, 2], 1, ["agent_1", "agent_2"])
    other = ownership_block(task_dir, [1, 2], 2, ["agent_1", "agent_2"])
    assert owner is not None and other is not None
    assert "You own these" in owner
    assert "`render_page`" in owner
    assert "Owned by agent_1" in other
    assert "do NOT edit their bodies" in other
    # Never leak the diff itself -- resource names only.
    for text in (owner, other):
        assert "+    y = 2" not in text
        assert "diff --git" not in text


def test_no_block_when_nothing_is_contested(tmp_path: Path):
    for fid, body in ((1, PATCH_A), (2, PATCH_GO)):
        d = tmp_path / f"feature{fid}"
        d.mkdir()
        (d / "feature.patch").write_text(body)
    assert ownership_block(tmp_path, [1, 2], 1, ["agent_1", "agent_2"]) is None


def test_maybe_apply_is_a_no_op_unless_enabled(task_dir: Path, monkeypatch):
    monkeypatch.delenv("COOPERBENCH_OWNERSHIP", raising=False)
    assert maybe_apply("TASK", task_dir, [1, 2], 1, ["agent_1", "agent_2"]) == "TASK"

    monkeypatch.setenv("COOPERBENCH_OWNERSHIP", "1")
    out = maybe_apply("TASK", task_dir, [1, 2], 1, ["agent_1", "agent_2"])
    assert out.startswith("TASK")
    assert "Ownership assignments (binding)" in out


def test_maybe_apply_no_op_for_single_feature(task_dir: Path, monkeypatch):
    monkeypatch.setenv("COOPERBENCH_OWNERSHIP", "1")
    assert maybe_apply("TASK", task_dir, [1], 1, ["agent_1"]) == "TASK"
