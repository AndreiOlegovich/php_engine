"""Russian-to-English article translation pipeline (chunk by chunk).

Stages: extract -> translate -> verify -> apply.

The script never touches markup, code, URLs, or placeholders: it splits
only human-readable text into sentence-sized chunks (HTML tags, <code>,
command/output blocks and PHP code are byte-preserved), stores them as
JSONL work files, translates each chunk through a pluggable engine
(Argos, Ollama, or identity for dry runs), and writes the destination
file only on explicit ``apply --yes`` after verification passes.

Usage:
    python3 ru_en_translate.py extract PATH --workdir DIR
    python3 ru_en_translate.py translate --workdir DIR --engine argos
    python3 ru_en_translate.py verify --workdir DIR
    python3 ru_en_translate.py apply --workdir DIR --yes
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
HEREDOC = re.compile(r"<<<('?)([A-Za-z_][A-Za-z0-9_]*)\1\n(.*?)\n\2;", re.DOTALL)
TAG_OR_TEXT = re.compile(r"<[^<>]*>|[^<>]+")
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[A-ZА-ЯЁ«\"“(\-])")
PLACEHOLDER = re.compile(r"\{[A-Za-z0-9_]+\}")
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


class Translator(Protocol):
    def translate_batch(self, texts: list[str]) -> list[str]: ...


class IdentityTranslator:
    """Dry-run engine: returns sources unchanged (pipeline self-test)."""

    def translate_batch(self, texts: list[str]) -> list[str]:
        return list(texts)


class ArgosTranslator:
    """Offline OpenNMT translation (pip install argostranslate +
    argosmodel argos-translate-ru-en)."""

    def __init__(self, source: str = "ru", target: str = "en") -> None:
        try:
            import argostranslate.translate as argos  # pylint: disable=import-outside-toplevel
        except ImportError as error:
            raise SystemExit("argostranslate is not installed. Run: pip install argostranslate && argosmodel install translate-ru-en") from error
        self._argos = argos
        self._source = source
        self._target = target

    def translate_batch(self, texts: list[str]) -> list[str]:
        return [self._argos.translate(text, self._source, self._target) for text in texts]


class OllamaTranslator:
    """Local LLM via Ollama (needs `ollama serve` + a pulled model)."""

    def __init__(self, model: str, host: str = "http://127.0.0.1:11434") -> None:
        self._model = model
        self._host = host.rstrip("/")

    def translate_batch(self, texts: list[str]) -> list[str]:
        import urllib.request  # pylint: disable=import-outside-toplevel

        out = []
        for text in texts:
            payload = json.dumps(
                {
                    "model": self._model,
                    "prompt": ("Translate this Russian UI/article text to English. Return ONLY the translation, no quotes or commentary:\n" + text),
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


def make_translator(engine: str, model: str) -> Translator:
    if engine == "identity":
        return IdentityTranslator()
    if engine == "argos":
        return ArgosTranslator()
    if engine == "ollama":
        if not model:
            raise SystemExit("--engine ollama needs --model NAME")
        return OllamaTranslator(model)
    raise SystemExit(f"unknown engine {engine!r} (argos|ollama|identity)")


def find_ranges(pattern: re.Pattern[str], text: str) -> list[tuple[int, int]]:
    return [match.span() for match in pattern.finditer(text)]


def in_ranges(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in ranges)


def quoted_spans(text: str, skip: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
    """Locate PHP single- and double-quoted string bodies (backslash-aware).

    Double-quoted bodies with $ interpolation are skipped: translating
    around live variables is unsafe.
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
                    pass
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
    for pattern in (CODE_COMMENT_LINE, CODE_COMMENT_BLOCK):
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
    for start, end, _body in quoted_spans(text, list(heredocs)):
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
        return re.sub(r'(?<!\\)"', r'\\"', target)
    return target


def tag_class_skip(tag: str) -> bool:
    classes = re.search(r"""class\s*=\s*["']([^"']*)["']""", tag)
    if not classes:
        return False
    return any(name in SKIP_CLASSES for name in classes.group(1).split())


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
                skip_p = True
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
        if not CYRILLIC.search(raw):
            continue
        for rel_start, rel_end in split_sentences_tag_aware(raw):
            part = raw[rel_start:rel_end]
            if CYRILLIC.search(part):
                chunks.append((base + start + rel_start, base + start + rel_end, part, "html"))
    return chunks


def extract_segments(source: Path) -> Manifest:
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

    for start, end, body in single_quoted_spans(text, heredocs):
        if "<" in body and ">" in body:
            for chunk_start, chunk_end, part, kind in html_text_chunks(body, start):
                add(kind, chunk_start, chunk_end, part)
            for chunk_start, chunk_end, value, kind in attr_chunks(body, start):
                add(kind, chunk_start, chunk_end, value)
        elif not never_translate(body):
            add("meta", start, end, body)
    manifest.segments.sort(key=lambda segment: segment.start)
    return manifest


def manifest_path(workdir: Path) -> Path:
    return workdir / "segments.jsonl"


def dump_manifest(manifest: Manifest, workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    with manifest_path(workdir).open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"file": manifest.source_file, "engine": manifest.engine}) + "\n")
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
    manifest = Manifest(source_file=head["file"], engine=head.get("engine", ""))
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


def cmd_extract(args: argparse.Namespace) -> int:
    source = Path(args.file)
    workdir = Path(args.workdir)
    manifest = extract_segments(source)
    dump_manifest(manifest, workdir)
    kinds: dict[str, int] = {}
    for segment in manifest.segments:
        kinds[segment.kind] = kinds.get(segment.kind, 0) + 1
    print(f"extracted {len(manifest.segments)} chunks from {source} {kinds}")
    print(f"work files in {workdir} (segments.jsonl). Review, then run translate.")
    return 0


def load_glossary(path: str | None) -> dict[str, str]:
    default = Path(__file__).resolve().parent / "glossary.json"
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


def cmd_translate(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    manifest = load_manifest(workdir)
    manifest.engine = args.engine
    glossary = {} if args.no_glossary else load_glossary(args.glossary)
    if glossary:
        print(f"glossary: {len(glossary)} term(s)")
    translator = make_translator(args.engine, args.model)
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
        print(f"translated {min(at + 25, len(sources))}/{len(sources)} chunks")
    dump_manifest(manifest, workdir)
    (workdir / "translated.txt").write_text("\n".join(f"[{s.id}] {s.target}" for s in manifest.segments), encoding="utf-8")
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


def cmd_verify(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    manifest = load_manifest(workdir)
    output = render_output(manifest)
    lang_flipped = False
    if "lang: 'ru'" in output and not CYRILLIC.search(output):
        output = output.replace("lang: 'ru'", "lang: 'en'")
        lang_flipped = True
    out_path = workdir / "output.php"
    out_path.write_text(output, encoding="utf-8")
    errors = []

    proc = subprocess.run(["php", "-l", str(out_path)], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        errors.append("php -l failed:\n" + proc.stdout + proc.stderr)

    leftovers = [(num, line.strip()[:120]) for num, line in enumerate(output.splitlines(), 1) if CYRILLIC.search(line)]
    if leftovers:
        errors.append(f"{len(leftovers)} lines still contain Cyrillic, e.g. L{leftovers[0][0]}: {leftovers[0][1]}")

    original = Path(manifest.source_file).read_text(encoding="utf-8")
    if sorted(tags_of(original)) != sorted(tags_of(output)):
        errors.append("HTML tag multiset changed (markup damaged)")

    for segment in manifest.segments:
        for token in PLACEHOLDER.findall(segment.source):
            if token not in segment.target:
                errors.append(f"chunk {segment.id} dropped placeholder {token}")
                break
        for entity in set(re.findall(r"&[A-Za-z0-9#]+;", segment.source)):
            if entity not in segment.target and html.unescape(entity) not in segment.target:
                errors.append(f"chunk {segment.id} damaged entity {entity}")
                break

    report = {"output": str(out_path), "errors": errors}
    (workdir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if errors:
        print(f"verify FAILED with {len(errors)} problem(s):")
        for error in errors:
            print(" -", error.splitlines()[0])
        return 1
    flipped = ", lang flipped ru->en" if lang_flipped else ""
    print(f"verify OK: {len(manifest.segments)} chunks, markup intact, no Cyrillic left{flipped}.")
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
    destination = Path(manifest.source_file)
    backup_dir = workdir / "backup"
    backup_dir.mkdir(exist_ok=True)
    shutil.copy2(destination, backup_dir / destination.name)
    out_path = workdir / "output.php"
    shutil.copy2(out_path, destination)
    print(f"applied {out_path} -> {destination} (backup in {backup_dir})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    get = sub.add_parser("extract", help="copy source into workdir as chunks")
    get.add_argument("file")
    get.add_argument("--workdir", required=True)

    put = sub.add_parser("translate", help="translate chunks via engine")
    put.add_argument("--workdir", required=True)
    put.add_argument("--engine", default="identity", choices=["argos", "ollama", "identity"])
    put.add_argument("--model", default="")
    put.add_argument("--glossary", default="")
    put.add_argument("--no-glossary", action="store_true")

    check = sub.add_parser("verify", help="render output and check it")
    check.add_argument("--workdir", required=True)

    do = sub.add_parser("apply", help="overwrite destination with verified output")
    do.add_argument("--workdir", required=True)
    do.add_argument("--yes", action="store_true")

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
