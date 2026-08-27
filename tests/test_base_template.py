# base.html is the shell every page extends; it must render and carry the
# data-theme attribute so the token switch in theme.css has something to match.
from __future__ import annotations

from app.templating import templates


def test_base_template_renders_light_theme_by_default() -> None:
    html = templates.get_template("base.html").render(page_title="Colophon")

    assert 'data-theme="light"' in html
    assert "<title>Colophon</title>" in html
    assert "/static/css/theme.css" in html


def test_base_template_renders_dark_theme() -> None:
    html = templates.get_template("base.html").render(theme="dark")

    assert 'data-theme="dark"' in html
