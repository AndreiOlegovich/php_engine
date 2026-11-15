#!/usr/bin/env python3
# version 0.8.0
"""Article translation pipeline, Russian to English and English to Russian.

Stages: extract -> translate -> verify -> apply.

ru-en (default) translates a page in place. The script never touches
markup, code, URLs, or placeholders: it splits only human-readable text
into sentence-sized chunks (HTML tags, <code>, command/output blocks and
PHP code are byte-preserved), stores them as JSONL work files, translates
each chunk through a pluggable engine, and writes the file only on
explicit ``apply --yes`` after verification passes. verify then makes the
page fit for the English site (the mirror of en-ru): lang, English engine
includes and $page_dir, the RelatedArticles namespace of the page's own
folder, the author's English name, and /ru/ links mapped to their English
twins (a link without one is reported and left as it is).

The pairing of the two trees is a separate command: ``en_ru_pairs.py``
writes en_ru_pairs.tsv (en_path, ru_path, method, score).

en-ru writes the Russian twin of an English page to ``--out``. A chunk is
a whole block (p, li, h1-h6, td, th) with its inline markup, so the
translator sees full sentences; code blocks, inline code (tt, cmd-line)
and PHP code are skipped. verify checks that every chunk came back in
Russian with the same tags and the same inline code, then adapts the page
to the Russian engine: lang, /ru/.php includes, $page_dir, the
RelatedArticles namespace of the destination folder, the author, and
internal links mapped to their Russian twins through
container/docs/en-ru-folders.md and src/sitemap.xml (a link without a
twin is reported and left as it is).

Engines: argos and ollama (local), identity (dry run), import (targets
written by a person or an AI agent: JSON lines {"id": ..., "target": ...}
for the ids in segments.jsonl).

Both directions: a <p class="... wspw"> that holds prose and not a tree or
program output is translated; extract in a workdir that already has targets
keeps those whose source text is still on the page (--fresh drops them);
translate --reflow wraps a one-line target like its source chunk; verify
--allow REGEX keeps a line with Cyrillic (ru-en) or a chunk that rightly
stays not Russian (en-ru), and warns when one pattern keeps more than five.

Usage:
    python3 ru_en_translate.py extract PATH --workdir DIR
    python3 ru_en_translate.py extract PATH --workdir DIR --direction en-ru --out RU_PATH
    python3 ru_en_translate.py translate --workdir DIR --engine argos
    python3 ru_en_translate.py translate --workdir DIR --engine import --targets FILE [--reflow]
    python3 ru_en_translate.py verify --workdir DIR [--allow REGEX ...]
    python3 ru_en_translate.py apply --workdir DIR --yes [--force]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

DIRECTIONS = ("ru-en", "en-ru")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
HEREDOC = re.compile(r"<<<('?)([A-Za-z_][A-Za-z0-9_]*)\1\n(.*?)\n\2;", re.DOTALL)
TAG_OR_TEXT = re.compile(r"<[^<>]*>|[^<>]+")
TAG = re.compile(r"<[^<>]*>")
P_CLOSE = re.compile(r"</p\s*>", re.IGNORECASE)
CLASS_ATTR = re.compile(r"""\bclass\s*=\s*(?:"[^"]*"|'[^']*')""")
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[A-ZА-ЯЁ«\"“(\-])")
PLACEHOLDER = re.compile(r"\{[A-Za-z0-9_]+\}")
PHP_BLOCK = re.compile(r"<\?(?:php\b|=).*?(?:\?>|\Z)", re.DOTALL)
ATTR_VALUE = re.compile(r"""(?:alt|title)\s*=\s*(?:"([^"]*)"|'([^']*)')""")

SKIP_TAGS = {"code", "pre", "script", "style"}
SKIP_CLASSES = {
    "cmd1",
    "cout1",
    "pycode",
    "ccode",
    "cppcode",
    "fname",
    "wspw",
}

NEVER_TRANSLATE = (
    re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$"),  # identifiers
    re.compile(r"^/"),  # absolute paths / URLs
    re.compile(r"^[a-z]+://"),  # scheme URLs
    re.compile(r"^#"),  # anchors
    re.compile(r"\.php$"),  # file refs
)


@dataclass
class Segment:
    id: str
    kind: str
    start: int
    end: int
    source: str
    target: str = ""


@dataclass
class Manifest:
    source_file: str
    engine: str = ""
    segments: list[Segment] = field(default_factory=list)
    direction: str = "ru-en"
    dest: str = ""


class Translator(Protocol):
    def translate_batch(self, texts: list[str]) -> list[str]: ...


class IdentityTranslator:
    """Dry-run engine: returns sources unchanged (pipeline self-test)."""

    def translate_batch(self, texts: list[str]) -> list[str]:
        return list(texts)


class ArgosTranslator:
    """Offline OpenNMT translation (argostranslate plus the language
    package of the direction, e.g. translate-ru_en or translate-en_ru)."""

    def __init__(self, source: str = "ru", target: str = "en") -> None:
        try:
            import argostranslate.translate as argos  # pylint: disable=import-outside-toplevel
        except ImportError as error:
            raise SystemExit(f"argostranslate is not installed. Install it and the translate-{source}_{target} package") from error
        self._argos = argos
        self._source = source
        self._target = target

    def translate_batch(self, texts: list[str]) -> list[str]:
        return [self._argos.translate(text, self._source, self._target) for text in texts]


OLLAMA_PROMPTS = {
    "ru-en": "Translate this Russian UI/article text to English. Return ONLY the translation, no quotes or commentary:\n",
    "en-ru": ('Translate this English article text to Russian. Keep every HTML tag and attribute, and the text inside <span class="tt"> and <span class="cmd-line">, exactly as it is. Return ONLY the translation, no quotes or commentary:\n'),
}


class OllamaTranslator:
    """Local LLM via Ollama (needs `ollama serve` + a pulled model)."""

    def __init__(self, model: str, host: str = "http://127.0.0.1:11434", direction: str = "ru-en") -> None:
        self._model = model
        self._host = host.rstrip("/")
        self._prompt = OLLAMA_PROMPTS[direction]

    def translate_batch(self, texts: list[str]) -> list[str]:
        import urllib.request  # pylint: disable=import-outside-toplevel

        out = []
        for text in texts:
            payload = json.dumps(
                {
                    "model": self._model,
                    "prompt": self._prompt + text,
                    "stream": False,
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                self._host + "/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    body = json.loads(response.read().decode("utf-8"))
            except OSError as error:
                raise SystemExit(f"cannot reach Ollama at {self._host}: {error}") from error
            out.append(body.get("response", "").strip())
        return out


def make_translator(engine: str, model: str, direction: str = "ru-en") -> Translator:
    if engine == "identity":
        return IdentityTranslator()
    if engine == "argos":
        source, target = direction.split("-")
        return ArgosTranslator(source, target)
    if engine == "ollama":
        if not model:
            raise SystemExit("--engine ollama needs --model NAME")
        return OllamaTranslator(model, direction=direction)
    raise SystemExit(f"unknown engine {engine!r} (argos|ollama|identity|import)")


def find_ranges(pattern: re.Pattern[str], text: str) -> list[tuple[int, int]]:
    return [match.span() for match in pattern.finditer(text)]


def in_ranges(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in ranges)


INTERPOLATION = re.compile(r"\{\$[^{}]*\}|\$\{[^{}]*\}|\$[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]\n]*\]|->[A-Za-z_][A-Za-z0-9_]*)?")


def literal_parts(body: str, start: int) -> list[tuple[int, int, str]]:
    """The literal runs of an interpolated double-quoted body, variables left out."""
    parts = []
    position = 0
    for match in INTERPOLATION.finditer(body):
        if body[position : match.start()].strip():
            parts.append((start + position, start + match.start(), body[position : match.start()]))
        position = match.end()
    if body[position:].strip():
        parts.append((start + position, start + len(body), body[position:]))
    return parts


def quoted_spans(text: str, skip: list[tuple[int, int]], split_interpolated: bool = False) -> list[tuple[int, int, str]]:
    """Locate PHP single- and double-quoted string bodies (backslash-aware).

    A double-quoted body with $ interpolation is skipped whole, or, with
    split_interpolated, returned as its literal parts: the variables stay
    outside every span, so a translation cannot touch them.
    """
    spans = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if char == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if char == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if char in "'\"" and not in_ranges(i, skip):
            quote = char
            start = i + 1
            i += 1
            body = []
            closed = False
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    body.append(text[i : i + 2])
                    i += 2
                    continue
                if text[i] == quote:
                    closed = True
                    break
                body.append(text[i])
                i += 1
            if closed:
                joined = "".join(body)
                if quote == '"' and re.search(r"\$[A-Za-z_{]", joined):
                    if split_interpolated:
                        spans.extend(literal_parts(joined, start))
                else:
                    spans.append((start, i, joined))
            i += 1
            continue
        i += 1
    return spans


single_quoted_spans = quoted_spans


def never_translate(body: str) -> bool:
    if not CYRILLIC.search(body):
        return True
    if "{" in body and "}" in body:
        return True
    if "$" in body or "->" in body or "::" in body:
        return True
    stripped = body.strip()
    return any(pattern.search(stripped) for pattern in NEVER_TRANSLATE)


CODE_COMMENT_LINE = re.compile(r"(?:^|[\s;{}()])(?://(?!/)|(?:&#47;){2})[^\n]*")
CODE_COMMENT_HASH = re.compile(r"(?:^|(?<=\n))[ \t]*#(?!\s*(?:define|include|if|ifdef|ifndef|else|elif|endif|pragma|undef|error|line|import)\b)(?:[ \t]+|[А-Яа-яЁё])[^\n]*")
CODE_COMMENT_BLOCK = re.compile(r"(?:/\*|&#47;\*).*?(?:\*/|\*&#47;)", re.DOTALL)
URL_IN_COMMENT = re.compile(r"://|&#58;\s*/")


def attr_chunks(region: str, base: int) -> list[tuple[int, int, str, str]]:
    """Translatable alt/title attribute values (visible tooltips, accessibility)."""
    chunks = []
    for match in ATTR_VALUE.finditer(region):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        if value and CYRILLIC.search(value):
            start = base + match.start(1 if match.group(1) is not None else 2)
            chunks.append((start, start + len(value), value, "attr"))
    return chunks


def code_comment_chunks(token: str, token_start: int) -> list[tuple[int, int, str]]:
    """Russian comment text inside skipped code (//... and /*...*/); code itself untouched."""
    chunks = []
    for pattern in (CODE_COMMENT_LINE, CODE_COMMENT_HASH, CODE_COMMENT_BLOCK):
        for match in pattern.finditer(token):
            body = match.group(0)
            if URL_IN_COMMENT.search(body):
                continue
            if CYRILLIC.search(body):
                chunks.append((token_start + match.start(), token_start + match.end(), body))
    return chunks


def literal_contexts(text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]], list[tuple[int, int]]]:
    """Heredoc bodies, single-quoted bodies, double-quoted bodies (start, end) spans."""
    heredocs = [(match.start(3), match.end(3)) for match in HEREDOC.finditer(text)]
    singles: list[tuple[int, int]] = []
    doubles: list[tuple[int, int]] = []
    for start, end, _body in quoted_spans(text, list(heredocs), split_interpolated=True):
        # Only a double-quoted string is split into parts, and a part that
        # follows a variable starts after "}", "]" or a name, never after "'".
        probe = text[start - 1 : start]
        if probe == "'":
            singles.append((start, end))
        else:
            doubles.append((start, end))
    return heredocs, singles, doubles


def escape_for_context(target: str, start: int, heredocs, singles, doubles) -> str:
    """Escape quotes that would terminate the enclosing PHP literal (heredocs are safe)."""
    if any(s <= start < e for s, e in heredocs):
        return target
    if any(s <= start < e for s, e in singles):
        return re.sub(r"(?<!\\)'", r"\\'", target)
    if any(s <= start < e for s, e in doubles):
        # A "$name" the translation brings in would become a live variable.
        return re.sub(r"(?<!\\)\$(?=[A-Za-z_{])", r"\\$", re.sub(r'(?<!\\)"', r'\\"', target))
    return target


TREE_CHARS = re.compile(r"[\u2500-\u257f]|\|--|\+--|`--")
WORD_OF = {"ru": re.compile(r"[А-Яа-яЁё]{2,}"), "en": re.compile(r"[A-Za-z]{2,}")}
SYMBOL_RUN = re.compile(r"([^\w\s.])\1{3,}")
LOG_LINE = re.compile(r"^\s*(?:\w+ +\d+ \d\d:\d\d:\d\d|\d{4}-\d\d-\d\d[ T]\d\d:\d\d|(?:WARNING|ERROR|FATAL|INFO|DEBUG|Traceback)\b)", re.MULTILINE)
OUTPUT_CLASSES = {"err", "cout1", "pout1", "cmd1", "path", "shellscript", "fname"}


def tag_classes(tag: str) -> list[str]:
    classes = re.search(r"""class\s*=\s*["']([^"']*)["']""", tag)
    return classes.group(1).split() if classes else []


def wspw_prose(tag: str, content: str, language: str) -> bool:
    """A <p class="... wspw"> that holds prose (a list, a biography, a quoted
    docstring) and not a tree or program output: wspw is its only code class,
    it has no tree characters, and most of its lines are sentences."""
    names = tag_classes(tag)
    if "wspw" not in names or any(name in OUTPUT_CLASSES or name.endswith("code") for name in names):
        return False
    plain = html.unescape(TAG.sub(" ", content))
    if TREE_CHARS.search(plain):
        return False
    lines = [line for line in plain.splitlines() if line.strip()]
    sentences = [line for line in lines if len(WORD_OF[language].findall(line)) >= 3]
    if language == "en":
        # Program output is English too: ask for running text, lines that end
        # a sentence or a clause, and no banner or log line.
        if SYMBOL_RUN.search(plain) or LOG_LINE.search(plain):
            return False
        sentences = [line for line in sentences if line.rstrip()[-1:] in ".,:;!?"]
    return bool(lines) and len(sentences) * 2 >= len(lines)


def tag_class_skip(tag: str) -> bool:
    return any(name in SKIP_CLASSES for name in tag_classes(tag))


MERGE_INLINE_TAGS = {"span", "b", "i", "em", "strong", "u", "s", "sub", "sup", "small", "big", "font"}


def split_sentences_tag_aware(text: str) -> list[tuple[int, int]]:
    """Sentence spans that never start/end inside an <...> tag."""
    tag_spans = [match.span() for match in re.finditer(r"<[^<>]*>", text)]

    def inside_tag(pos: int) -> bool:
        return any(start <= pos < end for start, end in tag_spans)

    bounds = [0]
    for match in SENTENCE_END.finditer(text):
        if not inside_tag(match.start()):
            bounds.append(match.end())
    bounds.append(len(text))
    spans = []
    for start, end in zip(bounds, bounds[1:]):  # noqa: B905 - sliding window is intentionally offset by one
        if text[start:end].strip():
            spans.append((start, end))
    if len(spans) == 1 and len(text) > 800:
        spans = [(match.start(), match.end()) for match in re.finditer(r".{1,800}(?:\s+|$)", text, re.DOTALL) if match.group(0).strip() and not inside_tag(match.start())]
    return spans


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans relative to text (tags never split: caller passes text-only runs)."""
    return split_sentences_tag_aware(text)


def tag_name_of(token: str) -> str:
    name = re.match(r"</?\s*([A-Za-z0-9]+)", token)
    return name.group(1).lower() if name else ""


def html_text_chunks(html_body: str, base: int) -> list[tuple[int, int, str, str]]:  # pylint: disable=too-many-statements
    """Yield (start, end, text, kind) for translatable runs; tags untouched.

    Runs glued by inline spacer tags with no word boundary (e.g.
    ``Опе<span> </span>ра``) merge into one chunk so the engine sees
    whole words; all other markup stays out of chunks.
    """
    tokens = list(TAG_OR_TEXT.finditer(html_body))
    text_skip: dict[int, bool] = {}
    in_code: dict[int, bool] = {}
    skip_depth = 0
    code_depth = 0
    skip_p = False
    for pos, match in enumerate(tokens):
        token = match.group(0)
        if token.startswith("<"):
            tag = tag_name_of(token)
            closing = token.startswith("</")
            if tag in SKIP_TAGS:
                skip_depth += -1 if closing else 1
            if tag in {"code", "pre"}:
                code_depth += -1 if closing else 1
            if tag == "p" and not closing and tag_class_skip(token):
                close = P_CLOSE.search(html_body, match.end())
                content = html_body[match.end() : close.start() if close else len(html_body)]
                skip_p = not wspw_prose(token, content, "ru")
            elif tag == "p" and closing and skip_p:
                skip_p = False
        else:
            text_skip[pos] = skip_depth > 0 or skip_p
            in_code[pos] = code_depth > 0

    groups: list[list[int]] = []
    code_groups: list[tuple[int, int, str]] = []
    pos = 0
    while pos < len(tokens):
        if text_skip.get(pos, True) or tokens[pos].group(0).startswith("<"):
            if not tokens[pos].group(0).startswith("<") and in_code.get(pos, False):
                token = tokens[pos].group(0)
                for cstart, cend, part in code_comment_chunks(token, base + tokens[pos].start()):
                    code_groups.append((cstart, cend, part))
            pos += 1
            continue
        group = [pos]
        while True:
            look = group[-1] + 1
            while look < len(tokens):
                gap_token = tokens[look].group(0)
                if gap_token.startswith("<"):
                    if tag_name_of(gap_token) not in MERGE_INLINE_TAGS:
                        break
                elif gap_token.strip():
                    break
                look += 1
            if look >= len(tokens) or text_skip.get(look, True) or tokens[look].group(0).startswith("<"):
                break
            prev_text = tokens[group[-1]].group(0)
            next_text = tokens[look].group(0)
            if prev_text[-1:].isalpha() and next_text[:1].isalpha():
                group.append(look)
            else:
                break
        groups.append(group)
        pos = group[-1] + 1

    chunks: list[tuple[int, int, str, str]] = []
    chunks.extend((s, e, part, "codecomment") for s, e, part in code_groups)
    for group in groups:
        start = tokens[group[0]].start()
        end = tokens[group[-1]].end()
        raw = html_body[start:end]
        # Text only: a Cyrillic letter in an attribute (a highlighter class
        # such as "сd0d0d0") is not Russian prose.
        if not CYRILLIC.search(TAG.sub("", raw)):
            continue
        for rel_start, rel_end in split_sentences_tag_aware(raw):
            part = raw[rel_start:rel_end]
            if CYRILLIC.search(TAG.sub("", part)):
                chunks.append((base + start + rel_start, base + start + rel_end, part, "html"))
    return chunks


BLOCK_START = re.compile(r"<(p|li|h[1-6]|td|th|dt|dd)\b[^<>]*>", re.IGNORECASE)
CODE_ELEMENT = re.compile(r"<(pre|code|script|style)\b.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
INLINE_CODE = re.compile(r"""<span\b[^<>]*\bclass\s*=\s*["'](?:[^"']*\s)?(?:tt|cmd-line)(?:\s[^"']*)?["'][^<>]*>.*?</span>""", re.DOTALL)
EN_RU_SKIP_CLASSES = SKIP_CLASSES | {"path", "shellscript"}
AUTHOR_ARG = re.compile(r"author\s*:\s*\[[^\]]*\]")
ALT_TITLE = re.compile(r"""\b(alt|title)\s*=\s*(?:"[^"]*"|'[^']*')""")
VOID_TAGS = {"br", "img", "hr", "input", "wbr", "meta", "link", "source"}
SINGLE_WORD = re.compile(r"[A-Z][a-z]{2,}")  # a label such as Checklist or Hardware, or a name such as Linux


def block_end(body: str, tag: str, content_start: int) -> int:
    """End of a block's content; li, td, th, dt and dd may have no closing tag."""
    stops = {
        "li": r"<li\b|</li\s*>|</ul\s*>|</ol\s*>",
        "td": r"</td\s*>|<td\b|<th\b|</tr\s*>",
        "th": r"</th\s*>|<td\b|<th\b|</tr\s*>",
        "dt": r"</dt\s*>|<dt\b|<dd\b|</dl\s*>",
        "dd": r"</dd\s*>|<dt\b|<dd\b|</dl\s*>",
    }.get(tag, rf"</{tag}\s*>")
    match = re.compile(stops, re.IGNORECASE).search(body, content_start)
    return match.start() if match else -1


def en_ru_skip_class(tag: str) -> bool:
    return any(name in EN_RU_SKIP_CLASSES or name.endswith("code") for name in tag_classes(tag))


def has_english_prose(fragment: str) -> bool:
    """Latin words outside tags and inline code."""
    prose = re.sub(r"<[^<>]*>", " ", INLINE_CODE.sub(" ", fragment))
    return LATIN_WORD.search(html.unescape(prose)) is not None


def en_block_chunks(body: str, base: int) -> list[tuple[int, int, str, str]]:
    """Whole blocks with their inline markup, for en-ru.

    The translator sees each sentence with its links and inline code;
    verify checks that the markup and the inline code came back unchanged.
    A block that holds another block is left to its inner blocks.
    """
    chunks = []
    code_ranges = find_ranges(CODE_ELEMENT, body)
    for match in BLOCK_START.finditer(body):
        if in_ranges(match.start(), code_ranges):
            continue
        end = block_end(body, match.group(1).lower(), match.end())
        if end == -1:
            continue
        content = body[match.end() : end]
        if en_ru_skip_class(match.group(0)) and not wspw_prose(match.group(0), content, "en"):
            continue
        core = content.strip()
        if not core or BLOCK_START.search(content) or not has_english_prose(core):
            continue
        start = base + match.end() + len(content) - len(content.lstrip())
        chunks.append((start, start + len(core), core, "html"))
    return chunks


def english_meta(body: str) -> bool:
    """A PHP string with English words that is not an identifier, path, URL or code."""
    stripped = body.strip()
    if not LATIN_WORD.search(stripped) or CYRILLIC.search(stripped):
        return False
    if ("{" in body and "}" in body) or any(code in body for code in ("$", "->", "::", "\\")):
        return False
    if SINGLE_WORD.fullmatch(stripped):
        return True
    return not any(pattern.search(stripped) for pattern in NEVER_TRANSLATE)


def extract_segments_en_ru(source: Path) -> Manifest:
    text = source.read_text(encoding="utf-8")
    manifest = Manifest(source_file=str(source), direction="en-ru")
    found: list[tuple[int, int, str, str]] = []
    heredocs = []
    for match in HEREDOC.finditer(text):
        heredocs.append((match.start(), match.end()))
        found.extend(en_block_chunks(match.group(3), match.start(3)))
    authors = find_ranges(AUTHOR_ARG, text)
    for start, end, body in quoted_spans(text, heredocs):
        if "<" in body and ">" in body:
            found.extend(en_block_chunks(body, start))
        elif not in_ranges(start, authors) and english_meta(body):
            found.append((start, end, body, "meta"))
    for number, (start, end, body, kind) in enumerate(sorted(found)):
        manifest.segments.append(Segment(id=f"s{number:04d}", kind=kind, start=start, end=end, source=body))
    return manifest


def extract_segments(source: Path, direction: str = "ru-en") -> Manifest:
    if direction == "en-ru":
        return extract_segments_en_ru(source)
    text = source.read_text(encoding="utf-8")
    manifest = Manifest(source_file=str(source))
    counter = 0

    def add(kind: str, start: int, end: int, body: str) -> None:
        nonlocal counter
        if not body.strip():
            return
        manifest.segments.append(Segment(id=f"s{counter:04d}", kind=kind, start=start, end=end, source=body))
        counter += 1

    heredocs = []
    for match in HEREDOC.finditer(text):
        body_start = match.start(3)
        heredocs.append((match.start(), match.end()))
        for start, end, part, kind in html_text_chunks(match.group(3), body_start):
            add(kind, start, end, part)
        for start, end, value, kind in attr_chunks(match.group(3), body_start):
            add(kind, start, end, value)

    # A person's name is not prose: adapt_to_en and adapt_to_ru swap the pair.
    authors = find_ranges(AUTHOR_ARG, text)
    for start, end, body in quoted_spans(text, heredocs + authors, split_interpolated=True):
        if "<" in body and ">" in body:
            for chunk_start, chunk_end, part, kind in html_text_chunks(body, start):
                add(kind, chunk_start, chunk_end, part)
            for chunk_start, chunk_end, value, kind in attr_chunks(body, start):
                add(kind, chunk_start, chunk_end, value)
        elif not never_translate(body):
            add("meta", start, end, body)

    # Bare HTML outside any PHP block (an .inc.php menu, a top-path fragment):
    # the string walker above cannot see it.
    position = 0
    for match in PHP_BLOCK.finditer(text):
        if match.start() > position:
            for chunk_start, chunk_end, part, kind in html_text_chunks(text[position : match.start()], position):
                add(kind, chunk_start, chunk_end, part)
        position = match.end()
    if position < len(text):
        for chunk_start, chunk_end, part, kind in html_text_chunks(text[position:], position):
            add(kind, chunk_start, chunk_end, part)
    # Attribute values in bare HTML are already covered by the string walker;
    # two overlapping chunks would break render_output.
    manifest.segments.sort(key=lambda segment: segment.start)
    deduped: list[Segment] = []
    for segment in manifest.segments:
        if deduped and segment.start < deduped[-1].end:
            continue
        deduped.append(segment)
    manifest.segments = deduped
    return manifest


def manifest_path(workdir: Path) -> Path:
    return workdir / "segments.jsonl"


def dump_manifest(manifest: Manifest, workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    with manifest_path(workdir).open("w", encoding="utf-8") as handle:
        head = {"file": manifest.source_file, "engine": manifest.engine, "direction": manifest.direction, "dest": manifest.dest}
        handle.write(json.dumps(head, ensure_ascii=False) + "\n")
        for segment in manifest.segments:
            handle.write(
                json.dumps(
                    {
                        "id": segment.id,
                        "kind": segment.kind,
                        "start": segment.start,
                        "end": segment.end,
                        "source": segment.source,
                        "target": segment.target,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def load_manifest(workdir: Path) -> Manifest:
    lines = manifest_path(workdir).read_text(encoding="utf-8").splitlines()
    head = json.loads(lines[0])
    manifest = Manifest(source_file=head["file"], engine=head.get("engine", ""), direction=head.get("direction", "ru-en"), dest=head.get("dest", ""))
    for line in lines[1:]:
        row = json.loads(line)
        manifest.segments.append(
            Segment(
                id=row["id"],
                kind=row["kind"],
                start=row["start"],
                end=row["end"],
                source=row["source"],
                target=row.get("target", ""),
            )
        )
    return manifest


def carry_targets(old: Manifest, new: Manifest) -> int:
    """Give the chunks of a fresh extract the targets an earlier extract of the
    same page already has. Ids are positional, so a page edited after extract
    shifts them; the source text of a chunk is what identifies it."""
    if old.direction != new.direction or Path(old.source_file).resolve() != Path(new.source_file).resolve():
        return 0
    new.engine = old.engine
    pool: dict[tuple[str, str], list[str]] = {}
    for segment in old.segments:
        if segment.target:
            pool.setdefault((segment.kind, segment.source), []).append(segment.target)
    kept = 0
    for segment in new.segments:
        targets = pool.get((segment.kind, segment.source))
        if targets:
            segment.target = targets.pop(0)
            kept += 1
    return kept


def cmd_extract(args: argparse.Namespace) -> int:
    source = Path(args.file)
    workdir = Path(args.workdir)
    direction = getattr(args, "direction", "ru-en")
    out = getattr(args, "out", "")
    if direction == "en-ru" and not out:
        raise SystemExit("--direction en-ru needs --out PATH, the Russian twin to write")
    manifest = extract_segments(source, direction)
    manifest.dest = str(Path(out).resolve()) if out else ""
    kept = 0
    if manifest_path(workdir).is_file() and not getattr(args, "fresh", False):
        kept = carry_targets(load_manifest(workdir), manifest)
    dump_manifest(manifest, workdir)
    if kept:
        print(f"kept {kept} target(s) of the earlier extract (same source text); --fresh drops them")
    kinds: dict[str, int] = {}
    for segment in manifest.segments:
        kinds[segment.kind] = kinds.get(segment.kind, 0) + 1
    print(f"extracted {len(manifest.segments)} chunks from {source} {kinds}")
    print(f"work files in {workdir} (segments.jsonl). Review, then run translate.")
    return 0


def load_glossary(path: str | None, direction: str = "ru-en") -> dict[str, str]:
    """glossary.json serves ru-en; another direction reads glossary.<direction>.json when it exists."""
    name = "glossary.json" if direction == "ru-en" else f"glossary.{direction}.json"
    default = Path(__file__).resolve().parent / name
    chosen = Path(path) if path else default
    if not chosen.is_file():
        return {}
    data = json.loads(chosen.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"glossary {chosen} must be a JSON object")
    return {str(key): str(value) for key, value in data.items()}


def apply_glossary(text: str, glossary: dict[str, str]) -> str:
    for term, replacement in glossary.items():
        text = re.sub(r"\b" + re.escape(term) + r"\b", replacement, text)
    return text


COMMENT_MARKERS = ("//", "/*", "*/", "&#47;&#47;", "&#47;*", "*&#47;")


def strip_comment_markers(text: str) -> tuple[str, str, str]:
    """Split a code-comment chunk into (prefix, core, suffix) so the engine
    never sees comment markers (NMT models stutter on repeated slashes)."""
    leading_ws = text[: len(text) - len(text.lstrip())]
    trailing_ws = text[len(text.rstrip()) :]
    core = text.strip()
    prefix = ""
    while True:
        hit = False
        for marker in sorted(COMMENT_MARKERS, key=len, reverse=True):
            if core.startswith(marker):
                prefix += marker
                core = core[len(marker) :]
                stripped = core.lstrip()
                prefix += core[: len(core) - len(stripped)]
                core = stripped
                hit = True
                break
        if not hit:
            break
    suffix = ""
    while True:
        hit = False
        for marker in sorted(COMMENT_MARKERS, key=len, reverse=True):
            if core.endswith(marker):
                suffix = marker + suffix
                core = core[: -len(marker)].rstrip()
                hit = True
                break
        if not hit:
            break
    return leading_ws + prefix, core, suffix + trailing_ws


def load_targets(path: Path) -> dict[str, str]:
    """JSON lines {"id": "s0001", "target": "..."}, written by a person or an AI agent."""
    targets: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise SystemExit(f"{path}:{number}: not JSON: {error}") from error
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("target"), str):
            raise SystemExit(f'{path}:{number}: expected {{"id": "...", "target": "..."}}')
        targets[row["id"]] = row["target"]
    return targets


def write_translated(manifest: Manifest, workdir: Path) -> None:
    dump_manifest(manifest, workdir)
    (workdir / "translated.txt").write_text("\n".join(f"[{s.id}] {s.target}" for s in manifest.segments), encoding="utf-8")


def pad_target(segment: Segment, target: str) -> str:
    """Keep the source chunk's leading/trailing whitespace and, for a code
    comment, the comment marker: an import target is the text only."""
    if segment.kind == "codecomment":
        pre, _core, post = strip_comment_markers(segment.source)
        indent = pre[: len(pre) - len(pre.lstrip())]
        marker = pre.strip()
        body = target.lstrip()
        if marker and not body.startswith(marker):
            body = marker + " " + body
        return indent + body.rstrip() + post
    leading = segment.source[: len(segment.source) - len(segment.source.lstrip())]
    trailing = segment.source[len(segment.source.rstrip()) :]
    return leading + target.strip() + trailing


def reflow(segment: Segment, target: str) -> str:
    """Wrap a one-line target the way its source chunk is wrapped: at the
    source's longest line, with the indent of the source's second line.
    A tag is never broken, and a target that has its own line breaks is kept."""
    core = segment.source.strip()
    body = target.strip()
    if segment.kind != "html" or "\n" not in core or "\n" in body:
        return target
    lines = core.splitlines()
    width = max(len(line.rstrip()) for line in lines)
    indent = lines[1][: len(lines[1]) - len(lines[1].lstrip())]
    words = TAG.sub(lambda match: re.sub(r"\s", "\x00", match.group(0)), body).split()
    wrapped = [""]
    for word in words:
        if wrapped[-1].strip() and len(wrapped[-1]) + 1 + len(word) > width:
            wrapped.append(indent + word)
        else:
            wrapped[-1] += (" " if wrapped[-1].strip() else indent) + word
    wrapped[0] = wrapped[0][len(indent) :]
    leading = target[: len(target) - len(target.lstrip())]
    trailing = target[len(target.rstrip()) :]
    return leading + "\n".join(wrapped).replace("\x00", " ") + trailing


def import_targets(manifest: Manifest, workdir: Path, targets_file: str, wrap: bool = False) -> int:
    if not targets_file:
        raise SystemExit('--engine import needs --targets FILE (JSON lines {"id": ..., "target": ...})')
    imported = load_targets(Path(targets_file))
    unknown = sorted(set(imported) - {segment.id for segment in manifest.segments})
    if unknown:
        raise SystemExit(f"{targets_file} has ids that are not in segments.jsonl: {', '.join(unknown[:5])}")
    for segment in manifest.segments:
        if segment.id in imported:
            segment.target = pad_target(segment, imported[segment.id])
            if wrap:
                segment.target = reflow(segment, segment.target)
    missing = [segment.id for segment in manifest.segments if not segment.target]
    write_translated(manifest, workdir)
    print(f"imported {len(imported)} target(s); {len(missing)} chunk(s) still have none" + (f": {', '.join(missing[:5])}" if missing else ""))
    print("targets saved (segments.jsonl + translated.txt). Review, then run verify.")
    return 0


def cmd_translate(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    manifest = load_manifest(workdir)
    manifest.engine = args.engine
    if args.engine == "import":
        return import_targets(manifest, workdir, getattr(args, "targets", ""), getattr(args, "reflow", False))
    glossary = {} if args.no_glossary else load_glossary(args.glossary, manifest.direction)
    if glossary:
        print(f"glossary: {len(glossary)} term(s)")
    translator = make_translator(args.engine, args.model, manifest.direction)
    pending = [segment for segment in manifest.segments if not segment.target]
    stripped: list[str] = []
    padding: list[tuple[str, str]] = []
    for segment in pending:
        if segment.kind == "codecomment":
            pre, core, post = strip_comment_markers(segment.source)
        else:
            leading = segment.source[: len(segment.source) - len(segment.source.lstrip())]
            trailing = segment.source[len(segment.source.rstrip()) :]
            pre, core, post = leading, segment.source.strip(), trailing
        padding.append((pre, post))
        stripped.append(apply_glossary(core, glossary))
    sources = stripped
    for at in range(0, len(sources), 25):
        batch = sources[at : at + 25]
        targets = translator.translate_batch(batch)
        if len(targets) != len(batch):
            raise SystemExit("engine returned a short batch; aborting")
        for segment, target, (leading, trailing) in zip(pending[at : at + 25], targets, padding[at : at + 25], strict=True):
            segment.target = leading + target.strip() + trailing
            if getattr(args, "reflow", False):
                segment.target = reflow(segment, segment.target)
        print(f"translated {min(at + 25, len(sources))}/{len(sources)} chunks")
    write_translated(manifest, workdir)
    print("targets saved (segments.jsonl + translated.txt). Review, then run verify.")
    return 0


def render_output(manifest: Manifest) -> str:
    text = Path(manifest.source_file).read_text(encoding="utf-8")
    heredocs, singles, doubles = literal_contexts(text)
    for segment in sorted(manifest.segments, key=lambda s: s.start, reverse=True):
        if not segment.target:
            raise SystemExit(f"chunk {segment.id} has no translation; run translate first")
        if text[segment.start : segment.end] != segment.source:
            raise SystemExit(f"chunk {segment.id} does not match the current file: {manifest.source_file} changed since extract; re-extract first")
        if segment.kind == "attr":  # noqa: SIM108 - branch form reads better than a 200-char ternary
            rendered = segment.target.replace('"', "&quot;").replace("'", "&#39;")
        else:
            rendered = escape_for_context(segment.target, segment.start, heredocs, singles, doubles)
        text = text[: segment.start] + rendered + text[segment.end :]
    return text


def tags_of(text: str) -> list[str]:
    return re.findall(r"</?[A-Za-z][A-Za-z0-9]*", text)


def tag_signature(fragment: str) -> list[str]:
    """Sorted tags of a fragment; alt and title values may be translated."""
    return sorted(ALT_TITLE.sub(r'\1=""', tag) for tag in re.findall(r"<[^<>]*>", fragment))


def balanced(fragment: str) -> bool:
    stack: list[str] = []
    for closing, name, self_closing in re.findall(r"<(/?)([A-Za-z][A-Za-z0-9]*)[^<>]*?(/?)>", fragment):
        name = name.lower()
        if self_closing or name in VOID_TAGS:
            continue
        if not closing:
            stack.append(name)
        elif not stack or stack.pop() != name:
            return False
    return not stack


ALLOW_MANY = 5


def allow_pattern(allowed: list[str]) -> re.Pattern[str] | None:
    try:
        return re.compile("|".join(f"(?:{pattern})" for pattern in allowed)) if allowed else None
    except re.error as error:
        raise SystemExit(f"--allow is not a regular expression: {error}") from error


def allow_warnings(allowed: list[str], kept: list[str], what: str) -> list[str]:
    """A broad --allow pattern (".*") switches the check off for the whole
    page: say so when one pattern keeps more than a handful of items."""
    warnings = []
    for pattern in allowed:
        count = sum(1 for item in kept if re.search(pattern, item))
        if count > ALLOW_MANY:
            warnings.append(f"--allow {pattern!r} keeps {count} {what}: check that it is not too broad")
    return warnings


def check_placeholders_and_entities(segment: Segment) -> list[str]:
    for token in PLACEHOLDER.findall(segment.source):
        if token not in segment.target:
            return [f"chunk {segment.id} dropped placeholder {token}"]
    for entity in sorted(set(re.findall(r"&[A-Za-z0-9#]+;", segment.source))):
        if entity not in segment.target and html.unescape(entity) not in segment.target:
            return [f"chunk {segment.id} damaged entity {entity}"]
    return []


def check_en_ru_segments(manifest: Manifest, allowed: list[str] | None = None, warnings: list[str] | None = None) -> list[str]:
    """Every chunk is Russian now, with the tags and inline code of its source.

    A chunk that is rightly not Russian (a keyword list, an error message, a
    code sample) passes when an --allow pattern matches its target.
    """
    errors = []
    keep = allow_pattern(allowed or [])
    kept: list[str] = []
    for segment in manifest.segments:
        kept_name = segment.target == segment.source and SINGLE_WORD.fullmatch(segment.source.strip())
        if not CYRILLIC.search(segment.target) and not kept_name:
            if segment.target and keep and keep.search(segment.target):
                kept.append(segment.target)
            else:
                errors.append(f"chunk {segment.id} has no Russian text: {segment.target[:80]!r}")
        errors.extend(check_placeholders_and_entities(segment))
        if segment.kind != "html":
            continue
        if tag_signature(segment.source) != tag_signature(segment.target):
            errors.append(f"chunk {segment.id} changed its tags")
        elif sorted(INLINE_CODE.findall(segment.source)) != sorted(INLINE_CODE.findall(segment.target)):
            errors.append(f"chunk {segment.id} changed inline code (tt or cmd-line)")
        elif balanced(segment.source) and not balanced(segment.target):
            errors.append(f"chunk {segment.id} nests its tags wrongly")
    if warnings is not None:
        warnings.extend(allow_warnings(allowed or [], kept, "chunk(s) without Russian text"))
    return errors


# Author credits of the site: English name -> (Russian name, Russian about URL).
# Set as JSON in SITE_RU_AUTHORS / SITE_EN_AUTHORS; empty by default.
RU_AUTHORS: dict[str, tuple[str, str]] = {k: tuple(v) for k, v in json.loads(os.environ.get("SITE_RU_AUTHORS", "{}")).items()}
EN_AUTHORS: dict[str, tuple[str, str]] = {k: tuple(v) for k, v in json.loads(os.environ.get("SITE_EN_AUTHORS", "{}")).items()}
RU_ENGINE_USE = re.compile(r"^use\s+Site\\Ru\\(?:[A-Za-z0-9_]+\\)?Php\\", re.MULTILINE)
RU_INCLUDE = re.compile(r"""(\$root\s*\.\s*['"])/ru/(?:[a-z0-9_]+/)*\.php/""")
RU_PAGE_DIR = re.compile(r"""(\$page_dir\s*=\s*['"])([^'"]*)(['"])""")
EN_LANG = re.compile(r"""lang\s*:\s*['"]ru['"]""")
LEGACY_LANG = re.compile(r"""(\$page_lang\s*=\s*)['"]ru['"]""")
INTERNAL_URL = re.compile(r"""(href="|(?:previous|next)_lesson_url\s*:\s*')(/(?!/)[^"'\s]*)""")
FOLDER = r"(/[a-z0-9_/-]*/)"


def find_src_root(dest: Path) -> Path | None:
    for parent in dest.resolve().parents:
        if parent.name == "src" and (parent / "sitemap.xml").is_file():
            return parent
    return None


def load_folder_pairs(doc: Path) -> list[tuple[str, str]]:
    """English folder -> Russian folder from the first section of en-ru-folders.md, longest first."""
    if not doc.is_file():
        return []
    text = doc.read_text(encoding="utf-8")
    section = text.split("## English folder to Russian folder", 1)[-1].split("\n## ", 1)[0]
    pairs = re.findall(rf"^\|\s*`{FOLDER}`\s*\|\s*`(/ru{FOLDER})`", section, re.MULTILINE)
    pairs = [(english, russian) for english, russian, _rest in pairs]
    pairs += re.findall(rf"`{FOLDER}`\s+and\s+`(/ru/[a-z0-9_/-]*/)`", section)
    return sorted(set(pairs), key=lambda pair: len(pair[0]), reverse=True)


def sitemap_paths(src_root: Path) -> set[str]:
    text = (src_root / "sitemap.xml").read_text(encoding="utf-8")
    return {urlparse(location).path for location in re.findall(r"<loc>([^<]+)</loc>", text)}


def ru_twin(url: str, pairs: list[tuple[str, str]], known: set[str]) -> str | None:
    """The Russian page for an English URL, or None when the sitemap has none."""
    path, hash_mark, fragment = url.partition("#")
    if path.startswith("/ru/"):
        return url
    candidate = next((russian + path[len(english) :] for english, russian in pairs if path.startswith(english)), "/ru" + path)
    return candidate + hash_mark + fragment if candidate in known else None


def adapt_to_ru(text: str, dest: Path) -> tuple[str, list[str]]:
    """Turn a rendered English page into its Russian twin at dest; return the text and warnings."""
    src_root = find_src_root(dest)
    if src_root is None:
        return text, [f"{dest} is not below a src/ folder with sitemap.xml: paths and links not adapted"]
    warnings: list[str] = []
    pairs = load_folder_pairs(src_root.parent / "docs" / "en-ru-folders.md")
    known = sitemap_paths(src_root)
    page_dir = "/" + dest.resolve().parent.relative_to(src_root).as_posix() + "/"
    text = text.replace("lang: 'en'", "lang: 'ru'").replace("use Site\\Php\\", "use Site\\Ru\\Php\\").replace("$root . '/.php/", "$root . '/ru/.php/")
    text = re.sub(r"(\$page_dir\s*=\s*')[^']*(')", lambda match: match.group(1) + page_dir + match.group(2), text)
    related = dest.parent / "RelatedArticles.php"
    namespace = re.search(r"^namespace\s+([^;]+);", related.read_text(encoding="utf-8"), re.MULTILINE) if related.is_file() else None
    if namespace:
        text = re.sub(r"^use\s+[^;]*\\RelatedArticles;", lambda _match: f"use {namespace.group(1)}\\RelatedArticles;", text, flags=re.MULTILINE)
    elif "RelatedArticles" in text:
        warnings.append(f"no namespace in {related}: the RelatedArticles use line is unchanged")

    def ru_author(match: re.Match[str]) -> str:
        names = re.findall(r"'([^']*)'", match.group(0))
        if names and names[0] in RU_AUTHORS:
            name, url = RU_AUTHORS[names[0]]
            return f"author: ['{name}', '{url}']"
        warnings.append(f"no Russian author for {match.group(0)}")
        return match.group(0)

    text = AUTHOR_ARG.sub(ru_author, text)
    source_text = text

    def relink(match: re.Match[str]) -> str:
        twin = ru_twin(match.group(2), pairs, known)
        if twin is None:
            line = source_text.count("\n", 0, match.start()) + 1
            warnings.append(f"L{line}: no Russian twin of {match.group(2)} in the sitemap, link unchanged")
            return match.group(0)
        return match.group(1) + twin

    return INTERNAL_URL.sub(relink, text), warnings


def english_twin(url: str, mirror: list[tuple[str, str]], known: set[str]) -> str | None:
    """The English page for a Russian URL, or None when the sitemap has none."""
    path, hash_mark, fragment = url.partition("#")
    if not path.startswith("/ru/"):
        return url
    candidate = path
    for russian, english in mirror:
        if path == russian or path.startswith(russian):
            candidate = english + path[len(russian) :]
            break
    return candidate + hash_mark + fragment if candidate in known else None


RELATED_REQUIRE = re.compile(
    r"(?:require|require_once|include|include_once)\s*\(?\s*([^;\n]*RelatedArticles\.php[^;\n]*)",
    re.IGNORECASE,
)
RELATED_LITERAL = re.compile(r"""['"]([^'"]*RelatedArticles\.php)['"]""")


def required_related_file(text: str, source: Path, src_root: Path) -> Path:
    """The RelatedArticles.php the page actually requires, else $page_dir's, else the sibling.

    A page may live in one folder while $page_dir points at another; taking the
    namespace from $page_dir alone pointed the `use` line at a class the page
    never loads (seen on linux/debian/network/new_interface.php and the iperf
    pages). The require_once path is the authority.
    """
    page_dir_arg = re.search(r"page_dir\s*:\s*['\"]([^'\"]*)['\"]", text)
    page_dir_var = re.search(r"\$page_dir\s*=\s*['\"]([^'\"]*)['\"]", text)
    found = page_dir_arg or page_dir_var
    folder = found.group(1) if found else ""

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("#", "//", "*", "/*")) or "RelatedArticles.php" not in line:
            continue
        statement = RELATED_REQUIRE.search(line)
        literal = RELATED_LITERAL.search(statement.group(1) if statement else line)
        if not literal:
            continue
        candidate = literal.group(1)
        if "$page_dir" in line:
            if not folder:
                continue
            candidate = folder.rstrip("/") + "/" + candidate.lstrip("/")
        if not candidate.startswith("/"):
            continue
        path = src_root / candidate.strip("/")
        if path.is_file():
            return path

    if folder:
        path = src_root / folder.strip("/") / "RelatedArticles.php"
        if path.is_file():
            return path
    return source.parent / "RelatedArticles.php"


def adapt_to_en(text: str, source: Path) -> tuple[str, list[str]]:
    """Turn a translated Russian page into an English one in place; return the text and warnings.

    The mirror of adapt_to_ru: lang, English engine includes and $page_dir,
    the RelatedArticles namespace of the page's own folder, the author's
    English name, and /ru/ links mapped to their English twin.
    """
    src_root = find_src_root(source)
    if src_root is None:
        return text, [f"{source} is not below a src/ folder with sitemap.xml: paths and links not adapted"]
    warnings: list[str] = []
    pairs = load_folder_pairs(src_root.parent / "docs" / "en-ru-folders.md")
    mirror = sorted(((russian, english) for english, russian in pairs), key=lambda pair: len(pair[0]), reverse=True)
    known = sitemap_paths(src_root)

    text = EN_LANG.sub("lang: 'en'", text)
    text = LEGACY_LANG.sub(lambda match: match.group(1) + "'en'", text)
    text = RU_ENGINE_USE.sub(lambda _match: "use Site\\Php\\", text)
    text = RU_INCLUDE.sub(lambda match: match.group(1) + "/.php/", text)

    source_text = text

    def page_dir(match: re.Match[str]) -> str:
        current = match.group(2)
        if not current.startswith("/ru/"):
            return match.group(0)
        twin = None
        for russian, english in mirror:
            if current == russian or current.startswith(russian):
                twin = english + current[len(russian) :]
                break
        if twin is None:
            twin = current[len("/ru") :]
        if (src_root / twin.strip("/")).is_dir():
            return match.group(1) + twin + match.group(3)
        own = "/" + source.resolve().parent.relative_to(src_root).as_posix() + "/"
        if (src_root / own.strip("/")).is_dir():
            warnings.append(f"no English twin of page_dir {current}, using {own}")
            return match.group(1) + own + match.group(3)
        warnings.append(f"no English twin of page_dir {current}, left as it is")
        return match.group(0)

    text = RU_PAGE_DIR.sub(page_dir, text)

    # The page's own require says which section file it needs. Fall back to
    # $page_dir and then to the sibling: a page may sit in one folder while
    # $page_dir points at another (the iperf/new_interface pages do).
    related = required_related_file(text, source, src_root)
    related_text = related.read_text(encoding="utf-8") if related.is_file() else ""
    namespace = re.search(r"^namespace\s+([^;]+);", related_text, re.MULTILINE) if related_text else None
    has_class = re.search(r"^class\s+RelatedArticles\b", related_text, re.MULTILINE) is not None
    if namespace and not has_class:
        warnings.append(f"{related} defines no class RelatedArticles: the use line is unchanged")
    elif namespace:
        text = re.sub(r"^use\s+[^;]*\\RelatedArticles;", lambda _match: f"use {namespace.group(1)}\\RelatedArticles;", text, flags=re.MULTILINE)
    elif "RelatedArticles" in text:
        warnings.append(f"no namespace in {related}: the RelatedArticles use line is unchanged")

    def en_author(match: re.Match[str]) -> str:
        names = re.findall(r"'([^']*)'", match.group(0))
        if names and names[0] in EN_AUTHORS:
            name, url = EN_AUTHORS[names[0]]
            if url in known or (src_root / url.strip("/")).is_dir():
                return f"author: ['{name}', '{url}']"
            warnings.append(f"no English about page for {url}: author unchanged")
            return match.group(0)
        warnings.append(f"no English author for {match.group(0)}")
        return match.group(0)

    text = AUTHOR_ARG.sub(en_author, text)

    def relink(match: re.Match[str]) -> str:
        twin = english_twin(match.group(2), mirror, known)
        if twin is None:
            line = source_text.count("\n", 0, match.start()) + 1
            warnings.append(f"L{line}: no English twin of {match.group(2)} in the sitemap, link unchanged")
            return match.group(0)
        return match.group(1) + twin

    return INTERNAL_URL.sub(relink, text), warnings


INLINE_TAG = re.compile(r"</?([A-Za-z0-9]+)\b[^<>]*>")
SPACING_TAGS = {"a", "b", "i", "em", "strong", "span", "code", "tt", "sup", "sub"}
OPEN_PUNCT = '([{\u00ab\u201e"'
CLOSE_PUNCT = ")]}\u00bb.,;:!?"


def adjacency(text: str) -> dict[str, dict[str, int]]:
    """How many inline tags are preceded/followed by a space, an opening or a
    closing punctuation mark."""
    counts = {"before": {"space": 0, "open": 0, "close": 0}, "after": {"space": 0, "open": 0, "close": 0}}
    for match in INLINE_TAG.finditer(text):
        if match.group(1).lower() not in SPACING_TAGS:
            continue
        closing = match.group(0).startswith("</")
        char = text[match.start() - 1 : match.start()] if not closing else text[match.end() : match.end() + 1]
        side = "after" if closing else "before"
        if char.isspace():
            counts[side]["space"] += 1
        elif char in OPEN_PUNCT:
            counts[side]["open"] += 1
        elif char in CLOSE_PUNCT:
            counts[side]["close"] += 1
    return counts


def check_tag_spacing(segment: Segment) -> list[str]:
    """A translation must not glue a word to an inline tag or drop the
    punctuation the source has there: ``page<a>`` instead of ``page <a>``,
    ``</a>with``, ``(...)`` around a link."""
    if segment.kind not in {"html", "attr", "meta"}:
        return []
    source = adjacency(segment.source)
    target = adjacency(segment.target)
    errors = []
    for side in ("before", "after"):
        for kind in ("space", "open", "close"):
            if target[side][kind] < source[side][kind]:
                errors.append(f"chunk {segment.id} lost the {kind} {side} an inline tag ({source[side][kind]} in the source, {target[side][kind]} in the target)")
                return errors
    return errors


EDGE_PUNCT = ",.;:!?)]}»"


def check_chunk_edges(segment: Segment) -> list[str]:
    """A chunk that starts (or ends) with punctuation keeps it: a sentence cut
    before a link may start with "," which the target must not drop."""
    if segment.kind not in {"html", "meta", "attr"}:
        return []
    source = segment.source.strip()
    target = segment.target.strip()
    # "&#35;" ends with a semicolon that is part of the entity, not punctuation.
    source = re.sub(r"(&[A-Za-z0-9#]+;)+$", "", source)
    errors = []
    if source[:1] in EDGE_PUNCT and target[:1] not in EDGE_PUNCT:
        errors.append(f"chunk {segment.id} dropped the leading {source[0]!r} of the source")
    # A trailing period may legitimately end a sentence in English; a comma
    # or a semicolon must survive, as in "…, ставить перед началом".
    if source[-1:] in ",;" and target[-1:] not in ",;":
        errors.append(f"chunk {segment.id} dropped the trailing {source[-1]!r} of the source")
    return errors


def verify_report(workdir: Path, out_path: Path, errors: list[str], warnings: list[str]) -> None:
    report = {"output": str(out_path), "errors": errors, "warnings": warnings}
    (workdir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for warning in warnings:
        print(" warning:", warning)
    if errors:
        print(f"verify FAILED with {len(errors)} problem(s):")
        for error in errors:
            print(" -", error.splitlines()[0])


def php_lint(path: Path) -> list[str]:
    proc = subprocess.run(["php", "-l", str(path)], capture_output=True, text=True, check=False)
    return [] if proc.returncode == 0 else ["php -l failed:\n" + proc.stdout + proc.stderr]


def write_output(out_path: Path, text: str, source_file: str) -> None:
    """Write the rendered page with the source's own line endings (CRLF kept)."""
    try:
        head = Path(source_file).read_bytes()[:4000]
    except OSError:
        head = b""
    if b"\r\n" in head:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    out_path.write_text(text, encoding="utf-8", newline="")


def verify_en_ru(manifest: Manifest, workdir: Path, allowed: list[str]) -> int:
    allow_notes: list[str] = []
    errors = check_en_ru_segments(manifest, allowed, allow_notes)
    output, warnings = adapt_to_ru(render_output(manifest), Path(manifest.dest))
    warnings += allow_notes
    out_path = workdir / "output.php"
    write_output(out_path, output, manifest.source_file)
    errors += php_lint(out_path)
    if sorted(tags_of(Path(manifest.source_file).read_text(encoding="utf-8"))) != sorted(tags_of(output)):
        errors.append("HTML tag multiset changed (markup damaged)")
    verify_report(workdir, out_path, errors, warnings)
    if errors:
        return 1
    print(f"verify OK: {len(manifest.segments)} chunks in Russian, markup and inline code intact, {len(warnings)} warning(s); output for {manifest.dest}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    manifest = load_manifest(workdir)
    allowed = list(getattr(args, "allow", []) or [])
    if manifest.direction == "en-ru":
        return verify_en_ru(manifest, workdir, allowed)
    output = render_output(manifest)
    output, warnings = adapt_to_en(output, Path(manifest.source_file))
    out_path = workdir / "output.php"
    write_output(out_path, output, manifest.source_file)
    errors = php_lint(out_path)

    # HTML comments are invisible to the reader: keep them as they are.
    # A class name is not text either: a highlighter class may hold a Cyrillic letter.
    visible = re.sub(r"<!--.*?-->", lambda match: "\n" * match.group(0).count("\n"), output, flags=re.DOTALL)
    visible = CLASS_ATTR.sub("", visible)
    leftovers = [(num, line.strip()[:120]) for num, line in enumerate(visible.splitlines(), 1) if CYRILLIC.search(line)]
    keep = allow_pattern(allowed)
    if keep:
        warnings += allow_warnings(allowed, [item[1] for item in leftovers if keep.search(item[1])], "line(s) with Cyrillic")
        leftovers = [item for item in leftovers if not keep.search(item[1])]
    if leftovers:
        errors.append(f"{len(leftovers)} lines still contain Cyrillic, e.g. L{leftovers[0][0]}: {leftovers[0][1]}")

    original = Path(manifest.source_file).read_text(encoding="utf-8")
    if sorted(tags_of(original)) != sorted(tags_of(output)):
        errors.append("HTML tag multiset changed (markup damaged)")

    for segment in manifest.segments:
        errors.extend(check_placeholders_and_entities(segment))
        errors.extend(check_tag_spacing(segment))
        errors.extend(check_chunk_edges(segment))

    verify_report(workdir, out_path, errors, warnings)
    if errors:
        return 1
    print(f"verify OK: {len(manifest.segments)} chunks, markup intact, no Cyrillic left, {len(warnings)} warning(s); English includes and links adapted.")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    if not args.yes:
        raise SystemExit("refusing to overwrite without --yes")
    manifest = load_manifest(workdir)
    report_path = workdir / "report.json"
    if not report_path.is_file():
        raise SystemExit("run verify first")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("errors"):
        raise SystemExit("verify reported errors; refusing to apply")
    destination = Path(manifest.dest or manifest.source_file)
    if manifest.dest and destination.exists() and not getattr(args, "force", False):
        raise SystemExit(f"{destination} exists; pass --force to replace it (a backup is kept)")
    if not destination.parent.is_dir():
        raise SystemExit(f"folder {destination.parent} does not exist")
    backup_dir = workdir / "backup"
    if destination.exists():
        backup_dir.mkdir(exist_ok=True)
        shutil.copy2(destination, backup_dir / destination.name)
    out_path = workdir / "output.php"
    staging = destination.with_name(f".{destination.name}.tmp")
    shutil.copyfile(out_path, staging)
    os.replace(staging, destination)
    kept = f" (backup in {backup_dir})" if backup_dir.is_dir() else ""
    print(f"applied {out_path} -> {destination}{kept}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    get = sub.add_parser("extract", help="copy source into workdir as chunks")
    get.add_argument("file")
    get.add_argument("--workdir", required=True)
    get.add_argument("--direction", default="ru-en", choices=DIRECTIONS)
    get.add_argument("--out", default="", help="en-ru: the Russian page to write")
    get.add_argument("--fresh", action="store_true", help="drop the targets an earlier extract in this workdir already has")

    put = sub.add_parser("translate", help="translate chunks via engine")
    put.add_argument("--workdir", required=True)
    put.add_argument("--engine", default="identity", choices=["argos", "ollama", "identity", "import"])
    put.add_argument("--model", default="")
    put.add_argument("--glossary", default="")
    put.add_argument("--no-glossary", action="store_true")
    put.add_argument("--targets", default="", help='import: JSON lines {"id": ..., "target": ...}')
    put.add_argument("--reflow", action="store_true", help="wrap a one-line target like its source chunk (line width and indent); not for a wspw block, where line breaks show")

    check = sub.add_parser("verify", help="render output and check it")
    check.add_argument("--workdir", required=True)
    check.add_argument(
        "--allow",
        action="append",
        default=[],
        help="regex of a line to keep as it is (real program output, a Russian URL); en-ru: of a chunk that rightly stays not Russian (a keyword list, an error message); repeatable",
    )

    do = sub.add_parser("apply", help="write the verified output to the destination")
    do.add_argument("--workdir", required=True)
    do.add_argument("--yes", action="store_true")
    do.add_argument("--force", action="store_true", help="en-ru: replace an existing destination")

    args = parser.parse_args(argv)
    if args.command == "extract":
        return cmd_extract(args)
    if args.command == "translate":
        return cmd_translate(args)
    if args.command == "verify":
        return cmd_verify(args)
    return cmd_apply(args)


if __name__ == "__main__":
    sys.exit(main())
