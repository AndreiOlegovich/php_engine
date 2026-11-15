# version 0.2.0
"""Tests for tools/translate/en_ru_pairs.py (no site needed: a fake src/)."""

import subprocess
import sys
from pathlib import Path

TOOLS_TRANSLATE = Path(__file__).resolve().parent.parent.parent / "tools" / "translate"

sys.path.insert(0, str(TOOLS_TRANSLATE))

from en_ru_pairs import (  # pylint: disable=wrong-import-position,import-error  # noqa: E402
    discover,
    is_page,
    load_duplicate_groups,
    load_folder_pairs,
    page_url,
    pair_pages,
    write_tsv,
)

FOLDERS_DOC = """# English and Russian folders

## English folder to Russian folder

| English | Russian | Shared | Note |
|---|---|---:|---|
| `/section/` | `/ru/section/` | 107 | |
| `/dev/` | `/ru/code/` | 259 | |
| `/web/` | `/ru/i/web/` | 21 | |

Folders that pair by name only: `/grep/` and `/ru/i/grep/`.

## Old English paths
"""

DUPLICATES_DOC = """# Duplicate pages

## English: same title and same name in different folders (1)

| Pages | Title | Keep |
|---|---|---|
| `/section/tbody.php` `/web/tbody.php` | tbody | |
"""


def write_page(path: Path, title: str, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"<?php\n\nuse Site\\Php\\ArticlePage;\n\n$page = new ArticlePage(\n    title: '{title}',\n);\necho 'body';\n{extra}\n",
        encoding="utf-8",
    )


CONTENT = """echo'
<code>systemctl status nginx</code>
<img src="/img/nginx-status.png">
<a href="https://nginx.org/en/docs/">docs</a>
';"""


def make_empty_site(tmp_path: Path) -> Path:
    src = tmp_path / "container" / "src"
    (tmp_path / "container" / "docs").mkdir(parents=True)
    (tmp_path / "container" / "docs" / "en-ru-folders.md").write_text(FOLDERS_DOC, encoding="utf-8")
    (tmp_path / "container" / "docs" / "duplicate-pages.md").write_text(DUPLICATES_DOC, encoding="utf-8")
    src.mkdir(parents=True)
    return src


def page_source(title: str, extra: str = "", comments: list[str] | None = None, header: str = "", newline: str = "\n") -> str:
    lines = ["<?php", ""]
    if header:
        lines.extend(header.split("\n"))
        lines.append("")
    if comments:
        lines.extend(comments)
        lines.append("")
    lines.extend(
        [
            "use Site\\Php\\ArticlePage;",
            "",
            "$page = new ArticlePage(",
            f"    title: '{title}',",
            ");",
            "echo 'body';",
        ]
    )
    text = newline.join(lines) + newline
    if extra:
        text += extra + newline
    return text


def write_source(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def run_script(src: Path, *args: str):
    script = TOOLS_TRANSLATE / "en_ru_pairs.py"
    return subprocess.run([sys.executable, str(script), "--src", str(src), *args], capture_output=True, text=True, check=False)


def make_site(tmp_path: Path) -> Path:
    src = make_empty_site(tmp_path)
    write_page(src / "section" / "index.php", "Software Testing")
    write_page(src / "ru" / "section" / "index.php", "Тестирование ПО")
    write_page(src / "section" / "linked.php", "Nginx")
    write_page(src / "ru" / "section" / "linked-ru" / "index.php", "Nginx")
    write_page(src / "section" / "content.php", "Nginx status", CONTENT)
    write_page(src / "ru" / "section" / "content-copy" / "index.php", "Nginx статус", CONTENT)
    write_page(src / "section" / "tbody.php", "tbody")
    write_page(src / "web" / "tbody.php", "tbody")
    write_page(src / "ru" / "i" / "web" / "tbody" / "index.php", "tbody")
    write_page(src / "section" / "lonely.php", "Only in English")
    write_page(src / "section" / "RelatedArticles.php", "Not a page")  # no engine
    (src / "section" / "RelatedArticles.php").write_text("<?php\n\nnamespace Site\\Qa;\n\nclass RelatedArticles\n{\n}\n", encoding="utf-8")
    write_page(src / "ao" / "section" / "index.php", "Тестирование")
    return src


def test_page_url_and_is_page(tmp_path):
    assert page_url("index.php") == "/"
    assert page_url("section/index.php") == "/section/"
    assert page_url("section/x.php") == "/section/x.php"
    source = tmp_path / "container" / "src" / "section"
    source.mkdir(parents=True)
    (source / "index.php").write_text("<?php\n$page = new ArticlePage(\n    title: 'x',\n);\n", encoding="utf-8")
    (source / "RelatedArticles.php").write_text("<?php\n\nnamespace Site\\Qa;\n", encoding="utf-8")
    assert is_page((source / "index.php").read_text(encoding="utf-8"))
    assert not is_page((source / "RelatedArticles.php").read_text(encoding="utf-8"))


def test_discover_skips_ao_and_includes(tmp_path):
    src = make_site(tmp_path)
    english, russian = discover(src)
    assert "ao/section/index.php" not in {page.rel for page in russian}
    assert "section/RelatedArticles.php" not in {page.rel for page in english}
    assert "ru/section/index.php" in {page.rel for page in russian}


def test_discover_skips_zh(tmp_path):
    """zh/ is the Chinese section, not part of the English tree."""
    src = make_site(tmp_path)
    write_page(src / "zh" / "hosting" / "index.php", "Хостинг")
    write_page(src / "zh" / "hosting" / "vps" / "index.php", "VPS")
    english, russian = discover(src)
    assert not any(page.rel.startswith("zh/") for page in english)
    assert not any(page.rel.startswith("zh/") for page in russian)


def test_folder_map_pairs_same_path(tmp_path):
    src = make_site(tmp_path)
    english, russian = discover(src)
    result, _notes = pair_pages(english, russian, load_folder_pairs(tmp_path / "container" / "docs" / "en-ru-folders.md"), [])
    pairs = {pairing.en.url: pairing.ru.url if pairing.ru else None for pairing in result}
    assert pairs["/section/"] == "/ru/section/"
    assert pairs["/section/lonely.php"] is None


def test_duplicate_copy_maps_to_the_russian_page(tmp_path):
    src = make_site(tmp_path)
    english, russian = discover(src)
    groups = load_duplicate_groups(tmp_path / "container" / "docs" / "duplicate-pages.md")
    result, notes = pair_pages(english, russian, load_folder_pairs(tmp_path / "container" / "docs" / "en-ru-folders.md"), groups)
    pairs = {pairing.en.url: pairing.ru.url if pairing.ru else None for pairing in result}
    # The folder form wins the one Russian page; the copy is reported.
    assert pairs["/web/tbody.php"] == "/ru/i/web/tbody/"
    assert "folder-map" in next(pairing for pairing in result if pairing.en.url == "/web/tbody.php").method
    assert pairs["/section/tbody.php"] is None
    assert any("tbody" in note for note in notes)


def test_link_and_content_pairing(tmp_path):
    src = make_site(tmp_path)
    english, russian = discover(src)
    linked = next(page for page in english if page.rel == "section/linked.php")
    linked.links.add("/ru/section/linked-ru/")
    result, _notes = pair_pages(english, russian, load_folder_pairs(tmp_path / "container" / "docs" / "en-ru-folders.md"), [])
    pairs = {pairing.en.url: pairing for pairing in result}
    assert pairs["/section/linked.php"].ru.url == "/ru/section/linked-ru/"
    assert "link" in pairs["/section/linked.php"].method
    assert pairs["/section/content.php"].ru.url == "/ru/section/content-copy/"
    assert "content" in pairs["/section/content.php"].method


def test_one_twin_per_page_and_runners_up(tmp_path):
    src = make_site(tmp_path)
    english, russian = discover(src)
    write_page(src / "ru" / "section" / "second" / "index.php", "Nginx")
    english, russian = discover(src)
    result, _ = pair_pages(english, russian, load_folder_pairs(tmp_path / "container" / "docs" / "en-ru-folders.md"), [])
    claimed = [pairing.ru.url for pairing in result if pairing.ru is not None]
    assert len(claimed) == len(set(claimed))
    assert len({pairing.en.url for pairing in result}) == len(english)


def test_tsv_columns_and_missing_twin(tmp_path):
    src = make_site(tmp_path)
    english, russian = discover(src)
    result, _notes = pair_pages(english, russian, load_folder_pairs(tmp_path / "container" / "docs" / "en-ru-folders.md"), [])
    out = tmp_path / "pairs.tsv"
    write_tsv(result, russian, out)
    rows = [line.split("\t") for line in out.read_text(encoding="utf-8").splitlines()]
    by_en = {row[0]: row for row in rows if row[0]}
    assert by_en["section/index.php"][1] == "ru/section/index.php"
    assert by_en["section/index.php"][3].isdigit()
    assert by_en["section/lonely.php"][1:] == ["", "", "0"]
    assert any(row[0] == "" and row[1] == "ru/section/index.php" for row in rows) is False


def test_cli_writes_tsv(tmp_path):
    src = make_site(tmp_path)
    out = tmp_path / "pairs.tsv"
    script = TOOLS_TRANSLATE / "en_ru_pairs.py"
    result = subprocess.run([sys.executable, str(script), "--src", str(src), "--out", str(out)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert out.is_file()
    assert "English pages" in result.stdout
    assert out.read_text(encoding="utf-8").startswith("section/content.php\t") or "\nsection/content.php\t" in out.read_text(encoding="utf-8")


def test_comment_pair_wins_over_a_better_content_match(tmp_path):
    src = make_empty_site(tmp_path)
    write_source(src / "section" / "content.php", page_source("Nginx status", CONTENT, comments=["# ru translation: ru/section/right/index.php"]))
    write_source(src / "ru" / "section" / "right" / "index.php", page_source("Совсем другое", comments=["# en translation: section/content.php"]))
    write_source(src / "ru" / "section" / "copy" / "index.php", page_source("Nginx статус", CONTENT))
    english, russian = discover(src)
    result, _notes = pair_pages(english, russian, load_folder_pairs(tmp_path / "container" / "docs" / "en-ru-folders.md"), [])
    pairing = next(pairing for pairing in result if pairing.en.url == "/section/content.php")
    assert pairing.ru is not None
    assert pairing.ru.url == "/ru/section/right/"
    assert pairing.method == "comment"
    assert pairing.score == 100.0


def test_not_found_stays_unpaired_and_reports_a_strong_candidate(tmp_path):
    src = make_empty_site(tmp_path)
    write_source(src / "section" / "lonely.php", page_source("Nginx status", CONTENT, comments=["# ru translation not found"]))
    write_source(src / "ru" / "section" / "candidate" / "index.php", page_source("Nginx статус", CONTENT, comments=["# en translation not found"]))
    english, russian = discover(src)
    result, notes = pair_pages(english, russian, load_folder_pairs(tmp_path / "container" / "docs" / "en-ru-folders.md"), [])
    pairing = next(pairing for pairing in result if pairing.en.url == "/section/lonely.php")
    assert pairing.ru is None
    assert pairing.method == ""
    assert any("suggestion" in note and "/ru/section/candidate/" in note for note in notes)


def test_russian_side_conflicts_are_reported(tmp_path):
    src = make_empty_site(tmp_path)
    write_source(src / "section" / "a.php", page_source("Alpha", comments=["# ru translation: ru/section/b/index.php"]))
    write_source(src / "ru" / "section" / "b" / "index.php", page_source("Beta", comments=["# en translation: section/other.php"]))
    write_source(src / "section" / "other.php", page_source("Other", comments=["# ru translation not found"]))
    write_source(src / "section" / "c.php", page_source("Gamma", comments=["# ru translation: ru/section/d/index.php"]))
    write_source(src / "ru" / "section" / "d" / "index.php", page_source("Delta", comments=["# en translation not found"]))
    english, russian = discover(src)
    result, notes = pair_pages(english, russian, load_folder_pairs(tmp_path / "container" / "docs" / "en-ru-folders.md"), [])
    pairs = {pairing.en.url: pairing for pairing in result}
    assert pairs["/section/a.php"].ru is not None
    assert pairs["/section/a.php"].ru.url == "/ru/section/b/"
    assert any("conflict" in note and "section/other.php" in note for note in notes)
    assert pairs["/section/c.php"].ru is not None
    assert pairs["/section/c.php"].ru.url == "/ru/section/d/"
    assert any("conflict" in note and "not found" in note for note in notes)


def test_write_comments_plain_placement_idempotence_and_no_other_byte(tmp_path):
    src = make_empty_site(tmp_path)
    before = page_source("Software Testing")
    write_source(src / "section" / "index.php", before)
    write_source(src / "ru" / "section" / "index.php", page_source("Тестирование"))
    first = run_script(src, "--write-comments")
    assert first.returncode == 0, first.stderr
    after = (src / "section" / "index.php").read_bytes().decode("utf-8")
    assert after.startswith("<?php\n\n# ru translation: ru/section/index.php\n\nuse ")
    assert after.replace("# ru translation: ru/section/index.php\n\n", "", 1) == before
    russian = (src / "ru" / "section" / "index.php").read_bytes().decode("utf-8")
    assert russian.startswith("<?php\n\n# en translation: section/index.php\n\nuse ")
    snapshot = {path: path.read_bytes() for path in src.rglob("*.php")}
    second = run_script(src, "--write-comments")
    assert second.returncode == 0, second.stderr
    assert {path: path.read_bytes() for path in src.rglob("*.php")} == snapshot


def test_write_comments_after_version_and_draft_blocks(tmp_path):
    src = make_empty_site(tmp_path)
    write_source(src / "section" / "versioned.php", page_source("Versioned", header="# version 0.1.0\n# a note"))
    write_source(src / "ru" / "section" / "versioned.php", page_source("Версия"))
    write_source(src / "section" / "draft.php", page_source("Draft", header="# draft: a design draft\n# second line"))
    write_source(src / "ru" / "section" / "draft.php", page_source("Черновик"))
    result = run_script(src, "--write-comments")
    assert result.returncode == 0, result.stderr
    versioned = (src / "section" / "versioned.php").read_bytes().decode("utf-8")
    assert "<?php\n\n# version 0.1.0\n# a note\n\n# ru translation: ru/section/versioned.php\n\nuse " in versioned
    draft = (src / "section" / "draft.php").read_bytes().decode("utf-8")
    assert "<?php\n\n# draft: a design draft\n# second line\n\n# ru translation: ru/section/draft.php\n\nuse " in draft


def test_write_comments_replaces_existing_comment(tmp_path):
    src = make_empty_site(tmp_path)
    write_source(src / "section" / "index.php", page_source("Software Testing", comments=["# ru translation: ru/section/index.php"]))
    write_source(src / "ru" / "section" / "index.php", page_source("Тестирование", comments=["# en translation not found"]))
    result = run_script(src, "--write-comments")
    assert result.returncode == 0, result.stderr
    english = (src / "section" / "index.php").read_bytes().decode("utf-8")
    russian = (src / "ru" / "section" / "index.php").read_bytes().decode("utf-8")
    assert english.count("# ru translation") == 1
    assert russian.count("# en translation") == 1
    assert "# en translation: section/index.php\n\nuse " in russian


def test_write_comments_keeps_crlf(tmp_path):
    src = make_empty_site(tmp_path)
    before = page_source("Software Testing", newline="\r\n")
    write_source(src / "section" / "index.php", before)
    write_source(src / "ru" / "section" / "index.php", page_source("Тестирование", newline="\r\n"))
    result = run_script(src, "--write-comments")
    assert result.returncode == 0, result.stderr
    after = (src / "section" / "index.php").read_bytes().decode("utf-8")
    assert "# ru translation: ru/section/index.php\r\n" in after
    assert "\n" not in after.replace("\r\n", "")
    assert after.replace("# ru translation: ru/section/index.php\r\n\r\n", "", 1) == before


def test_write_comments_skips_ao_zh_feed_and_engine(tmp_path):
    src = make_empty_site(tmp_path)
    write_source(src / "section" / "index.php", page_source("Software Testing"))
    write_source(src / "ru" / "section" / "index.php", page_source("Тестирование"))
    skipped = ["ao/section/index.php", "zh/section/index.php", "feed/index.php", "section/.php/Engine.php"]
    for rel in skipped:
        write_source(src / rel, page_source("Skipped"))
    before = {rel: (src / rel).read_bytes() for rel in skipped}
    result = run_script(src, "--write-comments")
    assert result.returncode == 0, result.stderr
    assert {rel: (src / rel).read_bytes() for rel in skipped} == before


def test_check_clean_site_exits_zero(tmp_path):
    src = make_empty_site(tmp_path)
    write_source(src / "section" / "index.php", page_source("Software Testing", comments=["# ru translation: ru/section/index.php"]))
    write_source(src / "ru" / "section" / "index.php", page_source("Тестирование", comments=["# en translation: section/index.php"]))
    result = run_script(src, "--check")
    assert result.returncode == 0, result.stdout
    assert "consistent" in result.stdout


def test_check_reports_missing_and_multiple_comments(tmp_path):
    src = make_empty_site(tmp_path)
    write_source(src / "section" / "none.php", page_source("No comment"))
    write_source(src / "section" / "two.php", page_source("Two comments", comments=["# ru translation not found", "# ru translation: ru/section/index.php"]))
    result = run_script(src, "--check")
    assert result.returncode == 1
    assert "section/none.php: no pairing comment" in result.stdout
    assert "section/two.php: more than one pairing comment" in result.stdout


def test_check_reports_asymmetric_and_missing_targets(tmp_path):
    src = make_empty_site(tmp_path)
    write_source(src / "section" / "a.php", page_source("Alpha", comments=["# ru translation: ru/section/b/index.php"]))
    write_source(src / "ru" / "section" / "b" / "index.php", page_source("Beta", comments=["# en translation not found"]))
    write_source(src / "section" / "c.php", page_source("Gamma", comments=["# ru translation: ru/section/missing/index.php"]))
    write_source(src / "section" / "d.php", page_source("Delta", comments=["# ru translation: section/RelatedArticles.php"]))
    (src / "section" / "RelatedArticles.php").write_text("<?php\n\nnamespace Site\\Qa;\n\nclass RelatedArticles\n{\n}\n", encoding="utf-8")
    result = run_script(src, "--check")
    assert result.returncode == 1
    assert "section/a.php: pair with ru/section/b/index.php is not symmetric: ru/section/b/index.php says not found" in result.stdout
    assert "section/c.php: pairing comment names ru/section/missing/index.php, which is missing or not a page" in result.stdout
    assert "section/d.php: pairing comment names section/RelatedArticles.php, which is missing or not a page" in result.stdout


def test_check_reports_a_table_that_disagrees(tmp_path):
    src = make_empty_site(tmp_path)
    write_source(src / "section" / "index.php", page_source("Software Testing", comments=["# ru translation: ru/section/index.php"]))
    write_source(src / "ru" / "section" / "index.php", page_source("Тестирование", comments=["# en translation: section/index.php"]))
    table = tmp_path / "pairs.tsv"
    table.write_text("section/index.php\tru/section/wrong/index.php\tcomment\t100\n", encoding="utf-8")
    result = run_script(src, "--check", "--tsv", str(table))
    assert result.returncode == 1
    assert "table pairs section/index.php with ru/section/wrong/index.php, the comments do not" in result.stdout
    assert "comments pair section/index.php with ru/section/index.php, the table does not" in result.stdout


if __name__ == "__main__":
    raise SystemExit("run with: pytest container/tests/tools/test_en_ru_pairs.py -q")
