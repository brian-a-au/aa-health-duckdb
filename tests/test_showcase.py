"""The README slideshow from scripts/make_showcase.py still builds.

The slideshow breaks without notice when a scene keeps a part of the page that is gone, or when
a scene's setup script fails. Both happen in the browser, so these tests open each scene in
Chrome. They skip the screenshots and FFmpeg, which only join the pictures.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from chrome import chrome_or_skip, run_probe

from ahd.render.html import render_html
from ahd.summary import build_view

ROOT = Path(__file__).resolve().parents[1]

# What is left on the page after the scene's script ran.
SCENE_PROBE = """
var root = document.documentElement;
var kept = [].filter.call(document.body.children, function (n) { return n.tagName !== "SCRIPT"; });
return {
  kept: kept.length,
  empty: kept.filter(function (n) { return !n.getBoundingClientRect().height; }).length,
  theme: root.getAttribute("data-theme"),
  width: root.clientWidth,
  scroll: root.scrollWidth
};
"""


def _load():
    spec = importlib.util.spec_from_file_location(
        "make_showcase", ROOT / "scripts/make_showcase.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SHOWCASE = _load()


@pytest.fixture(scope="module")
def html(tmp_path_factory):
    store, rid = SHOWCASE.build_store(tmp_path_factory.mktemp("showcase") / "showcase.duckdb")
    yield render_html(build_view(store, rid))
    store.con.close()


@pytest.mark.parametrize("scene", SHOWCASE.SCENES, ids=[s[0] for s in SHOWCASE.SCENES])
def test_each_scene_keeps_its_parts_without_errors(html, tmp_path, scene):
    chrome = chrome_or_skip()
    # The excerpt is screenshot at 920 CSS pixels wide.
    page = SHOWCASE.excerpt_html(html, scene)
    got = run_probe(chrome, page, SCENE_PROBE, tmp_path, width=920)
    assert got["kept"] == len(scene[5]), scene[0]
    assert got["empty"] == 0, f"scene {scene[0]} keeps a part with no height"
    assert got["theme"] == scene[6]
    assert got["scroll"] <= got["width"]


def test_each_frame_fills_every_field():
    for i, scene in enumerate(SHOWCASE.SCENES):
        frame = SHOWCASE.frame_html(i, f"excerpt{i}.png")
        assert "{{" not in frame and "}}" not in frame
        assert f"{i + 1:02d} / {scene[0]}" in frame and f"excerpt{i}.png" in frame
