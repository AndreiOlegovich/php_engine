# version 0.8.0
"""Tests for tools/translate/ru_en_translate.py (no engine needed)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS_TRANSLATE = Path(__file__).resolve().parent.parent.parent / "tools" / "translate"

sys.path.insert(0, str(TOOLS_TRANSLATE))

import ru_en_translate as _ru_en_translate  # pylint: disable=wrong-import-position,import-error  # noqa: E402

_ru_en_translate.RU_AUTHORS["Ada Example"] = ("Ада Пример", "/ru/about/ada_example/")
_ru_en_translate.EN_AUTHORS["Ада Пример"] = ("Ada Example", "/about/ada_example/")

from ru_en_translate import (  # pylint: disable=wrong-import-position,import-error  # noqa: E402
    Manifest,
    Segment,
    adapt_to_en,
    adapt_to_ru,
    carry_targets,
    check_chunk_edges,
    check_en_ru_segments,
    check_tag_spacing,
    dump_manifest,
    english_twin,
    escape_for_context,
    extract_segments,
    extract_segments_en_ru,
    html_text_chunks,
    load_folder_pairs,
    load_manifest,
    pad_target,
    reflow,
    render_output,
    ru_twin,
    strip_comment_markers,
)

SAMPLE = """<?php
$page = new ArticlePage(
    title: 'Заголовок статьи',
    description: 'Описание статьи',
    keywords: 'Ключ, слово',
    og_image_url: 'https://example.com/img.png',
    next_lesson_url: '/dev/c/next.php',
);
echo $page->createHead();
echo'
<p class="p2">
    Первый абзац. Второе предложение!
</p>
<p class="cmd1">
    touch /tmp/файл
</p>
<p class="pycode p2">
    print("код")
</p>
<p class="p2">Текст с {content} плейсхолдером.</p>
';
echo $page->createFooter();
"""


def test_extract_skips_code_urls_and_placeholders(tmp_path):
    source = tmp_path / "sample.php"
    source.write_text(SAMPLE, encoding="utf-8")
    manifest = extract_segments(source)
    texts = [segment.source for segment in manifest.segments]
    joined = "\n".join(texts)
    assert any("Заголовок" in text for text in texts)
    assert any("Первый абзац" in text for text in texts)
    assert any("Второе предложение" in text for text in texts)
    assert "touch" not in joined
    assert 'print("код")' not in joined
    assert "example.com" not in joined
    assert "/dev/c/next.php" not in joined
    assert any("{content}" in text for text in texts)


def test_sentence_chunks_keep_spans_reassemblable(tmp_path):
    source = tmp_path / "sample.php"
    source.write_text(SAMPLE, encoding="utf-8")
    manifest = extract_segments(source)
    for segment in manifest.segments:
        segment.target = segment.source
    text = source.read_text(encoding="utf-8")
    for segment in sorted(manifest.segments, key=lambda s: s.start, reverse=True):
        text = text[: segment.start] + segment.target + text[segment.end :]
    assert text == SAMPLE


def test_html_chunks_never_split_tags():
    body = '<p class="p2">Раз <b>два</b> три. Четыре!</p>'
    chunks = html_text_chunks(body, 100)
    assert len(chunks) == 4
    for start, end, part, _kind in chunks:
        assert "<" not in part and ">" not in part
        assert body[start - 100 : end - 100] == part


def test_double_quoted_labels_extracted_but_interpolation_skipped(tmp_path):
    sample = '<?php\n$articles = array(\n    "/c/", "Development на C",\n    "/c/x.php", "Hello $name",\n);\n'
    source = tmp_path / "rel.php"
    source.write_text(sample, encoding="utf-8")
    manifest = extract_segments(source)
    texts = [segment.source for segment in manifest.segments]
    assert any("Development на C" in text for text in texts)
    assert not any("Hello" in text for text in texts)


def test_html_spacer_split_word_merges():
    body = 'Опе<span class="width_a"> </span>ра<span class="width_a"> </span>тор'
    chunks = html_text_chunks(body, 0)
    assert len(chunks) == 1
    assert chunks[0][2] == body
    split = html_text_chunks("Раз <b>два</b> три. Четыре!", 0)
    assert len(split) == 4


def test_escape_for_context():
    heredocs = [(5, 15)]
    assert escape_for_context("Let's go", 6, [], [(5, 15)], []) == "Let\\'s go"
    assert escape_for_context("Let's go", 6, heredocs, [], []) == "Let's go"
    assert escape_for_context('say "hi"', 6, [], [], [(5, 15)]) == 'say \\"hi\\"'


def test_code_comments_extracted_code_untouched(tmp_path):
    sample = "<?php\necho'\n<code>// русский коммент\nint x = 1;</code>\n';\n"
    source = tmp_path / "code.php"
    source.write_text(sample, encoding="utf-8")
    manifest = extract_segments(source)
    kinds = {(segment.source.strip(), segment.kind) for segment in manifest.segments}
    assert any("русский коммент" in text and kind == "codecomment" for text, kind in kinds)
    assert not any("int x" in text for text, _kind in kinds)


def test_verify_flags_space_and_punctuation_lost_next_to_an_inline_tag():
    source = 'Centrify redefines privileged access management (<a href="dict.php#pam">PAM</a>) with an identity approach'
    good = 'Centrify redefines privileged access management (<a href="dict.php#pam">PAM</a>) with an identity approach'
    glued = 'Centrify redefines privileged access management<a href="dict.php#pam">PAM</a>with an identity approach'
    without_parens = 'Centrify redefines privileged access management <a href="dict.php#pam">PAM</a> with an identity approach'

    def segment(target):
        return Segment(id="s0001", kind="html", start=0, end=0, source=source, target=target)

    assert not check_tag_spacing(segment(good))
    assert "lost the open before an inline tag" in check_tag_spacing(segment(glued))[0]
    assert "lost the open before an inline tag" in check_tag_spacing(segment(without_parens))[0]
    extra_space = 'Centrify redefines privileged access management (<a href="dict.php#pam">PAM</a>)  with an identity approach'
    assert not check_tag_spacing(segment(extra_space))


def test_chunk_starting_with_punctuation_keeps_it():
    """The find.php case: "… unlike <a>grep</a>, you do not …" is two chunks and
    the second one starts with the comma."""
    segment = Segment(id="s0025", kind="html", start=0, end=0, source=",\n    ставить перед началом\n", target="you do not have to put any symbols")
    assert "dropped the leading ','" in check_chunk_edges(segment)[0]
    kept = Segment(id="s0025", kind="html", start=0, end=0, source=",\n    ставить перед началом\n", target=", you do not have to put any symbols")
    assert not check_chunk_edges(kept)


def test_import_keeps_leading_and_trailing_whitespace(tmp_path):
    """An import target is the text only: the chunk's own padding survives."""
    segment = Segment(id="s0", kind="html", start=0, end=0, source="\n    Первый абзац!\n")
    assert pad_target(segment, "First paragraph!") == "\n    First paragraph!\n"
    assert pad_target(segment, "  First paragraph!  ") == "\n    First paragraph!\n"
    comment = Segment(id="s1", kind="codecomment", start=0, end=0, source="// русский коммент\n")
    assert pad_target(comment, "russian comment") == "// russian comment\n"
    assert pad_target(comment, "// russian comment") == "// russian comment\n"


def test_apply_keeps_crlf(tmp_path):
    source = tmp_path / "page.php"
    source.write_bytes("\r\n".join(["<?php", "", "$page = new ArticlePage(", "    title: 'Крышка',", ");", ""]).encode("utf-8"))
    workdir = tmp_path / "work"
    script = TOOLS_TRANSLATE / "ru_en_translate.py"

    def run(*args):
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, check=False)

    assert run("extract", str(source), "--workdir", str(workdir)).returncode == 0
    manifest = load_manifest(workdir)
    targets = tmp_path / "targets.jsonl"
    rows = [{"id": segment.id, "target": "Lid"} for segment in manifest.segments]
    targets.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    assert run("translate", "--workdir", str(workdir), "--engine", "import", "--targets", str(targets)).returncode == 0
    assert run("verify", "--workdir", str(workdir)).returncode == 0
    assert run("apply", "--workdir", str(workdir), "--yes", "--force").returncode == 0
    written = source.read_bytes()
    assert b"\r\n" in written
    assert written.count(b"\n") == written.count(b"\r\n")


def test_bare_html_include_is_extracted(tmp_path):
    """A menu .inc.php with no PHP string around it (IMPROVEMENTS item 16)."""
    sample = '<!-- menu -->\n<table><tr><td><a href="/x/">Образование</a>\n<a href="/y/">Физ-Фак</a></td></tr></table>\n<img src="a.png" alt="Кафедра">\n<?php echo $footer; ?>\n<p>История кафедры</p>\n'
    source = tmp_path / "menu_inc.php"
    source.write_text(sample, encoding="utf-8")
    texts = [segment.source for segment in extract_segments(source).segments]
    assert "Образование" in texts
    assert any("История кафедры" in text for text in texts)
    assert "Кафедра" in texts
    assert not any("$footer" in text for text in texts)


def test_hash_comments_in_code_are_extracted(tmp_path):
    sample = "<?php\necho'\n<code># русский коммент\n#define X 1\n</code>\n';\n"
    source = tmp_path / "hash.php"
    source.write_text(sample, encoding="utf-8")
    chunks = [segment.source for segment in extract_segments(source).segments]
    assert any("русский коммент" in chunk for chunk in chunks)
    assert not any("#define" in chunk for chunk in chunks)


def test_entity_encoded_comment_marker(tmp_path):
    sample = "<?php\necho'\n<code>&#47;&#47; т.е. коммент\nint x = 1;</code>\n';\n"
    source = tmp_path / "entity.php"
    source.write_text(sample, encoding="utf-8")
    manifest = extract_segments(source)
    found = [s for s in manifest.segments if s.kind == "codecomment" and "коммент" in s.source]
    assert len(found) == 1
    assert found[0].source.startswith("&#47;&#47;")


def test_entity_encoded_block_comment(tmp_path):
    sample = "<?php\necho'\n<code>#define X 1 &#47;* русский коммент *&#47;</code>\n';\n"
    source = tmp_path / "block.php"
    source.write_text(sample, encoding="utf-8")
    manifest = extract_segments(source)
    found = [s for s in manifest.segments if s.kind == "codecomment" and "коммент" in s.source]
    assert len(found) == 1
    assert found[0].source.startswith("&#47;*")


def test_stale_workdir_refuses_to_render(tmp_path):
    source = tmp_path / "page.php"
    source.write_text("<?php\necho'Привет';\n", encoding="utf-8")
    manifest = Manifest(source_file=str(source))
    manifest.segments.append(Segment(id="s0000", kind="html", start=11, end=17, source="Привет", target="Hello"))
    source.write_text("<?php\necho'Пока';\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="changed since extract"):
        render_output(manifest)


def test_comment_markers_stripped_for_engine():
    assert strip_comment_markers("// коммент\n") == ("// ", "коммент", "\n")
    assert strip_comment_markers("/* текст */") == ("/* ", "текст", "*/")
    assert strip_comment_markers("&#47;&#47; текст") == ("&#47;&#47; ", "текст", "")


def test_attr_values_extracted(tmp_path):
    sample = '<?php\necho\'\n<img src="x.jpg" alt="Русская подпись">\n\';\n'
    source = tmp_path / "attr.php"
    source.write_text(sample, encoding="utf-8")
    manifest = extract_segments(source)
    found = [s for s in manifest.segments if s.kind == "attr"]
    assert len(found) == 1
    assert found[0].source == "Русская подпись"


def test_path_blocks_translate_prose_but_not_pure_paths(tmp_path):
    sample = '<?php\necho\'\n<p class="path p2">\n        A1 - это нижний левый угол.\n</p>\n<p class="path p1">\n    <span class="green">/home/user/project</span>\n</p>\n\';\n'
    source = tmp_path / "path.php"
    source.write_text(sample, encoding="utf-8")
    manifest = extract_segments(source)
    texts = [s.source for s in manifest.segments]
    assert any("нижний левый угол" in t for t in texts)
    assert not any("/home/user/project" in t for t in texts)


def test_mixed_prose_and_code_is_extracted(tmp_path):
    sample = "<?php\n$page_keywords = 'Как узнать тип, typeid() name';\n"
    source = tmp_path / "meta.php"
    source.write_text(sample, encoding="utf-8")
    manifest = extract_segments(source)
    assert any("Как узнать тип" in s.source for s in manifest.segments)


def test_manifest_roundtrip(tmp_path):
    source = tmp_path / "sample.php"
    source.write_text(SAMPLE, encoding="utf-8")
    manifest = extract_segments(source)
    workdir = tmp_path / "work"
    dump_manifest(manifest, workdir)
    reloaded = load_manifest(workdir)
    assert [s.source for s in reloaded.segments] == [s.source for s in manifest.segments]
    assert reloaded.source_file == str(source)


def test_cli_extract_verify_identity(tmp_path):
    source = tmp_path / "sample.php"
    source.write_text(SAMPLE, encoding="utf-8")
    workdir = tmp_path / "work"
    script = TOOLS_TRANSLATE / "ru_en_translate.py"

    def run(*args):
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, check=False)

    assert run("extract", str(source), "--workdir", str(workdir)).returncode == 0
    assert run("translate", "--workdir", str(workdir), "--engine", "identity").returncode == 0
    verify = run("verify", "--workdir", str(workdir))
    assert verify.returncode == 1
    assert "Cyrillic" in verify.stdout


EN_SAMPLE = """<?php

use Site\\Linux\\Hw\\RelatedArticles;
use Site\\Php\\ArticlePage;

$root = $_SERVER['DOCUMENT_ROOT'];
require_once($root . '/.php/ArticlePage.php');
$page_dir = '/linux/hw/';

$tcont_urls = array(
    "#intro", "1. First step",
    "#checklist", "Checklist",
    );

$page = new ArticlePage(
    type: 'IT',
    name: 'lid',
    lang: 'en',
    title: 'Lid and Monitor',
    next_lesson_url: '/linux/hw/next.php',
    author: ['Ada Example', '/about/ada_example/'],
);
echo'
<p class="p2">
    Read the <a href="/grep/">grep</a> page and run
    <span class="tt">ls -l</span> first.
</p>
<p class="cmd1">ls -l /etc</p>
<p class="cout1 wspw">total 0</p>
<p class="path p1">/etc/systemd/logind.conf</p>
<ul class="u1">
<li>Check the lid: <span class="cmd-line">systemd-inhibit --list</span>
<li>See <a href="/linux/hw/missing.php">another page</a>.
</ul>
';
echo $page->createFooter();
"""

EN_TARGETS = {
    "1. First step": "1. Первый шаг",
    "Checklist": "Чеклист",
    "Lid and Monitor": "Крышка и монитор",
    'Read the <a href="/grep/">grep</a> page and run\n    <span class="tt">ls -l</span> first.': 'Сначала выполните <span class="tt">ls -l</span> и\n    прочитайте страницу <a href="/grep/">grep</a>.',
    'Check the lid: <span class="cmd-line">systemd-inhibit --list</span>': 'Проверьте крышку: <span class="cmd-line">systemd-inhibit --list</span>',
    'See <a href="/linux/hw/missing.php">another page</a>.': 'См. <a href="/linux/hw/missing.php">другую страницу</a>.',
}

FOLDERS_DOC = """# English and Russian folders

## English folder to Russian folder

| English | Russian | Shared | Note |
|---|---|---:|---|
| `/linux/` | `/ru/linux/` | 228 | |

Folders that pair by name only: `/grep/` and `/ru/i/grep/`.

## Old English paths

| Old address | Now |
|---|---|
| `/i/web/...` | `/web/...` |
"""


def make_site(tmp_path):
    """A container/ copy with docs, a sitemap and the Russian folder of the twin."""
    src = tmp_path / "container" / "src"
    (src / "linux" / "hw").mkdir(parents=True)
    (src / "ru" / "linux" / "hw").mkdir(parents=True)
    (tmp_path / "container" / "docs").mkdir()
    (tmp_path / "container" / "docs" / "en-ru-folders.md").write_text(FOLDERS_DOC, encoding="utf-8")
    locs = ["/ru/i/grep/", "/ru/linux/hw/next.php", "/ru/linux/hw/lid.php"]
    (src / "sitemap.xml").write_text("".join(f"<url><loc>https://www.example.com{loc}</loc></url>" for loc in locs), encoding="utf-8")
    (src / "ru" / "linux" / "hw" / "RelatedArticles.php").write_text("<?php\n\nnamespace Site\\Ru\\Linux\\Hw;\n", encoding="utf-8")
    source = src / "linux" / "hw" / "lid.php"
    source.write_text(EN_SAMPLE, encoding="utf-8")
    return source, src / "ru" / "linux" / "hw" / "lid.php"


def test_en_ru_extract_blocks_keep_markup_and_skip_code(tmp_path):
    source, _dest = make_site(tmp_path)
    manifest = extract_segments(source, "en-ru")
    texts = [segment.source for segment in manifest.segments]
    assert set(texts) == set(EN_TARGETS)
    assert 'Read the <a href="/grep/">grep</a> page' in texts[[t.startswith("Read") for t in texts].index(True)]
    joined = "\n".join(texts)
    for code in ("total 0", "logind.conf", "Klavdiya", "ArticlePage", "'IT'"):
        assert code not in joined
    assert "/linux/hw/" not in texts
    assert {segment.kind for segment in manifest.segments} == {"meta", "html"}


def test_en_ru_check_flags_untranslated_tags_and_inline_code():
    def segment(source, target, kind="html"):
        return Segment(id="s0000", kind=kind, start=0, end=len(source), source=source, target=target)

    source = 'Run <span class="tt">ls -l</span> and <b>stop</b>.'
    assert not check_en_ru_segments(Manifest(source_file="x", segments=[segment(source, 'Выполните <span class="tt">ls -l</span> и <b>стоп</b>.')]))
    assert not check_en_ru_segments(Manifest(source_file="x", segments=[segment(source, '<b>Стоп</b> после <span class="tt">ls -l</span>.')]))
    assert "no Russian" in check_en_ru_segments(Manifest(source_file="x", segments=[segment(source, source)]))[0]
    assert "tags" in check_en_ru_segments(Manifest(source_file="x", segments=[segment(source, 'Выполните <span class="tt">ls -l</span> и стоп.')]))[0]
    assert "inline code" in check_en_ru_segments(Manifest(source_file="x", segments=[segment(source, 'Выполните <span class="tt">ls -la</span> и <b>стоп</b>.')]))[0]
    linked = 'See <a href="#x">this</a> and <b>stop</b>.'
    assert "nests" in check_en_ru_segments(Manifest(source_file="x", segments=[segment(linked, 'См. <a href="#x">это <b>и</a> стоп</b>.')]))[0]
    assert not check_en_ru_segments(Manifest(source_file="x", segments=[segment("Linux", "Linux", "meta")]))


def test_folder_pairs_and_twins(tmp_path):
    _source, _dest = make_site(tmp_path)
    pairs = load_folder_pairs(tmp_path / "container" / "docs" / "en-ru-folders.md")
    assert pairs == [("/linux/", "/ru/linux/"), ("/grep/", "/ru/i/grep/")]
    known = {"/ru/i/grep/", "/ru/linux/hw/next.php", "/ru/"}
    assert ru_twin("/grep/#flags", pairs, known) == "/ru/i/grep/#flags"
    assert ru_twin("/linux/hw/next.php", pairs, known) == "/ru/linux/hw/next.php"
    assert ru_twin("/", pairs, known) == "/ru/"
    assert ru_twin("/ru/linux/", pairs, known) == "/ru/linux/"
    assert ru_twin("/linux/hw/missing.php", pairs, known) is None


def test_adapt_to_ru(tmp_path):
    _source, dest = make_site(tmp_path)
    text, warnings = adapt_to_ru(EN_SAMPLE, dest)
    assert "use Site\\Ru\\Linux\\Hw\\RelatedArticles;" in text
    assert "use Site\\Ru\\Php\\ArticlePage;" in text
    assert "$root . '/ru/.php/ArticlePage.php'" in text
    assert "$page_dir = '/ru/linux/hw/';" in text
    assert "lang: 'ru'" in text
    assert "author: ['Ада Пример', '/ru/about/ada_example/']" in text
    assert 'href="/ru/i/grep/"' in text
    assert "next_lesson_url: '/ru/linux/hw/next.php'" in text
    assert 'href="/linux/hw/missing.php"' in text
    assert len(warnings) == 1
    assert "/linux/hw/missing.php" in warnings[0]


def test_cli_en_ru_import_verify_apply(tmp_path):
    source, dest = make_site(tmp_path)
    workdir = tmp_path / "work"
    script = TOOLS_TRANSLATE / "ru_en_translate.py"

    def run(*args):
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, check=False)

    assert run("extract", str(source), "--workdir", str(workdir), "--direction", "en-ru").returncode != 0
    assert run("extract", str(source), "--workdir", str(workdir), "--direction", "en-ru", "--out", str(dest)).returncode == 0
    manifest = load_manifest(workdir)
    targets = tmp_path / "targets.jsonl"
    rows = [{"id": segment.id, "target": EN_TARGETS[segment.source]} for segment in manifest.segments]
    targets.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows[:-1]), encoding="utf-8")
    assert run("translate", "--workdir", str(workdir), "--engine", "import", "--targets", str(targets)).returncode == 0
    with pytest.raises(SystemExit, match="no translation"):
        render_output(load_manifest(workdir))
    targets.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    assert run("translate", "--workdir", str(workdir), "--engine", "import", "--targets", str(targets)).returncode == 0
    verify = run("verify", "--workdir", str(workdir))
    assert verify.returncode == 0, verify.stdout
    assert "missing.php" in verify.stdout
    assert run("apply", "--workdir", str(workdir), "--yes").returncode == 0
    written = dest.read_text(encoding="utf-8")
    assert "Сначала выполните" in written
    assert "lang: 'ru'" in written
    assert source.read_text(encoding="utf-8") == EN_SAMPLE
    again = run("apply", "--workdir", str(workdir), "--yes")
    assert again.returncode != 0
    assert "--force" in again.stderr
    assert run("apply", "--workdir", str(workdir), "--yes", "--force").returncode == 0
    assert (workdir / "backup" / dest.name).is_file()


def test_cli_import_rejects_unknown_ids(tmp_path):
    source, dest = make_site(tmp_path)
    workdir = tmp_path / "work"
    script = TOOLS_TRANSLATE / "ru_en_translate.py"
    subprocess.run([sys.executable, str(script), "extract", str(source), "--workdir", str(workdir), "--direction", "en-ru", "--out", str(dest)], check=True, capture_output=True)
    targets = tmp_path / "targets.jsonl"
    targets.write_text(json.dumps({"id": "s9999", "target": "нет"}), encoding="utf-8")
    result = subprocess.run([sys.executable, str(script), "translate", "--workdir", str(workdir), "--engine", "import", "--targets", str(targets)], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "s9999" in result.stderr


RU_SAMPLE = """<?php

use Site\\Ru\\Linux\\Hw\\RelatedArticles;
use Site\\Ru\\Php\\ArticlePage;

$root = $_SERVER['DOCUMENT_ROOT'];
require_once($root . '/ru/.php/ArticlePage.php');
$page_dir = '/ru/linux/hw/';

$page = new ArticlePage(
    lang: 'ru',
    title: 'Крышка и монитор',
    description: 'Проверка крышки ноутбука',
    next_lesson_url: '/ru/linux/hw/next.php',
    author: ['Ада Пример', '/ru/about/ada_example/'],
);
echo'
<p class="p2">
    Прочитайте страницу <a href="/ru/i/grep/">grep</a> и запустите ls -l.
</p>
<p class="p2">См. <a href="/ru/linux/hw/missing.php">другую страницу</a>.</p>
';
echo $page->createFooter();
"""

RU_TARGETS = {
    "Крышка и монитор": "Lid and Monitor",
    "Проверка крышки ноутбука": "Checking the laptop lid",
    "Прочитайте страницу": "Read the",
    "и запустите ls -l.": "page and run ls -l.",
    "См.": "See",
    "другую страницу": "another page",
}


def make_ru_site(tmp_path):
    """An English-tree page still in Russian, its folder, the English about page."""
    src = tmp_path / "container" / "src"
    (src / "linux" / "hw").mkdir(parents=True)
    (src / "about" / "ada_example").mkdir(parents=True)
    (tmp_path / "container" / "docs").mkdir()
    (tmp_path / "container" / "docs" / "en-ru-folders.md").write_text(FOLDERS_DOC, encoding="utf-8")
    locs = ["/grep/", "/linux/hw/next.php", "/linux/hw/lid.php", "/about/ada_example/"]
    (src / "sitemap.xml").write_text("".join(f"<url><loc>https://www.example.com{loc}</loc></url>" for loc in locs), encoding="utf-8")
    (src / "linux" / "hw" / "RelatedArticles.php").write_text("<?php\n\nnamespace Site\\Linux\\Hw;\n\nclass RelatedArticles\n{\n}\n", encoding="utf-8")
    source = src / "linux" / "hw" / "lid.php"
    source.write_text(RU_SAMPLE, encoding="utf-8")
    return source


def test_english_twin(tmp_path):
    make_ru_site(tmp_path)
    pairs = load_folder_pairs(tmp_path / "container" / "docs" / "en-ru-folders.md")
    mirror = sorted(((ru, en) for en, ru in pairs), key=lambda pair: len(pair[0]), reverse=True)
    known = {"/grep/", "/linux/hw/next.php"}
    assert english_twin("/ru/i/grep/#flags", mirror, known) == "/grep/#flags"
    assert english_twin("/ru/linux/hw/next.php", mirror, known) == "/linux/hw/next.php"
    assert english_twin("/ru/linux/hw/missing.php", mirror, known) is None
    assert english_twin("/grep/", mirror, known) == "/grep/"


def test_adapt_to_en(tmp_path):
    source = make_ru_site(tmp_path)
    text, warnings = adapt_to_en(RU_SAMPLE, source)
    assert "use Site\\Linux\\Hw\\RelatedArticles;" in text
    assert "use Site\\Php\\ArticlePage;" in text
    assert "$root . '/.php/ArticlePage.php'" in text
    assert "$page_dir = '/linux/hw/';" in text
    assert "lang: 'en'" in text
    assert "author: ['Ada Example', '/about/ada_example/']" in text
    assert 'href="/grep/"' in text
    assert "next_lesson_url: '/linux/hw/next.php'" in text
    assert 'href="/ru/linux/hw/missing.php"' in text
    assert len(warnings) == 1
    assert "/ru/linux/hw/missing.php" in warnings[0]


def test_adapt_to_en_keeps_the_use_line_of_a_function_only_sibling(tmp_path):
    source = make_ru_site(tmp_path)
    (source.parent / "RelatedArticles.php").write_text("<?php\n\nnamespace Site\\Linux\\Hw;\n\nfunction list_related_urls()\n{\n}\n", encoding="utf-8")
    text, warnings = adapt_to_en(RU_SAMPLE, source)
    assert "use Site\\Ru\\Linux\\Hw\\RelatedArticles;" in text
    assert any("defines no class" in warning for warning in warnings)


def test_adapt_to_en_uses_the_required_file_not_page_dir(tmp_path):
    """The require path wins when page_dir points at another folder (the iperf case)."""
    source = make_ru_site(tmp_path)
    perf = source.parent / "perf"
    video = source.parent / "video"
    perf.mkdir()
    video.mkdir()
    (perf / "RelatedArticles.php").write_text("<?php\n\nnamespace Site\\Linux\\Hw\\Perf;\n\nclass RelatedArticles\n{\n}\n", encoding="utf-8")
    (video / "RelatedArticles.php").write_text("<?php\n\nnamespace Site\\Linux\\Hw\\Video;\n\nclass RelatedArticles\n{\n}\n", encoding="utf-8")
    page = RU_SAMPLE.replace(
        "$page_dir = '/ru/linux/hw/';",
        "$page_dir = '/ru/linux/hw/video/';\nrequire_once($root . '/linux/hw/perf/RelatedArticles.php');",
    )
    source.write_text(page, encoding="utf-8")
    text, _warnings = adapt_to_en(page, source)
    assert "use Site\\Linux\\Hw\\Perf\\RelatedArticles;" in text
    assert "Site\\Linux\\Hw\\Video" not in text


def test_adapt_to_en_uses_the_page_dir_section_file(tmp_path):
    """A page may require $page_dir . 'RelatedArticles.php', not the sibling."""
    source = make_ru_site(tmp_path)
    sub = source.parent / "video"
    sub.mkdir()
    (sub / "RelatedArticles.php").write_text("<?php\n\nnamespace Site\\Linux\\Hw\\Video;\n\nclass RelatedArticles\n{\n}\n", encoding="utf-8")
    text, _warnings = adapt_to_en(RU_SAMPLE, sub / "index.php")
    assert "use Site\\Linux\\Hw\\RelatedArticles;" in text
    assert "Site\\Linux\\Hw\\Video" not in text


def test_cli_ru_en_import_verify_apply_adapts_to_english(tmp_path, monkeypatch):
    monkeypatch.setenv("SITE_RU_AUTHORS", json.dumps({"Ada Example": ["Ада Пример", "/ru/about/ada_example/"]}))
    monkeypatch.setenv("SITE_EN_AUTHORS", json.dumps({"Ада Пример": ["Ada Example", "/about/ada_example/"]}))
    source = make_ru_site(tmp_path)
    workdir = tmp_path / "work"
    script = TOOLS_TRANSLATE / "ru_en_translate.py"

    def run(*args):
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, check=False)

    assert run("extract", str(source), "--workdir", str(workdir)).returncode == 0
    manifest = load_manifest(workdir)
    targets = tmp_path / "targets.jsonl"
    rows = [{"id": segment.id, "target": RU_TARGETS[segment.source.strip()]} for segment in manifest.segments]
    targets.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    assert run("translate", "--workdir", str(workdir), "--engine", "import", "--targets", str(targets)).returncode == 0
    verify = run("verify", "--workdir", str(workdir))
    assert verify.returncode == 0, verify.stdout
    assert "missing.php" in verify.stdout
    assert run("apply", "--workdir", str(workdir), "--yes").returncode == 0
    written = source.read_text(encoding="utf-8")
    assert "Read the" in written
    assert "lang: 'en'" in written
    assert "use Site\\Php\\ArticlePage;" in written
    assert "$page_dir = '/linux/hw/';" in written
    assert 'href="/grep/"' in written


if __name__ == "__main__":
    raise SystemExit("run with: pytest container/tests/tools/test_ru_en_translate.py -q")


def run_cli(*args):
    return subprocess.run([sys.executable, str(TOOLS_TRANSLATE / "ru_en_translate.py"), *args], capture_output=True, text=True, check=False)


def test_interpolated_string_is_split_into_literal_parts(tmp_path):
    sample = '<?php\nlocal_img("img.png", "Запустить notepad в pywinauto $alt и {$row[\'x\']} закрыть");\n'
    source = tmp_path / "img.php"
    source.write_text(sample, encoding="utf-8")
    manifest = extract_segments(source)
    assert [segment.source for segment in manifest.segments] == ["Запустить notepad в pywinauto ", " и ", " закрыть"]
    for segment, target in zip(manifest.segments, ['Start "notepad" for $5 in pywinauto', "and", "close"], strict=True):
        segment.target = pad_target(segment, target)
    assert 'Start \\"notepad\\" for $5 in pywinauto $alt and {$row[\'x\']} close"' in render_output(manifest)


def test_translation_cannot_bring_in_a_live_variable():
    text = '<?php $a = "Привет";'
    start = text.index("Привет")
    assert escape_for_context("Hello $name", start, [], [], [(start, start + 6)]) == "Hello \\$name"


def test_wspw_paragraph_with_prose_is_extracted_and_a_tree_is_not(tmp_path):
    sample = """<?php
echo'
<p class="p2 wspw">Первая строка это обычное предложение.
Вторая строка тоже обычное предложение.</p>
<p class="p2 wspw">проект
├── файл с данными проекта
└── каталог с тестами проекта</p>
<p class="cout1 wspw">Это вывод программы на экран</p>
';
"""
    source = tmp_path / "wspw.php"
    source.write_text(sample, encoding="utf-8")
    texts = " ".join(segment.source for segment in extract_segments(source).segments)
    assert "Первая строка" in texts and "Вторая строка" in texts
    assert "файл с данными" not in texts and "вывод программы" not in texts


def test_en_ru_wspw_paragraph_with_prose_is_extracted(tmp_path):
    sample = """<?php
echo'
<p class="p2 wspw">Return a shallow copy of the list.
The copy holds the same objects as the list.</p>
<p class="cout1 wspw">Compile or assemble the source files</p>
';
"""
    source = tmp_path / "wspw.php"
    source.write_text(sample, encoding="utf-8")
    sources = [segment.source for segment in extract_segments_en_ru(source).segments]
    assert len(sources) == 1 and sources[0].startswith("Return a shallow copy")


def test_cyrillic_in_a_class_name_is_not_russian_text():
    body = 'print<span class="сd0d0d0">(</span>value<span class="сd0d0d0">)</span> Комментарий'
    chunks = html_text_chunks(body, 0)
    assert [chunk[2].strip() for chunk in chunks] == ["Комментарий"]
    assert not html_text_chunks('foo<span class="сd0d0d0">bar</span>', 0)


def test_carry_targets_matches_on_source_text_not_on_id():
    old = Manifest(source_file="/x/page.php", segments=[Segment("s0000", "html", 0, 5, "Один", "One"), Segment("s0001", "html", 6, 9, "Два", "Two")])
    new = Manifest(source_file="/x/page.php", segments=[Segment("s0000", "html", 0, 5, "Новый"), Segment("s0001", "html", 6, 9, "Один"), Segment("s0002", "html", 9, 12, "Два")])
    assert carry_targets(old, new) == 2
    assert [segment.target for segment in new.segments] == ["", "One", "Two"]
    other = Manifest(source_file="/x/other.php", segments=[Segment("s0000", "html", 0, 5, "Один")])
    assert carry_targets(old, other) == 0


def test_cli_reextract_keeps_targets_unless_fresh(tmp_path):
    source = tmp_path / "page.php"
    source.write_text("<?php\necho'\n<p>Первый абзац.</p>\n<p>Второй абзац.</p>\n';\n", encoding="utf-8")
    workdir = tmp_path / "work"
    assert run_cli("extract", str(source), "--workdir", str(workdir)).returncode == 0
    targets = tmp_path / "targets.jsonl"
    targets.write_text('{"id": "s0001", "target": "Second paragraph."}\n', encoding="utf-8")
    assert run_cli("translate", "--workdir", str(workdir), "--engine", "import", "--targets", str(targets)).returncode == 0
    source.write_text("<?php\necho'\n<p>Новый абзац.</p>\n<p>Первый абзац.</p>\n<p>Второй абзац.</p>\n';\n", encoding="utf-8")
    again = run_cli("extract", str(source), "--workdir", str(workdir))
    assert "kept 1 target" in again.stdout
    assert [segment.target for segment in load_manifest(workdir).segments] == ["", "", "Second paragraph."]
    assert "kept" not in run_cli("extract", str(source), "--workdir", str(workdir), "--fresh").stdout
    assert not any(segment.target for segment in load_manifest(workdir).segments)


def test_reflow_wraps_like_the_source_and_keeps_tags_whole():
    source = 'Первая строка исходного текста\n    вторая строка <a href="/x/" class="a b">ссылка</a>\n    третья строка.'
    segment = Segment("s0000", "html", 0, len(source), source)
    target = 'The first line of the source text, the second line <a href="/x/" class="a b">link</a> and the third line.'
    wrapped = reflow(segment, target)
    lines = wrapped.splitlines()
    assert len(lines) > 1 and all(line.startswith("    ") for line in lines[1:])
    assert '<a href="/x/" class="a b">link</a>' in wrapped
    assert " ".join(wrapped.split()) == target
    assert reflow(segment, "Line one\nline two") == "Line one\nline two"
    assert reflow(Segment("s0001", "html", 0, 4, "Одна"), target) == target


def test_en_ru_allow_keeps_a_chunk_that_is_rightly_not_russian():
    manifest = Manifest(
        source_file="x.php",
        direction="en-ru",
        segments=[
            Segment("s0000", "html", 0, 1, "Run the tests.", "Запустите тесты."),
            Segment("s0001", "html", 1, 2, "ModuleNotFoundError: No module named requests", "ModuleNotFoundError: No module named requests"),
            Segment("s0002", "html", 2, 3, "Left as it was.", "Left as it was."),
        ],
    )
    assert len(check_en_ru_segments(manifest)) == 2
    errors = check_en_ru_segments(manifest, ["^ModuleNotFoundError"])
    assert len(errors) == 1 and "s0002" in errors[0]


def test_en_ru_check_flags_a_dropped_placeholder_and_a_damaged_entity():
    manifest = Manifest(
        source_file="x.php",
        direction="en-ru",
        segments=[
            Segment("s0000", "html", 0, 1, "Open {name} now.", "Откройте сейчас."),
            Segment("s0001", "html", 1, 2, "Use &lt;p&gt; here.", "Используйте p здесь."),
        ],
    )
    errors = check_en_ru_segments(manifest)
    assert any("placeholder {name}" in error for error in errors)
    assert any("damaged entity" in error for error in errors)


def test_verify_warns_about_a_broad_allow_and_ignores_class_names(tmp_path):
    lines = "".join(f'<p class="cout1">вывод {number}</p>\n' for number in range(7))
    source = tmp_path / "page.php"
    source.write_text('<?php\necho\'\n<p>Абзац.</p>\n<span class="сd0d0d0">x</span>\n' + lines + "';\n", encoding="utf-8")
    workdir = tmp_path / "work"
    assert run_cli("extract", str(source), "--workdir", str(workdir)).returncode == 0
    targets = tmp_path / "targets.jsonl"
    targets.write_text('{"id": "s0000", "target": "Paragraph."}\n', encoding="utf-8")
    assert run_cli("translate", "--workdir", str(workdir), "--engine", "import", "--targets", str(targets)).returncode == 0
    failed = run_cli("verify", "--workdir", str(workdir))
    assert failed.returncode == 1 and "7 lines still contain Cyrillic" in failed.stdout
    passed = run_cli("verify", "--workdir", str(workdir), "--allow", "вывод")
    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert "keeps 7 line(s) with Cyrillic" in passed.stdout
