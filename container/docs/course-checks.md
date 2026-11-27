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

Both need Python 3.14, use the standard library only, take files or folders
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

`--run` also runs every program that built, the way the page does. The
command paragraphs after the block (`cmd1`), up to the next block, give
what follows. Blocks with no command or output between them, such as a
broken and a fixed program shown one after the other, share the commands
and the output after the last of them.

- the build command: `gcc -fsanitize=address -g double.c -o double` builds
  with those flags, the extracted file and the output name in place of the
  ones in the text, also before `;` or `&&` on the same line. A compile of
  two sources (`gcc helpers.c use.c -o use`) is not used: the block is one
  of them;
- whether it runs at all: a block whose commands only compile it, as
  `gcc -Wall badfmt.c -o badfmt` and nothing after, is built and not run.
  Such a page shows a warning and says the program would crash or never
  end;
- the file name: `node env.mjs` runs the block as `env.mjs`, so as a module,
  and `node count.cjs` as CommonJS. A JavaScript block that a "The file X:"
  line names takes that name even without a command, and a command that
  runs another file, as `node main.js` after `helper.js`, is not used;
- the program name and arguments: `./argv alpha beta`, also after a build
  on the same line (`gcc argv.c -o argv && ./argv alpha beta`);
- the variables in front: `GREETING=hello node env.mjs`;
- the input the page feeds it: `npm pack --json | node report.js` or
  `./prog < input.txt`.

Output redirects and pipes after the program (`2>&1`, `| head`) do not change
it and are dropped. A command with an expansion (`$HOME`, a glob) or a node
option is not used, and the block runs under a name of the tool's own with
no arguments. Every run reads `/dev/null` as its input, locally as in
docker, so a program that waits for input never waits for a terminal.

A failed run, a non-zero exit or a timeout, is then read in this order.
What the run lacked comes first: a status the page shows is the status of
the page's run, which had its input and files, so matching it here would be
luck.

1. A run that cannot find a package is `SKIP`.
2. A run that failed without the input the page feeds it is `SKIP`.
3. A run that could not open a file the block does not carry is `SKIP`:
   Node's `ENOENT: no such file or directory, open 'package-lock.json'`, C's
   `demo/a.txt: No such file or directory`, Rust's `kind: NotFound`, a file
   the source opens for reading by name (`fopen("records.bin", "rb")`) and
   does not write itself, or a file named in the arguments
   (`node cli.js notes.txt`). When the text around the block shows this
   file as missing, the lesson fails on purpose and step 5 decides: "No
   such file or directory", `ENOENT` or `kind: NotFound` within 200
   characters of the file name, or anywhere when the text never names the
   file (`errno: 2 message: No such file or directory`).
4. A Rust run that died for an environment variable (`NotPresent`) that a
   build script or a command sets is `SKIP`.
5. The failure is expected when the text around the block shows it: the
   exit status (`exit: 1`, `status 3`, make's `Error 1`, "returns 1", or a
   bare `3` in the output after `echo $?`), a
   panic, `SIGSEGV`, a stack overflow, a sanitizer report or a timeout. The
   text around the block is the prose right before it, back to the last
   code, command or output paragraph, and everything after it (or after its
   group) up to the next block of the language: its commands, its output
   and what the page says about them. It stops before the exercises and the
   quiz, which ask about failures rather than show them. Another program's
   `exit 1` elsewhere on the page does not count.
6. A block the page builds and runs with `cargo` is `SKIP`: it belongs to a
   project the tool does not have. So is a block the page runs with `ctest`:
   the arguments come from its CMakeLists.txt, which the run does not read.
7. Anything else is a `RUN` finding.

A build that fails because the linker cannot find a file the page builds
separately (`cannot find rect.o: No such file or directory`) is `SKIP` too.

A C or C++ program with `void main` exits with whatever was left in the
register, so its status is not read unless a signal killed it.

```bash
# a worker in the container, on its own lesson
python3 container/tools/course_check/code_blocks.py \
  container/src/dev/rust/course/011_ownership.php --lang rust

# a whole course, on the host, in the native image (no compilers on the host)
python3 container/tools/course_check/code_blocks.py \
  container/src/dev/cpp/course --lang cpp --docker <project>-agent-native

# run the programs too, and keep the extracted sources
python3 container/tools/course_check/code_blocks.py \
  container/src/dev/c/course --lang c --run --keep /tmp/c-blocks

# another language, or other flags than the preset
python3 container/tools/course_check/code_blocks.py \
  container/src/dev/rust/course --lang rust --build 'rustc --edition 2021 {src} -o {out}'
```

Lines:

| Line | Meaning | What to do |
|---|---|---|
| `FAIL page block N: <error>` | The block does not build and the page does not show that error. | Fix the block, or show the error on the page if it is a deliberate mistake. |
| `SKIP page block N: <reason>` | The block needs a crate, header or another file, so it cannot build alone. | Nothing, unless `--strict` is on and the skip is unexpected. |
| `RUN page block N: exit R ...` | The program failed or timed out and the page does not say so; the line shows its output, or its stderr when it printed nothing. | Fix the program, or show the failure on the page. |

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
cannot answer it. A file name that two or more options repeat is what the
question asks about, not a pointer: "Which command runs the file hello.js?"
with the options `node hello.js`, `node run hello.js` and so on is not
`REFERS`.

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
  errors do not say `error:`, as Go does, keeps its first diagnostic line. An
  option that names the error of that line counts too, so
  `A SyntaxError, because the private name is used outside its class` is
  proved by `node --check` reporting `SyntaxError: Private field ...`. If
  another option matches instead, it is `WRONG`.
- The program dies of an uncaught error: when it printed nothing to stdout
  and exits with a status other than 0, an option that names the error on
  stderr is the answer. The forms are `a TypeError`, `A TypeError at run
  time`, `It throws a TypeError, because ...` and `Nothing, because the
  assertion throws an AssertionError`. `TypeError boom` or
  `1 / a ReferenceError` describe printed output, so they do not count here.
- A question about a warning is proved from the build log: a build that
  succeeds keeps the warnings it printed, and the marked option may be the
  warning line, its message alone or its `-W` flag.
- A question about an exit status is proved from the run. The last ` / `
  part of an option may name it, as in `about to leave / exit status: 3`, or
  a prose tail, as in `work done, then status 3`, and a question that asks
  for the exit status takes a bare number as one. A line
  a sanitizer wrote to stderr counts as an answer too, so
  `heap-buffer-overflow` is proved by the AddressSanitizer report.
- If the question names its own build command, for example
  `gcc -std=c17 -O2 opt.c -o opt`, that command is used, with the extracted
  file and the output name in place of the ones in the text. A compile
  before `&&` or `;` counts too, as in `gcc -std=c17 x.c -o x && ./x`, and a
  compile of two sources does not.
- If the question names its own run command, for example
  `./args one two`, the program is built and run under that name with those
  arguments, and its `argv[0]` is the name the question uses. A JavaScript
  question states it as `node count.js one two three`: the file gets that
  name, so a program that requires itself as `./m.js` finds itself, and the
  words after the file are passed to the program. `NAME=value` words in
  front, as in `GREETING=hey node env.js`, set those variables for the run.
  A command that redirects or pipes is ignored, because the reader sees a
  file or the end of a pipe that the tool cannot reproduce; only `2>&1` is
  allowed, since the tool reads both streams anyway.
- Another file the question gives inline is written next to the program
  when the program names it: "a.js is `module.exports = { n: 1 };` and
  main.js is:" writes a.js, and "data.json next to the program holds
  `{ ... }`" writes data.json. A file the question only describes in prose,
  as "math.js exports double and square", cannot be written, and the
  question stays `SKIP`.
- A JavaScript question whose prose calls its program a module ("What does
  this module print?", "A module contains:") is written to an `.mjs` file,
  because Node reads a plain `.js` file as a sloppy CommonJS script: there
  `typeof require` is `function`, `this` in a plain call is the global
  object, and a write to a read-only property is silent. The code of the
  question is not read for this, so `module.exports` does not count, and a
  question that says CommonJS stays `.js`. `--ext` turns this off: every
  file gets the extension it names.

A program that proves no option and died because a file of the lesson's
project is missing, as `readFileSync('package.json')` in a fresh folder, is
`SKIP`. A program that throws `ENOENT` on purpose keeps its check: the
option `It throws an Error, because missing.txt does not exist` is proved as
before, and the file rule is asked only when nothing matched.

Exit status is 1 on `WRONG`, `REFERS` or a structure finding. `UNMATCHED`
alone exits 0 unless `--strict` is given.

```bash
# a worker in the container, on its own lesson
python3 container/tools/course_check/quiz_answers.py \
  container/src/dev/rust/course/011_ownership.php --lang rust

# a whole course, on the host, in the native image
python3 container/tools/course_check/quiz_answers.py \
  container/src/dev/c/course --lang c --docker <project>-agent-native

# only the shape of the questions, no compilers needed: '(?!)' matches no
# text, so no question is taken for a program ('^$' would match a blank line)
python3 container/tools/course_check/quiz_answers.py \
  container/src/dev/cpp/course --lang cpp --program-regex '(?!)' --json /tmp/quiz.json
```

Lines:

| Line | Meaning | What to do |
|---|---|---|
| `WRONG page qN: marked [i] ...; ... matches option [j]` | The program proves another option is the answer. | Fix the page: move the answer index or the option text. |
| `UNMATCHED page qN: marked ...; output ...` | Nothing matched. Usually the output depends on the machine. `..., then the run timed out` means the program did not finish in `--timeout` seconds. | Read it. Fix the page or the tool only when it is really wrong; rerun a timeout with a larger `--timeout`. |
| `REFERS page qN: points outside the question (...)` | The question needs the page around it. | Put the code or the fact into the question. |
| `STRUCT ...` | Wrong question count, option count, answer index, duplicate option or empty `why`. | Fix the page. |
| `SKIP page qN: <reason>` | The question's program needs a crate, a package or another file. | Nothing, unless the program should be self-contained. |

Summary: `pages=... questions=... programs=... build_errors=... wrong=...
unmatched=... refers=... structure=...`

`build_errors` counts the question programs that did not build, and it is
not a count of problems. A build error that matches the marked answer is
the question working: question 9 of
`dev/js/course/067_class_fields_and_private.php` reads a private field
outside its class, and its marked answer is that SyntaxError, so it adds 1
and prints nothing. A program that lacks a crate or a package is counted
too, with a `SKIP` line. Only the `WRONG`, `UNMATCHED`, `REFERS` and
`STRUCT` lines need action.

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
- A JavaScript program that imports an npm package runs only when the image
  has that package. `<project>-agent-script` starts with none, and a
  missing package shows `SKIP`. To cover a lesson, add its package to
  `container/containerfiles/Dockerfile.script.opencode`, not to the tools.
- Build and run C, C++ and Rust in `<project>-agent-native`: the pages say
  which compiler they were run with (gcc 15.2.0, rustc 1.96.1), and that is
  the image's. An older compiler on the host gives false findings: GCC 13
  does not take C23 by default and quotes its messages differently.
- `code_blocks.py --run` reads a failure in the text around its block, so a
  page that explains a failure several sections away from the block shows
  it as `RUN`. Move the explanation next to the block, or show the status in
  its output.
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
- Programs run with a timeout and their output is cut at `--output-limit`
  bytes (2000 by default). The timeout is the preset's, 5 s, and 15 s for
  JavaScript: loading a package such as jsdom takes about 2 s, and
  with `--jobs` at the number of CPUs a DOM program passed 5 s now and then.
  `--timeout` sets another one.
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
