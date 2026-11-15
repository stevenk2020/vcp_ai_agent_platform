"""Source lint for every page file (spec 003 FR-015, FR-016, SC-008).

Fails with file and line if a page uses anything that could run attacker text as markup or script, loads something
from outside the site, or keeps a secret client-side. The self-test at the bottom proves each rule can fire."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app import pages

WEB = Path(pages.WEB)
MAX_BYTES = 60 * 1024

JS_FORBIDDEN = {
    "innerHTML": r"\binnerHTML\b", "outerHTML": r"\bouterHTML\b", "insertAdjacentHTML": r"\binsertAdjacentHTML\b",
    "document.write": r"\bdocument\.write(ln)?\b", "eval": r"\beval\s*\(", "new Function": r"\bnew\s+Function\b",
    "Function()": r"(?<![\w.])Function\s*\(", "setAttribute style": r"setAttribute\(\s*['\"]style['\"]",
    "cssText": r"\.style\.cssText", "localStorage": r"\blocalStorage\b", "sessionStorage": r"\bsessionStorage\b",
    "document.cookie": r"\bdocument\.cookie\b", "external URL": r"https?://", "protocol-relative URL": r"['\"]//[\w.-]+",
    "string timer": r"\bset(Timeout|Interval)\s*\(\s*['\"]", "dynamic import": r"\bimport\s*\(",
    "createContextualFragment": r"\bcreateContextualFragment\b", "DOMParser": r"\bDOMParser\b",
    "srcdoc": r"\bsrcdoc\b", "XMLHttpRequest": r"\bXMLHttpRequest\b", "WebSocket": r"\bWebSocket\b",
    "sendBeacon": r"\bsendBeacon\b",
}


def js_violations(text: str) -> list[str]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if re.match(r"\s*//", line):  # whole-line comment (trailing comments are scanned: a URL there is still a smell)
            continue
        code = line
        for label, pattern in JS_FORBIDDEN.items():
            if re.search(pattern, code):
                found.append(f"line {number}: {label}")
    return found


class HtmlLint(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.problems: list[str] = []

    def handle_starttag(self, tag, attrs):
        names = {name for name, _ in attrs}
        values = dict(attrs)
        line = self.getpos()[0]
        for name, value in attrs:
            if name.startswith("on"):
                self.problems.append(f"line {line}: inline event handler {name}")
            if name == "style":
                self.problems.append(f"line {line}: inline style attribute")
            if value and re.match(r"\s*javascript:", value, re.I):
                self.problems.append(f"line {line}: javascript: URL")
            if name in ("href", "src", "action", "formaction", "poster", "data") and value and not value.startswith(("/", "#")):
                self.problems.append(f"line {line}: {name} leaves the site: {value!r}")
        if tag == "script":
            src = values.get("src") or ""
            if "src" not in names:
                self.problems.append(f"line {line}: inline script")
            elif not src.startswith(("/static/", "/admin/assets/")):
                self.problems.append(f"line {line}: script from {src!r}")
            if values.get("type") != "module":
                self.problems.append(f"line {line}: script is not type=module")
        if tag == "style":
            self.problems.append(f"line {line}: <style> block")
        if tag in ("iframe", "object", "embed", "base", "meta") and tag != "meta":
            self.problems.append(f"line {line}: <{tag}> is not allowed")
        if tag == "meta" and (values.get("http-equiv") or "").lower() in ("refresh", "content-security-policy"):
            self.problems.append(f"line {line}: meta {values.get('http-equiv')}")
        if tag == "link" and not (values.get("href") or "").startswith("/static/"):
            self.problems.append(f"line {line}: link outside /static/")
        if tag == "link" and (values.get("rel") or "") not in ("stylesheet", "icon"):
            self.problems.append(f"line {line}: link rel {values.get('rel')!r}")


def html_violations(text: str) -> list[str]:
    lint = HtmlLint()
    lint.feed(text)
    found = list(lint.problems)
    for number, line in enumerate(text.splitlines(), 1):
        if re.search(r"https?://", line):
            found.append(f"line {number}: absolute URL")
    return found


def css_violations(text: str) -> list[str]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if "@import" in line:
            found.append(f"line {number}: @import")
        if re.search(r"url\(\s*['\"]?(https?:|//|data:)", line):
            found.append(f"line {number}: url() to an outside or data source")
        if re.search(r"expression\s*\(|behavior\s*:|-moz-binding", line):
            found.append(f"line {number}: script in css")
    return found


def svg_violations(text: str) -> list[str]:
    found = []
    if re.search(r"<script|<foreignObject|<use\b|\bon\w+\s*=|javascript:", text, re.I):
        found.append("script or external reference in svg")
    if re.search(r"(href|src)\s*=\s*['\"](?!#)", text, re.I):
        found.append("svg references another resource")
    return found


def files(suffix: str):
    return sorted(p for p in WEB.rglob(f"*{suffix}") if p.is_file())


CHECKS = {".html": html_violations, ".js": js_violations, ".css": css_violations, ".svg": svg_violations}


def test_the_web_folder_holds_only_known_file_types():
    unknown = [str(p.relative_to(WEB)) for p in WEB.rglob("*") if p.is_file() and p.suffix not in CHECKS]
    assert unknown == []


@pytest.mark.parametrize("suffix", sorted(CHECKS))
def test_every_page_file_passes_its_lint(suffix):
    assert files(suffix), f"no {suffix} files found under {WEB}"
    problems = []
    for path in files(suffix):
        text = path.read_text(encoding="utf-8")
        problems.extend(f"{path.relative_to(WEB)} {p}" for p in CHECKS[suffix](text))
    assert problems == []


def test_every_page_file_is_small():
    big = [(str(p.relative_to(WEB)), p.stat().st_size) for p in WEB.rglob("*") if p.is_file() and p.stat().st_size > MAX_BYTES]
    assert big == []


def test_every_html_page_loads_the_shared_stylesheet_and_only_module_scripts_from_the_site():
    for path in files(".html"):
        text = path.read_text(encoding="utf-8")
        assert 'href="/static/style.css"' in text, path.name
        for src in re.findall(r'<script[^>]*src="([^"]+)"', text):
            assert src.startswith(("/static/", "/admin/assets/")), (path.name, src)


# ---- the lint itself must be able to fail -----------------------------------------------------------

@pytest.mark.parametrize("snippet,label", [
    ("node.innerHTML = x;", "innerHTML"), ("n.outerHTML = x", "outerHTML"), ("n.insertAdjacentHTML('a', x)", "insertAdjacentHTML"),
    ("document.write(x)", "document.write"), ("eval(code)", "eval"), ("new Function('x')", "new Function"),
    ("n.setAttribute('style', 'x')", "setAttribute style"), ("n.style.cssText = ''", "cssText"),
    ("localStorage.setItem('a', 'b')", "localStorage"), ("sessionStorage.x", "sessionStorage"),
    ("x = document.cookie", "document.cookie"), ("fetch('https://evil.example/x')", "external URL"),
    ("fetch('//evil.example/x')", "protocol-relative URL"), ("setTimeout('boom()', 1)", "string timer"),
    ("await import('/x.js')", "dynamic import"), ("new DOMParser()", "DOMParser"), ("new XMLHttpRequest()", "XMLHttpRequest"),
])
def test_the_js_lint_catches_each_banned_pattern(snippet, label):
    assert any(label in problem for problem in js_violations(snippet)), (snippet, js_violations(snippet))


@pytest.mark.parametrize("snippet", [
    "<script>alert(1)</script>", '<script src="https://cdn.example/x.js" type="module"></script>',
    '<script src="/static/a.js"></script>', '<button onclick="x()">', '<p style="color:red">', "<style>p{}</style>",
    '<a href="javascript:alert(1)">x</a>', '<img src="https://evil.example/p.png">', '<link rel="stylesheet" href="https://x/y.css">',
    '<iframe src="/x"></iframe>', '<meta http-equiv="refresh" content="0;url=/">', "<p>see http://example.com</p>",
    '<form action="https://evil.example"></form>',
])
def test_the_html_lint_catches_each_banned_pattern(snippet):
    assert html_violations(snippet), snippet


@pytest.mark.parametrize("snippet", ["@import url(/x.css);", "a{background:url(https://x/y.png)}", "a{background:url(//x/y)}",
                                     "a{background:url(data:image/png;base64,AA)}", "a{width:expression(alert(1))}"])
def test_the_css_and_svg_lint_catch_banned_patterns(snippet):
    assert css_violations(snippet), snippet
    assert svg_violations("<svg><script>x</script></svg>") and svg_violations('<svg><image href="http://x/y.png"/></svg>')


def test_clean_snippets_pass():
    assert js_violations("node.textContent = value; fetch('/v1/access');") == []
    assert html_violations('<script type="module" src="/static/app.js"></script><a href="/admin">x</a>') == []


# ---- feature 004: the Data tab of the admin page ----------------------------------------------------------------

def test_the_data_tab_reads_only_same_origin_sync_endpoints_and_writes_text_only():
    js = (WEB / "admin-assets" / "admin.js").read_text(encoding="utf-8")
    start, end = js.index("// ---- Data (sync status"), js.index("// ---- tabs")
    section = js[start:end]
    assert "/v1/admin/sync" in section
    assert "innerHTML" not in section and "insertAdjacentHTML" not in section and "outerHTML" not in section
    assert "http://" not in section and "https://" not in section
    for field in ("error_text", "reason_code", "last_reason", "table"):
        assert field in section
    # the badge class comes from a fixed map, never from the server string
    assert "STATE_BADGE[item.state]" in section


def test_the_refresh_timer_is_stopped_when_another_tab_is_shown():
    js = (WEB / "admin-assets" / "admin.js").read_text(encoding="utf-8")
    assert 'if (name !== "data") stopRefresh();' in js and "REFRESH_MS = 15000" in js
