#!/usr/bin/env python3
# version 1.7.1
"""Build the whole programs of the code blocks of pages and check the failures.

A block counts as a whole program when it matches the whole-program regex of
the language preset. Every such block is extracted from its page, built in a
folder of its own, and the build result is compared with the page: a failed
build is expected when the page shows the compiler's first error line, and a
block that needs a crate, a header or another file of a multi-file example is
reported as SKIP, not as a failure. So is a run that dies because a file the
lesson's project holds, such as package-lock.json, is not in the folder.

A block is built and run the way the commands after it on the page build
and run it: with the page's compiler flags, under that file name (env.mjs
is a module) or program name, with its arguments and its NAME=value
variables. A block the page only compiles is not run. A run whose input the
page pipes in, or that reads a file the block does not carry, is a SKIP. A
failed run is expected when the page text around the block shows it.

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
  code_blocks.py ../src/dev/rust/course --lang rust
  code_blocks.py ../src/dev/rust/course --lang rust --jobs 8
  code_blocks.py ../src/dev/rust/course/011_ownership.php --lang rust --run
  code_blocks.py ../src/dev/cpp/course --lang cpp --docker <project>-agent-native
  code_blocks.py ../src/dev/c --lang c --build 'gcc -std=c17 -Wall -Wextra {src} -o {out}'
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
    if args.timeout is not None and args.timeout <= 0:
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
    timeout = language.timeout if args.timeout is None else args.timeout

    try:
        pages = cc.collect_pages(args.paths)
    except FileNotFoundError as error:
        parser.error(str(error))

    root, holder = cc.temp_workdir(args.keep, "code-blocks-")
    totals = {"blocks": 0, "whole": 0, "built": 0, "expected": 0, "skipped": 0, "findings": 0}
    records: list[dict[str, object]] = []
    plans: list[tuple[Path, cc.CodeBlock, Path, cc.BuildJob, str]] = []
    jobs: list[cc.BuildJob] = []
    try:
        for page_path in pages:
            text = page_path.read_text(encoding="utf-8", errors="replace")
            for block in cc.extract_blocks(text, code_class, page_path):
                totals["blocks"] += 1
                if not pattern.search(block.code):
                    continue
                totals["whole"] += 1
                stem = f"{page_path.parent.name}_{page_path.stem}_{block.number}"
                workdir = root / stem
                workdir.mkdir(parents=True, exist_ok=True)
                run = block_run(language, block)
                source_name = block_source_name(language, block, run, extension, fixed=bool(args.ext)) or f"{stem}.{extension}"
                (workdir / source_name).write_text(block.code + "\n", encoding="utf-8")
                job = cc.BuildJob(
                    workdir,
                    source_name,
                    build if args.build else cc.stated_build(args.lang, block.command.splitlines(), build, extension),
                    run_name="" if language.run else run.name,
                    arguments=run.arguments,
                    run_command=language.run,
                    environment=run.environment,
                    runs=not cc.build_only(block.command, args.lang),
                )
                jobs.append(job)
                plans.append((page_path, block, workdir, job, run.input_from))
        session = cc.open_session(root=root, docker_image=args.docker, network=args.network)
        try:
            results = cc.run_jobs(
                session,
                jobs,
                workers=args.jobs,
                run=args.run,
                timeout=timeout,
                output_limit=args.output_limit,
            )
        finally:
            session.close()
        for (page_path, block, workdir, job, input_from), result in zip(plans, results, strict=True):
            if result.status == "compile_error" and not result.skip:
                result.skip = cc.unbuildable_reason(block.code, workdir)
            if cc.VOID_MAIN_RE.search(block.code) and result.exit_code is not None and 0 < result.exit_code < 128:
                result.exit_code = 0  # void main: the status is whatever was left in the register
            run = block_run(language, block)
            record = report(
                page_path,
                block.number,
                result,
                block.context,
                strict=args.strict,
                ran=args.run and job.runs,
                totals=totals,
                input_from=input_from,
                unread=cc.unread_file(block.code, workdir) or cc.missing_argument_file(run.arguments, workdir),
                cargo=any(segment.split()[0] == "cargo" for segment in cc.shell_segments([block.command])),
                ctest=any(segment.split()[0] == "ctest" for segment in cc.shell_segments([block.command])),
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


def block_run(language: cc.Language, block: cc.CodeBlock) -> cc.PageRun:
    """How the page runs this block, from the command right after it.

    A JavaScript block the page names ("The file helper.js:") followed by a
    command that runs another file, as ``node main.js``, is not run by that
    command: the block is one file of a larger example.
    """
    run = cc.page_run(block.command)
    if language.run and block.caption and run.name and run.name != Path(block.caption).name:
        return cc.PageRun()
    if not language.run and not run.name.startswith("./"):
        return cc.PageRun()
    return run


def block_source_name(language: cc.Language, block: cc.CodeBlock, run: cc.PageRun, extension: str, *, fixed: bool) -> str:
    """The file name of an interpreted block: the page's, when its extension fits, or ''.

    The command's file wins, then the caption: env.mjs runs as a module and
    count.cjs as CommonJS, as on the page. A caption with a folder, as
    tests/inventory.test.js, gives its file name. With --ext only a name with
    that extension is used.
    """
    if not language.run:
        return ""
    for name in (run.name, Path(block.caption).name if block.caption else ""):
        suffix = Path(name).suffix.removeprefix(".")
        if name and (suffix == extension if fixed else suffix in language.named_extensions):
            return name
    return ""


def report(  # pylint: disable=too-many-arguments
    page_path: Path,
    number: int,
    result: cc.ProgramResult,
    plain: str,
    *,
    strict: bool,
    ran: bool,
    totals: dict[str, int],
    input_from: str = "",
    unread: str = "",
    cargo: bool = False,
    ctest: bool = False,
) -> dict[str, object] | None:
    """Print the line of one block, count it, and return a finding record or None.

    plain is the page text the failure is looked for in: the text around the
    block. unread names a file the block opens for reading, or takes as an
    argument, and its folder lacks. cargo says the page builds the block
    with cargo, in a project the tool does not have. ctest says the page runs
    the block through ctest, with the arguments its CMakeLists.txt gives.
    """
    where = f"{page_path} block {number}"
    finding: dict[str, object] | None = None

    if result.status == "built":
        totals["built"] += 1
        failed = ran and (result.timed_out or (result.exit_code not in (0, None)))
        if failed:
            # What the run lacked comes before what the page shows: a package,
            # the input the page pipes in, a file, a variable. A status shown
            # near the block is the status of the page's run, which had them,
            # so matching it here would be luck. A missing file that the text
            # around the block shows as missing is the lesson's own point.
            reason = cc.skip_reason(result.error_output)
            missing = cc.missing_file(result.error_output) or unread
            if not reason and input_from:
                reason = f"input the page gives it from {input_from}"
            elif not reason and missing and not cc.shows_not_found(plain, missing):
                reason = f"a file outside the block: {missing}"
            elif not reason and cc.missing_variable(result.error_output):
                reason = "an environment variable the page sets outside the block"
            expected = not reason and cc.page_expects_failure(plain, result)
            if not reason and not expected and cargo:
                reason = "built and run with cargo on the page, in a project the tool does not have"
            if not reason and not expected and ctest:
                reason = "run by ctest on the page, with the arguments its CMakeLists.txt gives"
            if reason:
                totals["skipped"] += 1
                print(f"SKIP {where}: {reason} ({short(result.error_output)})")
                if strict:
                    totals["findings"] += 1
                    finding = {"file": str(page_path), "block": number, "kind": "SKIP", "detail": reason}
            elif not expected:
                totals["findings"] += 1
                outcome = "timed out" if result.timed_out else f"exit {result.exit_code}"
                print(f"RUN {where}: {outcome} and the page does not show it: {short(result.output or result.error_output)}")
                finding = {"file": str(page_path), "block": number, "kind": "RUN", "detail": outcome}
    elif result.status == "compile_error":
        if cc.expected_failure(result.error_line, plain):
            totals["expected"] += 1
        elif result.skip:
            totals["skipped"] += 1
            print(f"SKIP {where}: {result.skip} ({short(result.error_line)})")
            if strict:
                totals["findings"] += 1
                finding = {"file": str(page_path), "block": number, "kind": "SKIP", "detail": result.skip}
        else:
            totals["findings"] += 1
            print(f"FAIL {where}: {short(result.error_line)}")
            finding = {"file": str(page_path), "block": number, "kind": "FAIL", "detail": result.error_line}
    else:
        totals["findings"] += 1
        print(f"FAIL {where}: {short(result.error_line or result.build_output)}")
        finding = {"file": str(page_path), "block": number, "kind": "FAIL", "detail": result.build_output}
    return finding


def short(text: str, limit: int = 200) -> str:
    """One line of text for a finding, cut at limit characters."""
    line = cc.normalize_space(text)
    return line if len(line) <= limit else line[: limit - 3] + "..."


if __name__ == "__main__":
    sys.exit(main())
