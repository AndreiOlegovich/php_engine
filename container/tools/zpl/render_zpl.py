"""Render <p class="zplcode"> samples via the Labelary API and embed the PNG.

For each page: extract zplcode blocks, strip markup to raw ZPL, render
through api.labelary.com (8dpmm), save to the page directory's img/
folder, and insert an <img> right after the block. Idempotent: blocks
already followed by a rendered image are skipped.

Label size is derived per block so small tags do not drown in whitespace:
explicit ^PW/^LL win, otherwise the content extent is estimated from ^FO/^FT
coordinates plus field heights. Every render is checked for content touching
the label edge; the size is bumped and retried (up to 3 attempts) on clipping.

Usage:
    python3 tools/zpl/render_zpl.py --page src/labels/zpl_01.php --apply
    python3 tools/zpl/render_zpl.py --all --apply
    python3 render_zpl.py --all --apply --size 4x6   # force one size for all
Without --apply only reports what would be done (including derived sizes).
"""

from __future__ import annotations

import argparse
import html
import math
import re
import struct
import sys
import time
import urllib.request
import zlib
from pathlib import Path

ZPLCODE = re.compile(r'(<p class="zplcode">)(.*?)(</p>)', re.DOTALL)
TAG = re.compile(r"<[^<>]*>")
RENDERED_MARKER = "data-zpl-render"
LABELARY = "http://api.labelary.com/v1/printers/8dpmm/labels/{w}x{h}/0/"

#: 8 dots per mm -> dots per inch.
DOTS_PER_INCH = 203.2
#: fallback when nothing can be estimated.
DEFAULT_SIZE = (4, 6)
#: extra dots below the lowest field so descenders never kiss the edge.
BOTTOM_MARGIN_DOTS = 80
#: default text height when no font command is in scope.
DEFAULT_FONT_HEIGHT = 40


def block_to_zpl(inner: str) -> str:
    text = inner.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = TAG.sub("", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _font_height(segment: str, current: int) -> int:
    """Height of the active font inside one ^FO..^FS segment."""
    match = re.search(r"\^A\w+,(\d+)", segment)
    if match:
        return int(match.group(1))
    match = re.search(r"\^CF\w?,(\d+)", segment)
    if match:
        return int(match.group(1))
    return current


def estimate_size(zpl: str) -> tuple[int, int]:
    """Derive label size in whole inches from ^PW/^LL or content extent."""
    width_in = 4
    match = re.search(r"\^PW(\d+)", zpl)
    if match:
        width_in = min(8, max(1, math.ceil(int(match.group(1)) / DOTS_PER_INCH)))

    explicit_length = re.search(r"\^LL(\d+)", zpl)
    if explicit_length:
        height_in = min(8, max(1, math.ceil(int(explicit_length.group(1)) / DOTS_PER_INCH)))
        return width_in, height_in

    lh_y = 0
    match = re.search(r"\^LH(-?\d+),(-?\d+)", zpl)
    if match:
        lh_y = int(match.group(2))

    # Unknown-extent recalls/graphics keep the safe full-size default.
    if re.search(r"\^(XF|GF|XG)", zpl):
        return width_in, 6

    font_height = 32
    last_y = 0
    bottom = 0
    # Split into field segments; a segment without its own origin continues
    # from the previous one (^FT concatenation pattern).
    for segment in re.split(r"(?=\^(FO|FT))", zpl):
        origin = re.match(r"\^(FO|FT)(-?\d+),(-?\d+)", segment)
        if origin:
            last_y = int(origin.group(3)) + (lh_y if origin.group(1) == "FO" else 0)
        if not re.search(r"\^(FO|FT)", segment):
            # Label-level commands (^RS, ^BY, ^PW ...) carry no print height,
            # except ^SN counters which print in the active font.
            if re.search(r"\^SN", segment) and origin:
                bottom = max(bottom, last_y + font_height)
            continue
        first = re.search(r"\^FO-?\d+,-?\d+\^([A-Z]+)", segment)
        if first and first.group(1)[:2] in ("RF", "FN", "HV", "DF"):
            continue  # chip writes, placeholders, stored formats: no ink
        font_height = _font_height(segment, font_height)
        if re.search(r"\^GB(\d+),(\d+)", segment):
            boxes = re.findall(r"\^GB(\d+),(\d+)", segment)
            height = max(int(h) for _, h in boxes)
        elif re.search(r"\^B7", segment):
            height = 250  # PDF417 stacked rows; edge-check guards the guess
        elif re.search(r"\^B[123C8]", segment):
            # ^B1/^B3 carry height third (o,e,h); ^B2/^BC/^B8 second (o,h).
            bars = re.findall(r"\^B[13]\w+,\w+,(\d+)", segment)
            bars += re.findall(r"\^B[28C]\w+,(\d+)", segment)
            height = max([int(h) for h in bars] or [font_height])
        elif re.search(r"\^FB(\d+),(\d+)", segment):
            blocks = re.findall(r"\^FB(\d+),(\d+)", segment)
            height = max(int(lines) * max(font_height, 30) for _, lines in blocks)
        else:
            height = font_height
        bottom = max(bottom, last_y + height)

    height_in = min(6, max(1, math.ceil((bottom + BOTTOM_MARGIN_DOTS) / DOTS_PER_INCH)))
    return width_in, height_in


def render_png(zpl: str, width: int, height: int, timeout: int = 60) -> bytes:
    url = LABELARY.format(w=width, h=height)
    request = urllib.request.Request(url, data=zpl.encode("utf-8"), headers={"Accept": "image/png"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    if not body.startswith(b"\x89PNG"):
        raise SystemExit(f"Labelary did not return PNG (got {len(body)} bytes for {width}x{height})")
    return body


def _decode_png_rows(body: bytes) -> tuple[int, int, list[bytes]]:
    """Minimal PNG decoder: 8-bit gray/RGB(A) only. Returns (w, h, rows)."""
    assert body[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, width, height, channels = 8, 0, 0, 0
    raw = b""
    while pos < len(body):
        (length,) = struct.unpack(">I", body[pos : pos + 4])
        ctype = body[pos + 4 : pos + 8]
        data = body[pos + 8 : pos + 8 + length]
        if ctype == b"IHDR":
            width, height, depth, colortype, _, _, _ = struct.unpack(">IIBBBBB", data)
            assert depth == 8, f"unsupported bit depth {depth}"
            channels = {0: 1, 2: 3, 4: 2, 6: 4}[colortype]
        elif ctype == b"IDAT":
            raw += data
        elif ctype == b"IEND":
            break
        pos += 12 + length
    pixels = zlib.decompress(raw)
    stride = width * channels
    rows, prev, off = [], bytearray(stride), 0
    for _ in range(height):
        filt = pixels[off]
        cur = bytearray(pixels[off + 1 : off + 1 + stride])
        if filt == 1:  # Sub
            for i in range(channels, stride):
                cur[i] = (cur[i] + cur[i - channels]) & 0xFF
        elif filt == 2:  # Up
            for i in range(stride):
                cur[i] = (cur[i] + prev[i]) & 0xFF
        elif filt == 3:  # Average
            for i in range(stride):
                a = cur[i - channels] if i >= channels else 0
                cur[i] = (cur[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif filt == 4:  # Paeth
            for i in range(stride):
                a = cur[i - channels] if i >= channels else 0
                b, c = prev[i], prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                cur[i] = (cur[i] + pr) & 0xFF
        rows.append(bytes(cur))
        prev, off = cur, off + 1 + stride
    return width, height, rows


def _row_has_ink(row: bytes, width: int, channels: int, x0: int, x1: int) -> bool:
    for x in range(x0, x1):
        base = x * channels
        if channels == 1:
            if row[base] < 128:
                return True
        else:
            if min(row[base], row[base + 1], row[base + 2]) < 128:
                return True
    return False


def touches_edge(body: bytes) -> tuple[bool, bool]:
    """(bottom_touched, right_touched): ink within 8 px of the label edge."""
    width, _height, rows = _decode_png_rows(body)
    channels = len(rows[0]) // width
    band = 8
    bottom = any(_row_has_ink(row, width, channels, 0, width) for row in rows[-band:])
    right = any(any((min(row[(width - band + dx) * channels : (width - band + dx) * channels + 3]) < 128) if channels > 1 else (row[(width - band + dx) * channels] < 128) for dx in range(band)) for row in rows)
    return bottom, right


def render_fitting(zpl: str, size: tuple[int, int]) -> tuple[bytes, tuple[int, int]]:
    """Render at size, bumping dimensions while ink touches the edge."""
    width, height = size
    for _ in range(3):
        png = render_png(zpl, width, height)
        bottom, right = touches_edge(png)
        if right:
            width = min(8, width + 1)
        if bottom:
            height = min(8, height + 1)
        if not (bottom or right) or (width >= 8 and height >= 8):
            return png, (width, height)
        time.sleep(1)
    return render_png(zpl, width, height), (width, height)


def process_page(path: Path, apply: bool, fixed_size: tuple[int, int] | None) -> int:
    text = path.read_text(encoding="utf-8")
    img_dir = path.parent / "img"
    stem = path.stem
    out = []
    pos = 0
    rendered = 0
    for index, match in enumerate(ZPLCODE.finditer(text)):
        out.append(text[pos : match.end()])
        pos = match.end()
        inner = match.group(2)
        after = text[match.end() : match.end() + 400]
        if RENDERED_MARKER in after:
            print(f"{path.name}: block {index} already has a render, skipping")
            continue
        zpl = block_to_zpl(inner)
        if "^XA" not in zpl or "^XZ" not in zpl:
            print(f"{path.name}: block {index} is not a full label (no ^XA/^XZ), skipping")
            continue
        img_name = f"zpl_{stem}_{index}.png" if len(list(ZPLCODE.finditer(text))) > 1 else f"zpl_{stem}.png"
        size = fixed_size or estimate_size(zpl)
        if not apply:
            print(f"{path.name}: block {index} would render {size[0]}x{size[1]} -> img/{img_name}")
            continue
        png, final = render_fitting(zpl, size)
        img_dir.mkdir(exist_ok=True)
        (img_dir / img_name).write_bytes(png)
        out.append(f'\n    <img class="zplrender" src="img/{img_name}" alt="Rendered ZPL label" {RENDERED_MARKER}="1">')
        rendered += 1
        note = "" if final == size else f" (bumped to {final[0]}x{final[1]})"
        print(f"{path.name}: block {index} rendered {final[0]}x{final[1]} -> img/{img_name} ({len(png)} bytes){note}")
        time.sleep(1)
    if apply and rendered:
        path.write_text("".join(out) + text[pos:], encoding="utf-8")
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--page", help="single page file to process")
    group.add_argument("--all", action="store_true", help="all course pages")
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--size", default="auto", help="label size WxH, or 'auto' to derive per block")
    parser.add_argument("--root", default="src", help="tree searched by --all for zpl_*.php")
    args = parser.parse_args(argv)

    fixed_size: tuple[int, int] | None = None
    if args.size != "auto":
        parts = args.size.lower().split("x")
        assert len(parts) == 2, "--size must look like 4x6"
        fixed_size = (int(parts[0]), int(parts[1]))

    pages = [Path(args.page)] if args.page else sorted(Path(args.root).glob("zpl_*.php"))
    total = 0
    for page in pages:
        try:
            total += process_page(page, args.apply, fixed_size)
        except OSError as error:
            print(f"{page.name}: ERROR {error}")
            return 1
    print(f"done: {total} image(s) rendered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
