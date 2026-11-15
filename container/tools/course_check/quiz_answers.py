#!/usr/bin/env python3
# version 1.2.0
"""Check the questions of lesson pages: their shape and their marked answers.

The questions are read with dump_quiz.php, which finds the array argument of
LessonQuiz::render() in the source text and never executes the page. Every
question that carries a whole program is built and run, in parallel and each
in a folder of its own, and its output is compared with the marked option; a
question that cannot be built is compared with the first compiler error line
instead, and a question about a warning or an exit status with the warning
the build printed or the status the run returned. A question that points at
something outside itself and carries no code is reported as REFERS.

Exit status: 0 when nothing needs attention, 1 on a wrong answer, a REFERS
line or a structure finding, 2 on a usage error.
"""

# pylint: disable=duplicate-code

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import course_checks as cc

DUMP_SCRIPT = Path(__file__).resolve().parent / "dump_quiz.php"

EXAMPLES = """\
examples:
  quiz_answers.py ../src/examples/rust/course --lang rust
  quiz_answers.py ../src/examples/rust/course --lang rust --jobs 8
  quiz_answers.py ../src/examples/cpp/course/014_recursion.php --lang cpp
  quiz_answers.py ../src/examples/c --lang c --docker opencode-agent-native
  quiz_answers.py ../src/examples/rust/course --lang rust --count 0 --json /tmp/quiz.json
"""

# A question that uses one of these phrases points at the page, not at itself.
REFER_PHRASES: tuple[str, ...] = (
    "the program above",
    "the program below",
    "the example",
    "the lesson's",
    "in the section",
    "the section above",
    "the snippet above",
    "the snippet below",
    "the listing above",
    "the previous program",
    "the output above",
    "the command above",
    "shown above",
    "shown below",
)
REFER_FILE_RE = re.compile(
    r"\b(?:in|from|see|read|open|inside|file)\s+`?([\w-]+\.(?:rs|c|cc|cpp|h|hpp|go|hs|f90|cob|cbl|asm|s))\b",
    re.IGNORECASE,
)
FENCE_RE = re.compile(r"```[A-Za-z0-9_+-]*\r?\n(.*?)```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`]+)`", re.DOTALL)


@dataclass
class Question:
    """One question of a lesson."""

    number: int
    q: str
    options: list[str]
    answer: int | None
    why: str


@dataclass
class QuizPage:
    """The questions of one page."""

    path: Path
    questions: list[Question] = field(default_factory=list)


def code_snippets(text: str) -> list[str]:
    """The code a question carries: fenced blocks first, then inline code."""
    snippets = [match.group(1).strip("\n") for match in FENCE_RE.finditer(text)]
    rest = FENCE_RE.sub("", text)
    snippets += [match.group(1) for match in INLINE_CODE_RE.finditer(rest)]
    return [snippet for snippet in snippets if snippet.strip()]


def refer_phrase(question: str, snippets: list[str]) -> str:
    """The phrase with which a question points outside itself, or ''."""
    if snippets:
        return ""
    lowered = question.lower()
    for phrase in REFER_PHRASES:
        if phrase in lowered:
            return phrase
    match = REFER_FILE_RE.search(question)
    return match.group(1) if match else ""


def structure_problems(
    page: QuizPage,
    *,
    expected_count: int = 10,
    expected_options: int = 4,
) -> list[str]:
    """Everything wrong with the shape of the questions of one page."""
    problems: list[str] = []
    if expected_count and len(page.questions) != expected_count:
        problems.append(f"{page.path}: {len(page.questions)} questions, expected {expected_count}")
    for question in page.questions:
        where = f"{page.path} q{question.number}"
        if expected_options and len(question.options) != expected_options:
            problems.append(f"{where}: {len(question.options)} options, expected {expected_options}")
        if not isinstance(question.answer, int) or not 0 <= question.answer < len(question.options):
            problems.append(f"{where}: answer {question.answer!r} is not an option index")
        elif len(set(question.options)) != len(question.options):
            problems.append(f"{where}: two options are equal")
        if not question.why.strip():
            problems.append(f"{where}: why is empty")
    return problems


def read_quiz(
    files: list[Path],
    *,
    php: str = "php",
    dump: Path = DUMP_SCRIPT,
    process_runner: cc.ProcessRunner = cc.run_process,
) -> tuple[list[QuizPage], list[str]]:
    """Read the questions of every page; returns the pages and the read errors."""
    if not files:
        return [], []
    argv = [php, str(dump), *[str(path) for path in files]]
    process = process_runner(argv, timeout=120)
    if process.returncode != 0:
        raise RuntimeError(f"dump_quiz.php failed: {cc.normalize_space(process.stderr or process.stdout)[:400]}")
    try:
        rows = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"dump_quiz.php returned no JSON ({error}): {process.stdout[:200]!r}") from error
    pages: dict[str, QuizPage] = {}
    errors: list[str] = []
    for row in rows:
        path = str(row.get("path", ""))
        if "error" in row:
            errors.append(f"{path}: {row['error']}")
            pages.setdefault(path, QuizPage(Path(path)))
            continue
        page = pages.setdefault(path, QuizPage(Path(path)))
        answer = row.get("answer")
        page.questions.append(
            Question(
                number=int(row.get("n", len(page.questions) + 1)),
                q=str(row.get("q", "")),
                options=[str(option) for option in row.get("options", [])],
                answer=answer if isinstance(answer, int) else None,
                why=str(row.get("why", "")),
            )
        )
    return list(pages.values()), errors


def build_parser() -> argparse.ArgumentParser:
    """The command line of the tool."""
    parser = argparse.ArgumentParser(
        description="Check the questions of lesson pages: their shape and their marked answers.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cc.add_common_arguments(parser)
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="questions a page must have, 0 turns the check off (default: 10)",
    )
    parser.add_argument(
        "--options",
        type=int,
        default=4,
        help="options a question must have, 0 turns the check off (default: 4)",
    )
    parser.add_argument("--strict", action="store_true", help="count UNMATCHED as a finding")
    parser.add_argument("--php", default="php", help="PHP binary that reads the pages (default: php)")
    return parser


def main(argv: list[str] | None = None) -> int:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Check every page given on the command line."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if args.output_limit <= 0:
        parser.error("--output-limit must be greater than zero")
    if args.count < 0:
        parser.error("--count cannot be negative")
    if args.options < 0:
        parser.error("--options cannot be negative")
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")

    language = cc.LANGUAGES[args.lang]
    extension = args.ext or language.extension
    build = args.build or language.build
    pattern = cc.program_pattern(args.program_regex or language.program_regex)

    try:
        files = cc.collect_pages(args.paths)
    except FileNotFoundError as error:
        parser.error(str(error))

    try:
        pages, errors = read_quiz(files, php=args.php)
    except FileNotFoundError:
        parser.error(f"cannot run {args.php}: install PHP or pass --php")
    except RuntimeError as error:
        parser.error(str(error))

    totals = {
        "pages": len(pages),
        "questions": 0,
        "programs": 0,
        "build_errors": 0,
        "wrong": 0,
        "unmatched": 0,
        "refers": 0,
        "structure": 0,
    }
    records: list[dict[str, object]] = []

    for problem in errors:
        totals["structure"] += 1
        print(f"STRUCT {problem}")

    for page in pages:
        totals["questions"] += len(page.questions)
        for problem in structure_problems(page, expected_count=args.count, expected_options=args.options):
            totals["structure"] += 1
            print(f"STRUCT {problem}")

    work, holder = cc.temp_workdir(args.keep, "quiz-answers-")
    tasks: list[QuestionTask] = []
    jobs: list[cc.BuildJob] = []
    try:
        for page in pages:
            for question in page.questions:
                tasks.append(plan_question(page, question, pattern, build, extension, args.lang, work, jobs))
        session = cc.open_session(root=work, docker_image=args.docker, network=args.network)
        try:
            results = cc.run_jobs(
                session,
                jobs,
                workers=args.jobs,
                run=True,
                timeout=args.timeout,
                output_limit=args.output_limit,
            )
        finally:
            session.close()
        pending = iter(results)
        for task in tasks:
            result = next(pending) if task.job is not None else None
            check_task(task, result, totals, records)
    finally:
        if holder is not None:
            holder.cleanup()

    if args.json_file:
        report_data = {
            "tool": "quiz_answers.py",
            "version": cc.VERSION,
            "language": args.lang,
            "summary": totals,
            "findings": records,
            "files": [str(path) for path in files],
        }
        args.json_file.write_text(json.dumps(report_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("pages={pages} questions={questions} programs={programs} build_errors={build_errors} wrong={wrong} unmatched={unmatched} refers={refers} structure={structure}".format(**totals))
    failed = totals["wrong"] or totals["refers"] or totals["structure"]
    if args.strict and totals["unmatched"]:
        failed = True
    return 1 if failed else 0


@dataclass
class QuestionTask:
    """One question ready to report: its REFERS phrase and its program, if any."""

    page: QuizPage
    question: Question
    phrase: str
    program: str | None = None
    job: cc.BuildJob | None = None


def plan_question(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    page: QuizPage,
    question: Question,
    pattern: re.Pattern[str],
    build: str,
    extension: str,
    language: str,
    root: Path,
    jobs: list[cc.BuildJob],
) -> QuestionTask:
    """Find the REFERS phrase of a question and write the program it carries."""
    snippets = code_snippets(question.q)
    phrase = refer_phrase(question.q, snippets)
    program = next((snippet for snippet in snippets if pattern.search(snippet)), None)
    if program is None:
        return QuestionTask(page, question, phrase)
    command = cc.stated_build(language, snippets, build, extension)
    run_name, arguments = cc.stated_run(snippets)
    stem = f"{page.path.parent.name}_{page.path.stem}_q{question.number}"
    workdir = root / stem
    workdir.mkdir(parents=True, exist_ok=True)
    source_name = f"{stem}.{extension}"
    (workdir / source_name).write_text(program + "\n", encoding="utf-8")
    job = cc.BuildJob(workdir, source_name, command, run_name=run_name, arguments=arguments)
    jobs.append(job)
    return QuestionTask(page, question, phrase, program, job)


def check_task(  # pylint: disable=too-many-branches,too-many-return-statements
    task: QuestionTask,
    result: cc.ProgramResult | None,
    totals: dict[str, int],
    records: list[dict[str, object]],
) -> None:
    """Report one question: REFERS first, then the proof of its program."""
    page, question = task.page, task.question
    where = f"{page.path} q{question.number}"
    if task.phrase:
        totals["refers"] += 1
        print(f"REFERS {where}: points outside the question ({task.phrase!r}) and carries no code")
        records.append({"file": str(page.path), "question": question.number, "kind": "REFERS", "detail": task.phrase})
    if task.job is None or result is None:
        return
    if result.status == "compile_error" and not result.skip:
        result.skip = cc.unbuildable_reason(task.program or "", task.job.workdir)

    if result.status == "compile_error":
        totals["build_errors"] += 1
        if result.skip:
            print(f"SKIP {where}: {result.skip}")
            return
        error = result.error_line
        marked = marked_option(question)
        if cc.matches_error(marked, error):
            return
        hits = [index for index, option in enumerate(question.options) if cc.matches_error(option, error)]
        if hits:
            totals["wrong"] += 1
            print(f"WRONG {where}: marked [{question.answer}] {short(marked)!r}; the build error matches option {hits}: {short(error)!r}")
            records.append({"file": str(page.path), "question": question.number, "kind": "WRONG", "detail": error})
            return
        totals["unmatched"] += 1
        print(f"UNMATCHED {where}: marked {short(marked)!r}; build error {short(error)!r}")
        records.append({"file": str(page.path), "question": question.number, "kind": "UNMATCHED", "detail": error})
        return

    if result.status != "built":
        totals["unmatched"] += 1
        print(f"UNMATCHED {where}: marked {short(marked_option(question))!r}; the program did not build")
        return

    totals["programs"] += 1
    output = result.output.strip()
    marked = marked_option(question)
    if output_matches(marked, result, question.q):
        return
    hits = [index for index, option in enumerate(question.options) if output_matches(option, result, question.q)]
    if hits:
        totals["wrong"] += 1
        print(f"WRONG {where}: marked [{question.answer}] {short(marked)!r}; the output matches option {hits}: {short(output or result.error_output)!r}")
        records.append({"file": str(page.path), "question": question.number, "kind": "WRONG", "detail": output})
        return
    totals["unmatched"] += 1
    print(f"UNMATCHED {where}: marked {short(marked)!r}; output {short(output or result.error_output)!r}")
    records.append({"file": str(page.path), "question": question.number, "kind": "UNMATCHED", "detail": output})


def output_matches(option: str, result: cc.ProgramResult, question: str = "") -> bool:
    """True when the option is what the program printed, warned or returned.

    Three forms of an option count: the output itself, a diagnostic the build
    or the run printed, and an exit status. The status is either the last
    ' / ' part of the option, as in 'about to leave / status: 3', or the
    whole option when the question asks for the exit status of the program.
    """
    text, status = cc.split_status(option)
    if status is None and question and cc.asks_exit_status(question):
        bare = cc.BARE_STATUS_RE.match(option.strip())
        if bare:
            return result.exit_code is not None and result.exit_code == int(bare.group(1))
    if text_matches(option, result):
        return True
    if status is None:
        return False
    if result.exit_code != status:
        return False
    if not text.strip():
        return not result.output.strip()
    return text_matches(text, result)


def text_matches(option: str, result: cc.ProgramResult) -> bool:
    """True when the option is the standard output, stderr or a diagnostic."""
    if cc.matches_option(option, result.output):
        return True
    if result.error_output:
        combined = (result.output + "\n" + result.error_output).strip()
        if cc.matches_option(option, combined):
            return True
    candidates = cc.diagnostic_candidates(result.diagnostics, result.error_output)
    return cc.matches_diagnostic(option, candidates)


def marked_option(question: Question) -> str:
    """The option the question marks as right, or ''."""
    if isinstance(question.answer, int) and 0 <= question.answer < len(question.options):
        return question.options[question.answer]
    return ""


def short(text: str, limit: int = 160) -> str:
    """One line of text for a finding, cut at limit characters."""
    line = cc.normalize_space(text)
    return line if len(line) <= limit else line[: limit - 3] + "..."


if __name__ == "__main__":
    sys.exit(main())
