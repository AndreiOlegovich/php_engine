# version 0.1.0
"""Tests for tools/translate/ru_en_translate.py (no engine needed)."""

import subprocess
import sys
from pathlib import Path

import pytest

TOOLS_TRANSLATE = Path(__file__).resolve().parent.parent.parent / "tools" / "translate"

sys.path.insert(0, str(TOOLS_TRANSLATE))

from ru_en_translate import (  # pylint: disable=wrong-import-position,import-error  # noqa: E402
    Manifest,
    Segment,
    dump_manifest,
    escape_for_context,
    extract_segments,
    html_text_chunks,
    load_manifest,
    render_output,
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


if __name__ == "__main__":
    raise SystemExit("run with: pytest container/tests/tools/test_ru_en_translate.py -q")
