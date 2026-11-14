# version 0.6.0
"""Static guard for the CSS source tree.

The 2026-10-20 `_to_top_button.css` incident was a source file that existed
but was not listed in `css_pipeline/targets.conf`, so its rules never reached a
served sheet.  These checks are deliberately independent of a running site:

* every CSS file under `css_pipeline/source/` is a target input; retired
  sheets and partials live in `css_pipeline/_legacy/`, which no target reads;
* the shared `core` target concatenates every shared source — the restored
  partial layout plus the colour, terminal, code-block and shared UI
  partials — and each modern sheet is exactly that core plus its
  `site_specific.css`;
* language, terminal, colour and line-number classes live in their shared
  partials (`_codeblocks.css`, `_terminal.css`, `_colors.css`), which are
  core inputs;
* the width and margin utility families stay complete, even when individual
  classes are currently unused;
* the line-increment helpers use the HTML casing `.incFrN`, not the legacy
  minifier's lowercased `.incfrN`;
* the docroot `src/.css/` holds only the four minified site sheets that
  `copy-prod-css.sh` copies from `css_pipeline/prod/` and the hand-kept
  page-scoped sheets: no sources, no core, no human-readable copies; each
  served site sheet is byte-identical to its `prod/` file.
"""

import os
import re
from pathlib import Path

import pytest

CONTAINER = Path(__file__).resolve().parents[1]
PIPELINE_DIR = CONTAINER / "css_pipeline"
SOURCE_DIR = PIPELINE_DIR / "source"
CORE = PIPELINE_DIR / "build" / "core.css"
TARGETS_CONF = Path(os.environ.get("SITE_CSS_TARGETS", str(PIPELINE_DIR / "targets.conf")))
SERVED_DIR = CONTAINER / "src" / ".css"
if not TARGETS_CONF.is_file():
    pytest.skip(f"no CSS targets file at {TARGETS_CONF}: this module checks a css_pipeline", allow_module_level=True)
MODERN_TARGETS = set(os.environ.get("SITE_CSS_MODERN_TARGETS", "").split())
# Page-scoped sheets kept by hand in the docroot, outside the pipeline.
PAGE_SHEETS = {"good-design.css", "mathdict_aside.css", "react/mathdict.css", "urnsu.css"}
CODE_CLASSES = {
    "somecode",
    "somecode-with-hidden-lines",
    "jsoncode-with-hidden-lines",
    "with-line-numbers",
    "phpcode",
    "pycode",
    "rubycode",
    "shellscript",
    "course_skeleton",
    "powercode",
    "robocode",
    "asmcode",
    "ccode",
    "cobolcode",
    "cppcode",
    "csharpcode",
    "csscode",
    "dotnetcode",
    "elixircode",
    "erlangcode",
    "fortrancode",
    "gocode",
    "groovycode",
    "haskellcode",
    "htmlcode",
    "javacode",
    "jscode",
    "jsoncode",
    "perlcode",
    "rustcode",
    "thriftcode",
    "typescriptcode",
    "zplcode",
    "yamlfile",
    "dockerfile",
    "markdown",
    "ini",
}
TERMINAL_CLASSES = {
    "cmd1",
    "cmd-line",
    "cmd2",
    "cmd3",
    "cmd4",
    "cmd5",
    "cmd8",
    "cmd10",
    "cout1",
    "cout2",
    "cout3",
    "cout4",
    "cout5",
    "cout8",
    "cout10",
    "pcmd1",
    "pcmd2",
    "pcmd3",
    "pcmd4",
    "pcmd5",
    "pcmd8",
    "pcmd10",
    "pout1",
    "pout2",
    "pout3",
    "pout4",
    "pout5",
    "pout8",
    "pout10",
    "cmdout",
    "cmdout1",
    "cmdout2",
    "cmdout3",
    "cmdout4",
    "cmdout5",
    "cmdout8",
    "cmdout10",
    "pcmdout",
    "pcmdout8",
    "pcmdout10",
    "command",
    "command2",
}
COLOR_CLASSES = {
    "c000000",
    "c0000cf",
    "c0000ff",
    "c008000",
    "c0086d2",
    "c008800",
    "c00aaaa",
    "c06989a",
    "c204a87",
    "c24909d",
    "c26a269",
    "c33c7de",
    "c3465a4",
    "c34e2e2",
    "c3677a9",
    "c40ffff",
    "c447fcf",
    "c4e9a06",
    "c5fd7ff",
    "c5fff00",
    "c657b83",
    "c6ab825",
    "c6c71c4",
    "c71adff",
    "c75507b",
    "c8700ff",
    "c8f5902",
    "c93a1a1",
    "c999999",
    "ca31515",
    "caa5500",
    "cad7fa8",
    "caf5f00",
    "cc061cb",
    "cc4a000",
    "ccc0000",
    "ccd2828",
    "cce5c00",
    "cd0d0d0",
    "cd3d7cf",
    "cd700af",
    "ce9ad0c",
    "ced9d13",
    "cef2929",
    "cfb660a",
    "cfce94f",
    "cff0000",
    "cff0086",
    "cff5f00",
    "cffd7d7",
    "cfff",
    "cffff00",
}


def read_targets():
    """Return {target: [input paths relative to css_pipeline/]}."""
    targets = {}
    for line in TARGETS_CONF.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _output, *inputs = line.split()
        targets[name] = inputs
    return targets


def strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_every_source_file_is_a_target_input():
    targets = read_targets()
    referenced = {path for inputs in targets.values() for path in inputs}
    orphans = []
    for path in sorted(SOURCE_DIR.rglob("*.css")):
        rel = path.relative_to(PIPELINE_DIR).as_posix()
        if rel not in referenced:
            orphans.append(rel)
    assert not orphans, "CSS sources that no target builds (add to css_pipeline/targets.conf or move to css_pipeline/_legacy/):\n  " + "\n  ".join(orphans)


def test_core_target_builds_shared_core():
    targets = read_targets()
    assert "core" in targets, "missing core target in css_pipeline/targets.conf"
    core_line = next(line for line in TARGETS_CONF.read_text(encoding="utf-8").splitlines() if line.split() and line.split()[0] == "core")
    _name, output, *core_inputs = core_line.split()
    assert output == "build/core.css"
    inputs = set(core_inputs)
    for required in (
        "source/_utilities.css",
        "source/_profile_form.css",
        "source/profile_page.css",
        "source/style_width.css",
        "source/_blockquotes.css",
        "source/_navigation.css",
        "source/_colors.css",
        "source/_terminal.css",
        "source/_codeblocks.css",
        "source/_homeblock.css",
        "source/_asides.css",
        "source/_to_top_button.css",
    ):
        assert required in inputs, f"core target does not include {required}"
    leaked = sorted(path for path in inputs if path.startswith("source/sites/"))
    assert not leaked, "site files must not be core inputs: " + ", ".join(leaked)


def test_modern_sheets_are_core_plus_site_file():
    targets = read_targets()
    for name in sorted(MODERN_TARGETS):
        assert name in targets, f"missing target {name}"
        assert targets[name] == [
            "build/core.css",
            f"source/sites/{name}/site_specific.css",
        ], f"{name} must build from exactly core.css + its site file; got {targets[name]}"


def test_codeblocks_holds_all_language_classes():
    codeblocks = (SOURCE_DIR / "_codeblocks.css").read_text(encoding="utf-8")
    missing = sorted(name for name in CODE_CLASSES if f".{name}" not in codeblocks)
    assert not missing, "missing from source/_codeblocks.css: " + ", ".join(missing)


def test_colors_file_holds_pure_colour_families():
    colors = strip_comments((SOURCE_DIR / "_colors.css").read_text(encoding="utf-8"))
    missing = sorted(name for name in COLOR_CLASSES if f".{name}" not in colors)
    assert not missing, "colour classes missing from _colors.css: " + ", ".join(missing)
    assert not re.search(r"(?m)^\.[A-Za-z_][\w-]*\s+[.#\[A-Za-z]", colors), "context selector leaked into _colors.css"


def test_terminal_file_holds_terminal_families():
    terminal = strip_comments((SOURCE_DIR / "_terminal.css").read_text(encoding="utf-8"))
    missing = sorted(name for name in TERMINAL_CLASSES if f".{name}" not in terminal)
    assert not missing, "terminal classes missing from _terminal.css: " + ", ".join(missing)
    for name in CODE_CLASSES:
        assert f".{name}" not in terminal, f"language class .{name} leaked into _terminal.css"


def test_contextual_colour_overrides_stay_with_their_context():
    codeblocks = strip_comments((SOURCE_DIR / "_codeblocks.css").read_text(encoding="utf-8"))
    terminal = strip_comments((SOURCE_DIR / "_terminal.css").read_text(encoding="utf-8"))
    colors = strip_comments((SOURCE_DIR / "_colors.css").read_text(encoding="utf-8"))
    assert ".somecode .c000000" in codeblocks
    assert ".somecode .c000000" not in colors
    assert ".cmd1 .blue" in terminal
    assert ".cmd1 .blue" not in colors
    assert not re.search(r"(?m)^\.c[0-9a-f]{3,6}\s*\{", codeblocks), "standalone colour class in _codeblocks.css"
    assert not re.search(r"(?m)^\.c[0-9a-f]{3,6}\s*\{", terminal), "standalone colour class in _terminal.css"


def test_new_language_blocks_have_labels():
    codeblocks = (SOURCE_DIR / "_codeblocks.css").read_text(encoding="utf-8")
    for name, label in (
        ("yamlfile", "YAML "),
        ("dockerfile", "Dockerfile "),
        ("markdown", "Markdown "),
        ("ini", "INI "),
    ):
        assert f".{name}:before" in codeblocks, f"missing .{name}:before header"
        assert f'content: "{label}";' in codeblocks, f"missing {name} header label"


def test_dockerfile_page_uses_dockerfile_language_class():
    page = (CONTAINER / "src/dvps/docker/dockerfile/index.php").read_text(encoding="utf-8")
    assert 'class="dockerfile p2"' in page, "Dockerfile article does not use the .dockerfile block class"
    assert 'class="somecode p2"' not in page, "Dockerfile article still has generic .somecode blocks"


def test_shared_core_contains_code_terminal_and_colour_classes():
    """The generated shared core now carries the code, terminal and colour partials."""
    core = strip_comments(CORE.read_text(encoding="utf-8"))
    for class_name in ("somecode", "phpcode", "cmd1", "c000000", "incFr13"):
        assert f".{class_name}" in core, f".{class_name} missing from the shared core.css"
    for helper in (".counter", ".br-counter"):
        assert helper in core, f"{helper} missing from the shared core.css"


def test_codeblocks_has_no_blockquote_or_article_rules():
    codeblocks = strip_comments((SOURCE_DIR / "_codeblocks.css").read_text(encoding="utf-8"))
    for tag in ("blockquote", "html", "body"):
        pattern = re.compile(rf"(^|[,\s>+~]){tag}(?=[\s:.#,\[{{]|$)", re.I)
        assert not pattern.search(codeblocks), f"{tag} rule leaked into _codeblocks.css"
    assert ".action" not in codeblocks, "callout .action rule leaked into _codeblocks.css"


def test_line_increment_helpers_use_html_case():
    codeblocks = (SOURCE_DIR / "_codeblocks.css").read_text(encoding="utf-8")
    assert ".incFr13" in codeblocks
    assert ".incfr13" not in codeblocks


def test_every_html_incfr_has_a_rule():
    codeblocks = (SOURCE_DIR / "_codeblocks.css").read_text(encoding="utf-8")
    defined = {int(value) for value in re.findall(r"\.incFr(\d+)\s*\{", codeblocks)}
    used = set()
    for path in (CONTAINER / "src").rglob("*"):
        if path.suffix.lower() not in {".php", ".html", ".htm"}:
            continue
        used.update(int(value) for value in re.findall(r"incFr(\d+)", path.read_text(errors="replace")))
    missing = sorted(used - defined)
    assert not missing, "HTML uses incFr values without a CSS rule: " + ", ".join(map(str, missing))


def test_width_scale_is_complete():
    css = strip_comments((SOURCE_DIR / "style_width.css").read_text(encoding="utf-8"))
    widths = {}
    for name, body in re.findall(r"\.w(\d+)\s*\{([^}]*)\}", css):
        value = re.search(r"(?<![\w-])width\s*:\s*(\d+)%", body)
        if value:
            widths[int(name)] = int(value.group(1))
    missing = sorted(set(range(1, 101)) - set(widths))
    wrong = sorted((name, value) for name, value in widths.items() if value != name)
    assert not missing, "width classes missing from style_width.css: " + ", ".join(map(str, missing))
    assert not wrong, "width classes with wrong percentage: " + ", ".join(f"w{name}={value}%" for name, value in wrong)


def test_margin_scale_is_complete():
    css = strip_comments((SOURCE_DIR / "_utilities.css").read_text(encoding="utf-8"))
    for family, pattern, unit in (
        ("mb", r"\.mb(\d+)\s*\{([^}]*)\}", "rem"),
        ("m-bot", r"\.m-bot(\d+)rem\s*\{([^}]*)\}", "rem"),
    ):
        values = {}
        for name, body in re.findall(pattern, css):
            value = re.search(r"(?<![\w-])margin-bottom\s*:\s*(\d+)(rem)?", body)
            if value:
                values[int(name)] = (int(value.group(1)), value.group(2) or "")
        missing = sorted(set(range(0, 13)) - set(values))
        wrong = sorted((name, value, suffix) for name, (value, suffix) in values.items() if value != name or (name and suffix != unit))
        assert not missing, f"{family} classes missing from _utilities.css: " + ", ".join(map(str, missing))
        assert not wrong, f"{family} classes with wrong value: " + repr(wrong)


def test_mixed_case_classes_keep_both_spellings():
    core = CORE.read_text(encoding="utf-8")
    for camel, lower in (
        (".clearBoth", ".clearboth"),
        (".evenRows", ".evenrows"),
        (".h2mTop", ".h2mtop"),
        (".h3mTop", ".h3mtop"),
        (".rightBanner", ".rightbanner"),
    ):
        assert camel in core, f"missing camelCase alias {camel}"
        assert lower in core, f"missing lower-case alias {lower}"


def test_legacy_is_not_a_build_input():
    targets = read_targets()
    referenced = {path for inputs in targets.values() for path in inputs}
    leaked = sorted(path for path in referenced if path.startswith("_legacy/"))
    assert not leaked, "_legacy files must not be target inputs: " + ", ".join(leaked)


@pytest.mark.parametrize("sheet", ["build/core.css", *(f"build/{name}-human-readable.css" for name in sorted(MODERN_TARGETS))])
def test_block_backgrounds_override_paragraph_classes(sheet):
    """Pin the gulp-era cascade: <p class="path p2"> and <div class="somecode p2"> stay dark, not the .p2 grey.

    The built sheets are checked too: a .p1/.p2 background in a site_specific.css
    lands after the core and turned these blocks grey on the modern targets.
    """
    css = strip_comments((PIPELINE_DIR / sheet).read_text(encoding="utf-8"))
    rules = [(match.start(), match.group(1), match.group(2)) for match in re.finditer(r"([^{}@]+)\{([^{}]*)\}", css)]

    def last_background(selector):
        found = [position for position, selectors, body in rules if selector in {name.strip() for name in selectors.split(",")} and re.search(r"(?<![\w-])background(-color)?\s*:", body)]
        assert found, f"no background rule for {selector} in {sheet}"
        return found[-1]

    site_fix = f"drop the background from the .p1/.p2 rule in source/sites/{Path(sheet).name.split('-')[0]}/site_specific.css; the core sets it in the right place"
    fix = site_fix if sheet != "build/core.css" else "keep style*.css before the underscore partials in the core target"
    paragraph = max(last_background(f".p{number}") for number in (1, 2, 5, 8, 10))
    for block in (".path", ".err", ".warn", ".warning", ".action", ".somecode"):
        assert last_background(block) > paragraph, f'{sheet}: {block} background comes before the .p1/.p2 background, so <p class="{block[1:]} p2"> turns grey: {fix}'


def test_somecode_base_rule_keeps_its_contrast_palette():
    """Pin the intended dark code block that the site paragraph classes must not override."""
    codeblocks = strip_comments((SOURCE_DIR / "_codeblocks.css").read_text(encoding="utf-8"))
    match = re.search(r"(?m)^(?:p)?\.somecode\s*\{([^}]*)\}", codeblocks)
    assert match, "source/_codeblocks.css must keep a plain .somecode base rule"
    declarations = {}
    for declaration in match.group(1).split(";"):
        if ":" not in declaration:
            continue
        name, value = declaration.split(":", 1)
        declarations[name.strip().lower()] = " ".join(value.split())
    expected = {
        "color": "#c5c8c6",
        "background": "#1d1f21",
        "display": "block",
        "text-align": "left",
        "white-space": "pre",
        "box-sizing": "border-box",
        "border-radius": "3px",
    }
    for name, value in expected.items():
        assert declarations.get(name) == value, f".somecode {name} changed: expected {value!r}, got {declarations.get(name)!r}"
    assert "monospace" in declarations.get("font-family", ""), f".somecode lost its monospace font: {declarations.get('font-family')!r}"


def test_docroot_holds_only_served_sheets():
    """Sources and build files live in css_pipeline/, never in the docroot, where they would be public."""
    served = {path.relative_to(SERVED_DIR).as_posix() for path in SERVED_DIR.rglob("*") if path.is_file()}
    expected = {f"{name}.css" for name in MODERN_TARGETS} | PAGE_SHEETS
    assert served == expected, f"src/.css/ holds {sorted(served - expected)} beyond the served sheets and lacks {sorted(expected - served)}; sources belong in css_pipeline/source/"


@pytest.mark.parametrize("name", sorted(MODERN_TARGETS))
def test_served_site_sheets_are_minified(name):
    """build-prod-css.sh minifies to one line; a readable sheet here was copied by hand."""
    sheet = (SERVED_DIR / f"{name}.css").read_text(encoding="utf-8")
    assert "\n" not in sheet.rstrip("\n"), f"src/.css/{name}.css is not minified: run css_pipeline/copy-prod-css.sh"
    assert "/*" not in sheet, f"src/.css/{name}.css keeps comments: run css_pipeline/copy-prod-css.sh"


@pytest.mark.parametrize("name", sorted(MODERN_TARGETS))
def test_served_site_sheet_is_the_prod_file(name):
    """copy-prod-css.sh serves css_pipeline/prod/<site>.css unchanged."""
    prod = PIPELINE_DIR / "prod" / f"{name}.css"
    served = SERVED_DIR / f"{name}.css"
    assert prod.is_file(), f"missing {prod}: run css_pipeline/copy-prod-css.sh"
    assert served.read_bytes() == prod.read_bytes(), f"src/.css/{name}.css differs from css_pipeline/prod/{name}.css: run css_pipeline/copy-prod-css.sh"
