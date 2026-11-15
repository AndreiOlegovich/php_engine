#!/usr/bin/env python3
# version 1.2.0
"""Build the whole programs of the code blocks of pages and check the failures.

A block counts as a whole program when it matches the whole-program regex of
the language preset. Every such block is extracted from its page, built in a
folder of its own, and the build result is compared with the page: a failed
build is expected when the page shows the compiler's first error line, and a
block that needs a crate, a header or another file of a multi-file example is
reported as SKIP, not as a failure.

The programs are built in parallel (--jobs), locally or in one docker
container for the whole run. Exit status: 0 when nothing needs attention,
1 when a block failed in a way the page does not show, 2 on a usage error.
"""

# pylint: disable=duplicate-code

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import course_checks as cc

EXAMPLES = """\
examples:
  code_blocks.py ../src/examples/rust/course --lang rust
  code_blocks.py ../src/examples/rust/course --lang rust --jobs 8
  code_blocks.py ../src/examples/rust/course/011_ownership.php --lang rust --run
  code_blocks.py ../src/examples/cpp/course --lang cpp --docker opencode-agent-native
  code_blocks.py ../src/examples/c --lang c --build 'gcc -std=c17 -Wall -Wextra {src} -o {out}'
"""


def build_parser() -> argparse.ArgumentParser:
    """The command line of the tool."""
    parser = argparse.ArgumentParser(
        description="Build the whole programs of the code blocks of pages and check the failures.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cc.add_common_arguments(parser)
    parser.add_argument("--class", dest="code_class", help="code-block class (default: the preset's)")
    parser.add_argument("--run", action="store_true", help="run every built program too")
    parser.add_argument("--strict", action="store_true", help="count SKIP as a finding")
    return parser


def main(argv: list[str] | None = None) -> int:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Check every page given on the command line."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if args.output_limit <= 0:
        parser.error("--output-limit must be greater than zero")
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")

    language = cc.LANGUAGES[args.lang]
    code_class = args.code_class or language.code_class
    extension = args.ext or language.extension
    build = args.build or language.build
    pattern = cc.program_pattern(args.program_regex or language.program_regex)

    try:
        pages = cc.collect_pages(args.paths)
    except FileNotFoundError as error:
        parser.error(str(error))

    root, holder = cc.temp_workdir(args.keep, "code-blocks-")
    totals = {"blocks": 0, "whole": 0, "built": 0, "expected": 0, "skipped": 0, "findings": 0}
    records: list[dict[str, object]] = []
    plans: list[tuple[Path, cc.CodeBlock, str, Path]] = []
    jobs: list[cc.BuildJob] = []
    try:
        for page_path in pages:
            text = page_path.read_text(encoding="utf-8", errors="replace")
            plain = cc.page_text(text)
            for block in cc.extract_blocks(text, code_class, page_path):
                totals["blocks"] += 1
                if not pattern.search(block.code):
                    continue
                totals["whole"] += 1
                stem = f"{page_path.parent.name}_{page_path.stem}_{block.number}"
                workdir = root / stem
                workdir.mkdir(parents=True, exist_ok=True)
                source_name = f"{stem}.{extension}"
                (workdir / source_name).write_text(block.code + "\n", encoding="utf-8")
                jobs.append(cc.BuildJob(workdir, source_name, build))
                plans.append((page_path, block, plain, workdir))
        session = cc.open_session(root=root, docker_image=args.docker, network=args.network)
        try:
            results = cc.run_jobs(
                session,
                jobs,
                workers=args.jobs,
                run=args.run,
                timeout=args.timeout,
                output_limit=args.output_limit,
            )
        finally:
            session.close()
        for (page_path, block, plain, workdir), result in zip(plans, results, strict=True):
            if result.status == "compile_error" and not result.skip:
                result.skip = cc.unbuildable_reason(block.code, workdir)
            record = report(
                page_path,
                block.number,
                result,
                plain,
                strict=args.strict,
                ran=args.run,
                totals=totals,
            )
            if record is not None:
                records.append(record)
    finally:
        if holder is not None:
            holder.cleanup()

    if args.json_file:
        report_data = {
            "tool": "code_blocks.py",
            "version": cc.VERSION,
            "language": args.lang,
            "build": build,
            "summary": totals,
            "findings": records,
            "files": [str(path) for path in pages],
        }
        args.json_file.write_text(json.dumps(report_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("blocks={blocks} whole={whole} built={built} expected={expected} skipped={skipped} findings={findings}".format(**totals))
    return 1 if totals["findings"] else 0


def report(  # pylint: disable=too-many-arguments
    page_path: Path,
    number: int,
    result: cc.ProgramResult,
    plain: str,
    *,
    strict: bool,
    ran: bool,
    totals: dict[str, int],
) -> dict[str, object] | None:
    """Print the line of one block, count it, and return a finding record or None."""
    where = f"{page_path} block {number}"
    finding: dict[str, object] | None = None

    if result.status == "built":
        totals["built"] += 1
        failed = ran and (result.timed_out or (result.exit_code not in (0, None)))
        if failed and not cc.page_expects_failure(plain, result):
            totals["findings"] += 1
            outcome = "timed out" if result.timed_out else f"exit {result.exit_code}"
            print(f"RUN {where}: {outcome} and the page does not show it: {short(result.output)}")
            finding = {"file": str(page_path), "block": number, "kind": "RUN", "detail": outcome}
        return finding

    if result.status == "compile_error":
        if cc.expected_failure(result.error_line, plain):
            totals["expected"] += 1
            return None
        if result.skip:
            totals["skipped"] += 1
            print(f"SKIP {where}: {result.skip} ({short(result.error_line)})")
            if strict:
                totals["findings"] += 1
                finding = {"file": str(page_path), "block": number, "kind": "SKIP", "detail": result.skip}
            return finding
        totals["findings"] += 1
        print(f"FAIL {where}: {short(result.error_line)}")
        return {"file": str(page_path), "block": number, "kind": "FAIL", "detail": result.error_line}

    totals["findings"] += 1
    print(f"FAIL {where}: {short(result.error_line or result.build_output)}")
    return {"file": str(page_path), "block": number, "kind": "FAIL", "detail": result.build_output}


def short(text: str, limit: int = 200) -> str:
    """One line of text for a finding, cut at limit characters."""
    line = cc.normalize_space(text)
    return line if len(line) <= limit else line[: limit - 3] + "..."


if __name__ == "__main__":
    sys.exit(main())
