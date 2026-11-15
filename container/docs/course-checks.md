# Course checks: code blocks and lesson questions

Two tools under `container/tools/course_check/` that check what a lesson page
claims. Both were written after real defects in the C, C++ and Rust courses:

- 57 questions in one batch had the wrong option marked as the answer. The
  page renders, PHP reports nothing, and only running the program shows it.
- A question pointed at "the program above" while the page above it held a
  different program. The reader cannot answer it, and nothing in the page
  says so.

Run them before you report a lesson page, and run them on a whole course
before you call a batch done.

```bash
python3 container/tools/course_check/code_blocks.py --help
python3 container/tools/course_check/quiz_answers.py --help
```

Both need Python 3.12, use the standard library only, take files or folders
(folders are walked for `*.php`), print one line per finding and a summary
last, and write the full result with `--json FILE`. Exit status: 0 when
nothing needs attention, 1 when something does, 2 on a usage error.

Both build the programs in parallel: `--jobs N` at a time (default: the
number of CPUs), each in a folder of its own, so parallel builds never write
the same output file. The report keeps the order of pages and blocks
whatever the order the builds finish in. In `--docker` mode one container
serves the whole run: it is started once, every job is copied into its own
writable folder under `/tmp`, built and run there, and the container is
removed at the end, also after an error or Ctrl-C.

## code_blocks.py

Checks the code blocks of the pages of one language.

1. It reads every block whose class is the language's class (`rustcode`,
   `ccode`, `cppcode`, ...), in page order, and decodes it: colour spans out,
   HTML entities decoded, the `\'` and `\\` of the PHP single-quoted string
   undone.
2. A block is a **whole program** when it matches the preset regex, for
   example `fn main(` for Rust or `int main(` for C. Fragments are counted
   but not built.
3. Each whole program is written to a folder of its own and built with the
   preset command; `--jobs N` programs are built at the same time (default:
   the CPUs of this machine). `--keep DIR` leaves the sources there for a
   look.
4. A failed build is **expected** when the page itself shows the compiler's
   first error line. Lessons teach with deliberate mistakes and print the
   real diagnostic, so this is not a finding. The first error line is the
   first line that says `error:` or `fatal error:`, because gcc and g++ put
   context such as `required from here` before the error.
5. A failed build whose error says the block needs something outside itself
   (a crate, an unresolved import, a header, a module or a second file) is
   reported as `SKIP`, not as a failure. So is a block that includes a
   quoted header the block does not carry, or imports a C++ module: the
   compiler does not always say that either, because a system header of the
   same name can hide the project one. `--strict` turns SKIP into a finding.
6. Anything else is `FAIL`.

`--run` also runs every program that built. A non-zero exit or a timeout is a
finding unless the page shows that status, a panic, `SIGSEGV`, a stack
overflow or a timeout.

```bash
# a worker in the container, on its own lesson
python3 container/tools/course_check/code_blocks.py \
  container/src/examples/rust/course/011_ownership.php --lang rust

# a whole course, on the host, in the native image (no compilers on the host)
python3 container/tools/course_check/code_blocks.py \
  container/src/examples/cpp/course --lang cpp --docker opencode-agent-native

# run the programs too, and keep the extracted sources
python3 container/tools/course_check/code_blocks.py \
  container/src/examples/c/course --lang c --run --keep /tmp/c-blocks

# another language, or other flags than the preset
python3 container/tools/course_check/code_blocks.py \
  container/src/examples/rust/course --lang rust --build 'rustc --edition 2021 {src} -o {out}'
```

Lines:

| Line | Meaning | What to do |
|---|---|---|
| `FAIL page block N: <error>` | The block does not build and the page does not show that error. | Fix the block, or show the error on the page if it is a deliberate mistake. |
| `SKIP page block N: <reason>` | The block needs a crate, header or another file, so it cannot build alone. | Nothing, unless `--strict` is on and the skip is unexpected. |
| `RUN page block N: exit R ...` | The program failed or timed out and the page does not say so. | Fix the program, or show the failure on the page. |

Summary: `blocks=... whole=... built=... expected=... skipped=... findings=...`

## quiz_answers.py

Checks the questions that `LessonQuiz::render()` renders.

The questions are read by `dump_quiz.php`, which uses `token_get_all()` to
find the array argument of the call in the source text and evaluates only
that array literal. The page is never included, so nothing on it runs. PHP is
needed; `--php` names another binary.

Structure, per page: the number of questions (`--count N`, default 10, 0
turns it off), four options per question (`--options N`), an answer index in
range, no two equal options, a non-empty `why`.

A question whose text points outside itself — "the program above", "the
example", "the lesson's", "in the section", a file name it tells the reader
to open, such as `musl.rs` — and carries no code is `REFERS`: the reader
cannot answer it.

Every question that carries a whole program is built and run, with the same
rules as `code_blocks.py`:

- The program runs: its output, its lines joined with ` / `, is compared
  with the marked option. Exact text, ` / ` for newlines and a loose
  word-by-word form all count. If another option matches instead, it is
  `WRONG`; if none matches, it is `UNMATCHED` and the output is printed for
  a human to read.
- The build fails: the marked option must be the compiler's first error line
  (the error code may be left out). The first line that says `error:` is that
  line: gcc and g++ print context before the error when a template, a macro
  or a header is involved, such as `required from here`,
  `recursively required from`, `In instantiation of` or a warning, and those
  lines carry a `file:line:` position like a diagnostic. A language whose
  errors do not say `error:`, as Go does, keeps its first diagnostic line. If
  another option matches instead, it is `WRONG`.
- A question about a warning is proved from the build log: a build that
  succeeds keeps the warnings it printed, and the marked option may be the
  warning line, its message alone or its `-W` flag.
- A question about an exit status is proved from the run. The last ` / `
  part of an option may name it, as in `about to leave / exit status: 3`, and
  a question that asks for the exit status takes a bare number as one. A line
  a sanitizer wrote to stderr counts as an answer too, so
  `heap-buffer-overflow` is proved by the AddressSanitizer report.
- If the question names its own build command, for example
  `gcc -std=c17 -O2 opt.c -o opt`, that command is used, with the extracted
  file and the output name in place of the ones in the text.
- If the question names its own run command, for example
  `./args one two`, the program is built and run under that name with those
  arguments, and its `argv[0]` is the name the question uses. A command that
  redirects or pipes is ignored, because the tool cannot provide the file it
  names.

Exit status is 1 on `WRONG`, `REFERS` or a structure finding. `UNMATCHED`
alone exits 0 unless `--strict` is given.

```bash
# a worker in the container, on its own lesson
python3 container/tools/course_check/quiz_answers.py \
  container/src/examples/rust/course/011_ownership.php --lang rust

# a whole course, on the host, in the native image
python3 container/tools/course_check/quiz_answers.py \
  container/src/examples/c/course --lang c --docker opencode-agent-native

# only the shape of the questions, no compilers needed
python3 container/tools/course_check/quiz_answers.py \
  container/src/examples/cpp/course --lang cpp --program-regex '^$' --json /tmp/quiz.json
```

Lines:

| Line | Meaning | What to do |
|---|---|---|
| `WRONG page qN: marked [i] ...; ... matches option [j]` | The program proves another option is the answer. | Fix the page: move the answer index or the option text. |
| `UNMATCHED page qN: marked ...; output ...` | Nothing matched. Usually the output depends on the machine. | Read it. Fix the page or the tool only when it is really wrong. |
| `REFERS page qN: points outside the question (...)` | The question needs the page around it. | Put the code or the fact into the question. |
| `STRUCT ...` | Wrong question count, option count, answer index, duplicate option or empty `why`. | Fix the page. |
| `SKIP page qN: <reason>` | The question's program needs a crate or another file. | Nothing, unless the program should be self-contained. |

Summary: `pages=... questions=... programs=... build_errors=... wrong=...
unmatched=... refers=... structure=...`

## Time

A whole course is minutes, not hours, because the programs are built in
parallel. On this machine (4 cores, rustc 1.96.1, g++/gcc 15.2.0, local
mode, no docker, `--jobs 4`):

| Course | `code_blocks.py` | `quiz_answers.py` |
|---|---|---|
| Rust: 632 programs, 580 question programs | 135 s | 96 s |
| C++: 703 programs, 441 question programs | 875 s | 845 s |

The C++ course is the slow one: its template lessons take a few seconds per
translation unit. Before the parallel run the same `code_blocks.py` run on
that course took about three hours one program at a time. The C course is
faster than either. In `--docker` mode add the start of the image (a second
or two) and, the first time, the pull.

## Limits

- A cargo project, a CMake project, a C++ module and a multi-file example
  are skipped: the tools build one file with one command. `SKIP` lines say
  which.
- A question about a compiler warning that is not an error needs the marked
  option to be the warning the compiler printed; a warning the option only
  resembles shows `UNMATCHED`.
- A question whose run command redirects or pipes, as in
  `./prog < input.txt`, shows `UNMATCHED` when the marked option depends on
  it: the tool passes the stated arguments but cannot provide the file.
- Output that depends on time, addresses, a random number or the machine
  (`sizeof`, `__STDC_VERSION__` without a `-std` flag, a pointer value)
  cannot be proven by running the program here. `UNMATCHED` is the honest
  answer.
- Programs run with a timeout (5 s by default, `--timeout`) and their output
  is cut at `--output-limit` bytes (2000 by default).
- `--docker` runs one container per program with `--network none`. A build
  that needs the network is not supported; `--network` turns it on.

## Adding a language preset

The presets are one table in `container/tools/course_check/course_checks.py`,
`LANGUAGES`. Add one entry:

```python
"zig": Language(
    "zig",
    "zigcode",
    "zig",
    "zig build-exe {src} -femit-bin={out}",
    r"^\s*pub\s+fn\s+main\s*\(",
),
```

The class must be the real class of the pages (check a page and the
`tools/*code-highlight` folder), the build command is what the lessons show,
and the regex must match a whole program but not a fragment. For C and C++
the regex asks for the opening brace of `main` too, so a question that only
mentions `int main(void)` is not taken for a program. Then add the language
to `PROGRAMS` and `CLASSES` in
`container/tests/test_course_check.py`; the parametrised tests pick it up.

`--class`, `--ext`, `--build` and `--program-regex` override the preset for
one run, which is enough to try a language before it is added.
