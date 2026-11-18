# version 0.3.0
"""Self-test for container/tools/check-diff-artifacts.sh.

The gate blocks byte-level damage only: line endings flipped or mixed,
CRLF lost together with other changes, U+FFFD, mojibake, a UTF-8 file turned
into another encoding, a BOM added. A conversion from CRLF to LF with nothing
else changed, whitespace-only edits and 1:1 rewrites of many lines are
ordinary changes: they pass, conversions and whitespace-only edits with a
NOTE. The script runs in scratch git repos.
"""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check-diff-artifacts.sh"
ORIGINAL = b"a Java Script b\n    x = 1\nfoo\n"
RUSSIAN = "Привет, мир! Это проверка кодировки.\n" * 3
LATIN = "café naïve résumé — “quoted”\n" * 2


def run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=60, check=False)


@pytest.fixture
def repo(tmp_path):
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.org"],
        ["git", "config", "user.name", "Test"],
        ["git", "config", "core.autocrlf", "false"],
    ):
        run(cmd, tmp_path)
    (tmp_path / "page.txt").write_bytes(ORIGINAL)
    run(["git", "add", "page.txt"], tmp_path)
    run(["git", "commit", "-qm", "init"], tmp_path)
    return tmp_path


def commit(repo, name, content):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    run(["git", "add", "--", name], repo)
    run(["git", "commit", "-qm", f"add {name}"], repo)


def gate(repo, base, new, name):
    """Commit base as name, stage new over it and run the gate on the index."""
    commit(repo, name, base)
    (repo / name).write_bytes(new)
    run(["git", "add", "--", name], repo)
    return run([str(SCRIPT), "--staged"], repo)


def out(result):
    return result.stdout + result.stderr


def crlf(text):
    return text.replace(b"\n", b"\r\n")


LONG = b"".join(b"line %d of the page\n" % i for i in range(150))
EDITED = LONG.replace(b"line 7 of", b"line seven of")  # LF, one line changed


@pytest.mark.parametrize(
    ("base", "new", "message"),
    [
        (crlf(LONG), EDITED, "CRLF line endings lost (base CRLF on all 150 lines, now LF) together with other changes"),
        (crlf(LONG), LONG.replace(b"line 7 of", b"line 7  of"), "CRLF line endings lost"),
        (crlf(LONG), crlf(LONG) + b"added with LF\n", "mixed line endings, 1 of 151 lines now end in LF"),
        (LONG, crlf(LONG), "line endings flipped from LF to CRLF"),
        (LONG, LONG + b"pasted\r\n", "mixed line endings, 1 of 151 lines now end in CRLF"),
        (ORIGINAL, ORIGINAL + "bad \N{REPLACEMENT CHARACTER} byte\n".encode(), "U+FFFD replacement chars introduced"),
        (
            RUSSIAN.encode(),
            RUSSIAN.encode().decode("cp1251").encode(),
            "mojibake introduced",
        ),
        (
            LATIN.encode(),
            LATIN.encode().decode("latin-1").encode(),
            "mojibake introduced",
        ),
        (RUSSIAN.encode(), RUSSIAN.encode("cp1251"), "no longer valid UTF-8"),
        (ORIGINAL, b"\xef\xbb\xbf" + ORIGINAL, "UTF-8 BOM added"),
    ],
    ids=[
        "crlf-lost-with-edit",
        "crlf-lost-with-space-edit",
        "crlf-file-gets-lf-line",
        "lf-to-crlf",
        "lf-file-gets-crlf-line",
        "fffd",
        "mojibake-cp1251",
        "mojibake-latin1",
        "saved-as-cp1251",
        "bom-added",
    ],
)
def test_damage_is_blocked(repo, base, new, message):
    result = gate(repo, base, new, "damaged.txt")
    assert result.returncode == 1, out(result)
    assert message in out(result)


@pytest.mark.parametrize(
    "content",
    [
        b"a Java Script b  \n    x = 1\nfoo\n",
        b"a Java Script b\n\tx = 1\nfoo\n",
        b"a Java   Script b\n    x = 1\nfoo\n",
    ],
    ids=["trailing", "tab-indent", "space-run"],
)
def test_whitespace_only_diff_is_a_note(repo, content):
    (repo / "page.txt").write_bytes(content)
    run(["git", "add", "page.txt"], repo)
    result = run([str(SCRIPT), "--staged"], repo)
    assert result.returncode == 0, out(result)
    assert "NOTE: page.txt [staged]: whitespace-only change" in out(result)
    assert "ARTIFACT" not in out(result)


def test_space_removed_inside_a_line_passes(repo):
    (repo / "page.txt").write_bytes(b"a JavaScript b\n    x = 1\nfoo\n")
    run(["git", "add", "page.txt"], repo)
    result = run([str(SCRIPT), "--staged"], repo)
    assert result.returncode == 0, out(result)
    assert out(result).strip() == "check-diff-artifacts: clean"


@pytest.mark.parametrize(
    ("base", "new"),
    [
        (LONG, LONG.replace(b"line", b"row")),
        (crlf(LONG), crlf(LONG.replace(b"line", b"row"))),
        (crlf(LONG), crlf(LONG) + b"one more\r\n"),
        (
            "Строка о коде.\n".encode() * 120,
            b"A line about code.\n" * 120,
        ),
        (b"plain\n", ("Пример ошибки: " + "Привет".encode().decode("cp1251") + "\n").encode()),
        (RUSSIAN.encode("cp1251"), RUSSIAN.encode("cp1251") + b"one more\n"),
    ],
    ids=[
        "1to1-rewrite-lf",
        "1to1-rewrite-crlf",
        "crlf-kept",
        "translation",
        "one-mojibake-sample",
        "legacy-cp1251-edit",
    ],
)
def test_ordinary_changes_pass(repo, base, new):
    result = gate(repo, base, new, "ordinary.txt")
    assert result.returncode == 0, out(result)
    assert out(result).strip() == "check-diff-artifacts: clean"


def test_bom_removed_is_a_note(repo):
    result = gate(repo, b"\xef\xbb\xbf" + ORIGINAL, ORIGINAL, "bom.txt")
    assert result.returncode == 0, out(result)
    assert "NOTE: bom.txt [staged]: UTF-8 BOM removed" in out(result)


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("new.txt", b"one\r\ntwo\n", "new file with mixed line endings"),
        ("unichr.txt", "chr(0xfffd) prints \N{REPLACEMENT CHARACTER}\n".encode(), "new file with U+FFFD"),
        ("README.mux", b"\xef\xbb\xbfvendored\n", "new file with a UTF-8 BOM"),
    ],
    ids=["mixed-endings", "fffd", "bom"],
)
def test_new_file_findings_are_notes(repo, name, content, message):
    (repo / name).write_bytes(content)
    run(["git", "add", name], repo)
    result = run([str(SCRIPT), "--staged"], repo)
    assert result.returncode == 0, out(result)
    assert f"NOTE: {name} [staged]: {message}" in out(result)


def test_new_php_file_with_bom_is_blocked(repo):
    (repo / "page.php").write_bytes(b"\xef\xbb\xbf<?php\necho 1;\n")
    run(["git", "add", "page.php"], repo)
    result = run([str(SCRIPT), "--staged"], repo)
    assert result.returncode == 1, out(result)
    assert "new PHP file with a UTF-8 BOM" in out(result)


@pytest.mark.parametrize(
    ("base", "new", "message"),
    [
        (crlf(LONG), LONG, "converted from CRLF to LF, nothing else changed (150 lines)"),
        (crlf(LONG) + b"one stray LF line\n", LONG + b"one stray LF line\n", "converted from mixed endings to LF"),
    ],
    ids=["crlf", "mostly-crlf-mixed"],
)
def test_conversion_to_lf_is_a_note(repo, base, new, message):
    result = gate(repo, base, new, "convert.txt")
    assert result.returncode == 0, out(result)
    assert f"NOTE: convert.txt [staged]: {message}" in out(result)
    assert "whitespace-only change" not in out(result)


def test_renamed_and_converted_file_is_a_note(repo):
    commit(repo, "old.txt", crlf(LONG))
    run(["git", "mv", "old.txt", "new.txt"], repo)
    (repo / "new.txt").write_bytes(LONG)
    run(["git", "add", "new.txt"], repo)
    result = run([str(SCRIPT), "--staged"], repo)
    assert result.returncode == 0, out(result)
    assert "NOTE: new.txt (from old.txt) [staged]: converted from CRLF to LF" in out(result)


def test_feed_json_line_endings_are_exempt(repo):
    """The default folder is feed; SITE_FEED_DIR moves it."""
    result = gate(repo, crlf(LONG), LONG, "container/src/feed/feed.json")
    assert result.returncode == 0, out(result)


def test_generated_css_line_endings_are_a_note(repo):
    result = gate(repo, LONG, LONG + b".pasted {}\r\n", "container/css_pipeline/build/core.css")
    assert result.returncode == 0, out(result)
    assert "NOTE: container/css_pipeline/build/core.css [staged]: mixed line endings" in out(result)


def test_binary_file_is_skipped(repo):
    result = gate(repo, b"\x00\x01\r\n" * 50, b"\x00\x02\n" * 50, "image.bin")
    assert result.returncode == 0, out(result)


def test_renamed_file_is_compared_with_its_source(repo):
    commit(repo, "old.txt", crlf(LONG))
    run(["git", "mv", "old.txt", "new.txt"], repo)
    (repo / "new.txt").write_bytes(crlf(LONG) + b"added with LF\n")
    run(["git", "add", "new.txt"], repo)
    result = run([str(SCRIPT), "--staged"], repo)
    assert result.returncode == 1, out(result)
    assert "new.txt (from old.txt) [staged]: mixed line endings" in out(result)


def test_non_ascii_path_is_checked(repo):
    result = gate(repo, crlf(LONG), EDITED, "города.txt")
    assert result.returncode == 1, out(result)
    assert "города.txt [staged]: CRLF line endings lost" in out(result)


def test_all_mode_sees_unstaged_changes(repo):
    commit(repo, "doc.txt", crlf(LONG))
    (repo / "doc.txt").write_bytes(EDITED)
    staged = run([str(SCRIPT), "--staged"], repo)
    assert staged.returncode == 0, out(staged)
    for args in ([], ["--all"]):
        result = run([str(SCRIPT), *args], repo)
        assert result.returncode == 1, out(result)
        assert "CRLF line endings lost" in out(result)


def test_commit_mode_audits_a_commit(repo):
    commit(repo, "doc.txt", crlf(LONG))
    commit(repo, "doc.txt", EDITED)
    result = run([str(SCRIPT), "--commit", "HEAD"], repo)
    assert result.returncode == 1, out(result)
    assert "doc.txt [commit " in out(result)
    assert run([str(SCRIPT), "--commit", "HEAD~1"], repo).returncode == 0


def test_unknown_argument_is_a_usage_error(repo):
    result = run([str(SCRIPT), "--stagd"], repo)
    assert result.returncode == 2
    assert "unknown argument" in out(result)
