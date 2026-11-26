# In the container

You run in a Docker container with --auto: every command runs without a
prompt. The container holds the tracked files of an agent worktree and
nothing else, so these replace some rules of AGENTS.md here:

- There is no git and no .git. Search with `grep -rn` or `rg` instead of
  `git grep`: there are no ignored files here for them to read.
- container/src is writable, and so is any path the run was started with
  `--rw`. AGENTS.md, the skills, container/docs, container/tools and
  container/tests are read-only otherwise.
- Files that read as empty and refuse writes (`*.env`, `*creds*`) are
  masked: say what should change in them, the edit is made on the host.
- There are no credentials here. Test the deploy scripts with `--dry-run`
  and a dummy creds file in /tmp, never against a real host.
- The local site, the tests that need it (`run-tests-venv.sh`) and commits
  are on the host. Do not try to reach them; list what is left to run on
  the host at the end of your answer.
- Do not run generate-sitemap.sh, even after adding pages: it dates every
  page by its file time, and here every file has the time of the checkout.
  The sitemap is rebuilt on the host after the merge.
- Image files are not in the container: `container/docs/image-files.txt`
  lists the ones on the host (path, size, sha1), and `test_img_src.py`
  reads it. An image you make (a banner, a diagram) is git-ignored, so the
  merge does not carry it: name each one in your answer, and the host copies
  them into the main checkout after the merge.
- Scratch files go to /tmp.

## Lesson pages with code or questions

Before you report a lesson page you wrote or changed, run both checks on it:

```bash
python3 container/tools/course_check/code_blocks.py container/src/dev/rust/course/011_ownership.php --lang rust
python3 container/tools/course_check/quiz_answers.py container/src/dev/rust/course/011_ownership.php --lang rust
```

`--lang` is the language of the course. A `FAIL`, `WRONG`, `REFERS` or
`STRUCT` line is yours to fix; `SKIP` and `UNMATCHED` are usually a limit of
the tool, so read them before you change anything. The manual, with the
commands for a whole course and for another language, is
`container/docs/course-checks.md`.

## Python gates

`ruff`, `mypy`, `pylint` and `pytest` are on PATH in every agent image, from
a venv in /opt/lint, at the versions of `.pre-commit-config.yaml`. Before you
report Python you wrote or changed, run (from /work):

```bash
ruff format --config container/pyproject.toml FILE...
ruff check --config container/pyproject.toml --no-cache FILE...
mypy --config-file container/pyproject.toml --cache-dir /tmp/mypy FILE...
pylint --rcfile=container/pyproject.toml FILE...
```

And the tests with `pytest -p no:cacheprovider` from `container/`:

```bash
cd container && pytest -p no:cacheprovider <tests> -q
```

Caches go to /tmp, never into the worktree: a `.mypy_cache`, `.ruff_cache`
or `.pytest_cache` there refuses the next round. No `pip install` into
/opt/lint.

numpy (2.4.6) and gunicorn (25.2.0) are in the system Python of every image,
not in /opt/lint, because the examples of the pages run with plain
`python3`, which does not see a venv: `python3 -c "import numpy"` and
`gunicorn --version`. A page that needs another library installs it with pip
in a venv under /tmp and names the version it used.

The base image (`Dockerfile.opencode`, the one built without a language flag)
has these gates, numpy, gunicorn and bandit, and no language linters.

## Native image programs

The native image (`Dockerfile.native.opencode`) has the compilers and these
tools: `cargo clippy`, `cargo fmt`, `maturin`, `patchelf`, `valgrind`,
`strace` and `hyperfine`, and the linters and formatters of each language of
the image, on PATH:

C and C++:

- `cppcheck 2.21.0`, static analysis: `cppcheck --enable=all file.c`.
- `clang-tidy 22.1.3`, clang's checks. It enables none by default, so name
  them or add a `.clang-tidy`:
  `clang-tidy --checks='clang-analyzer-*' file.c --` (the `--` ends the
  clang-tidy options and passes the rest to the compiler).
- `clang-format 22.1.3`, formatting: `clang-format --dry-run --Werror file.c`
  reports, `clang-format -i file.c` rewrites the file.
- `gcovr 8.6`, coverage reports over gcov data: build with `--coverage`, then
  `gcovr -r . --html-details coverage.html`.

Shell:

- `shellcheck 0.11.0`: `shellcheck script.sh` (the repo's pre-commit runs the
  same tool on container/tools).
- `shfmt 3.13.1`: `shfmt -d script.sh` prints the diff, `shfmt -w script.sh`
  rewrites the file. Its `--version` prints `(devel)`: Alpine builds it
  without the version string.

Go (gofmt and go vet come with the go package):

- `golangci-lint 2.5.0`: `golangci-lint run` in a module folder.
- `staticcheck 2026.1`: `staticcheck ./...`.
- `gofmt -l .` lists the files gofmt would change, `go vet ./...` runs vet.

Haskell:

- `hlint 3.10`: `hlint File.hs`.
- `ormolu 0.8.0.2`: `ormolu --mode check File.hs` reports, `ormolu -i File.hs`
  formats in place.

Fortran, from the venv /opt/fortran-tools, with both commands on PATH:

- `fortitude 0.10.1`: `fortitude check file.f90`.
- `fprettify 0.3.7`: `fprettify --diff file.f90` prints the diff and
  `fprettify file.f90` rewrites the file in place.

Python: `bandit 1.9.4`, a security lint, next to the gates above:
`bandit -r container/tools`.

`container/tools/ai/docker/smoke_tools.sh` prints the version of every tool
of an image and fails on a missing one:

```bash
docker run --rm -i --entrypoint sh IMAGE -s native < smoke_tools.sh
```

Deliberately missing: no rustup and no nightly Rust, so no sanitizers and no
unstable features, and no `cargo-audit` or `cargo-deny`: they download the
RustSec advisory database on first run, and a page check runs with
`--network none`. `maturin` is the Alpine package; a page that needs a newer
one installs it with pip in a venv under /tmp and names the version it used.

Also missing, and staying out: no `fpm` and no OpenCoarrays (not Alpine
packages, large builds; the Fortran pages use gfortran directly), no
`govulncheck` (not packaged), no hadolint (the Dockerfiles are linted on the
host) and no COBOL linter, because none is maintained and packaged;
`cobc -fsyntax-only file.cob` is the syntax check that is left.

## Script image programs

The script image (`Dockerfile.script.opencode`) has Node.js (`node`,
`npm`), the TypeScript compiler (`tsc`) and Ruby (`ruby`, `irb`, `gem`,
`erb`), with these tools on PATH:

- `eslint 10.14.0`: `eslint file.js` reads the flat `eslint.config.js` of the
  folder it runs in; `eslint --fix file.js` applies the fixes it can.
- `prettier 3.8.3`: `prettier --check file.js` reports, `prettier --write
  file.js` formats. It handles JavaScript, TypeScript, JSON and Markdown.
- `tsc 5.9.2` (the `typescript` package): `tsc --noEmit file.ts` type-checks
  without writing a `.js` file.
- `rubocop 1.92.2`: `rubocop file.rb` lints; `rubocop -a file.rb` applies the
  safe autocorrections.
- `shellcheck 0.11.0` and `shfmt 3.13.1`, as in the native image.
- `bandit 1.9.4` with the Python gates, as in the native image.

eslint and its 78 packages live in /opt/node-tools, not in
/opt/node-packages: `/node_modules` links there, and eslint's dependencies
would otherwise be visible to every `require()` of a lesson. Only its `bin`
is linked into /usr/local/bin. The `ruby-racc` package is in the image for
one reason: without it `gem install rubocop` builds racc from source and
needs ruby-dev and a compiler.

The same smoke test covers this image:

```bash
docker run --rm -i --entrypoint sh IMAGE -s script < smoke_tools.sh
```

Deliberately missing: no test runner or bundler (jest, mocha, webpack, vite):
they are not linters, and a lesson that names one runs its own `npm install`.
No `ruby-lsp`: rubocop has `--lsp` for editors, and the language server gem
is a large dependency that no page runs.

## VM image programs

The VM image (`Dockerfile.vm.opencode`) has the JDK 21 with Maven, Groovy,
the .NET SDK, Erlang/OTP and Elixir, and the Thrift compiler, with these
checkers on PATH:

Java:

- `checkstyle 14.4.0`: `checkstyle -c /google_checks.xml File.java` lints
  with the Google rules, which ship inside the jar; `/sun_checks.xml` is the
  stricter Sun set of the same jar.
- `google-java-format 2.0.0`: `google-java-format --dry-run File.java` lists
  the files it would rewrite, `google-java-format -i File.java` rewrites
  them.

Groovy: no linter. CodeNarc is a set of jars from Maven that the image does
not carry, so only `groovyc` reports what is wrong, and it reports syntax.

.NET: nothing was added, because `dotnet format` is part of the SDK:
`dotnet format --verify-no-changes` checks a project and `dotnet format`
fixes it. Neither has been run against this image yet: check them on the
host before a page relies on them.

Erlang:

- `erlc` warns while it compiles: `erlc +warn_unused_vars file.erl`.
- `rebar3 3.27.3`: `rebar3 compile` inside a project. `rebar3 dialyzer`
  needs dialyzer, which this image does not have: see below. Type checking
  of Erlang code belongs to the erlang image (`--erlang`).

Elixir: `mix format --check-formatted` lists the files that need formatting
and `mix compile --warnings-as-errors` fails on a warning, both inside a mix
project.

The Thrift compiler only generates code: `thrift --gen java file.thrift`.

`shellcheck 0.11.0`, `shfmt 3.13.1` and `bandit 1.9.4` are here as in the
other images. The same smoke test covers this image:

```bash
docker run --rm -i --entrypoint sh IMAGE -s vm < smoke_tools.sh
```

Deliberately missing: no PMD and no spotbugs (a large bundle of jars and
dependencies each, next to the checkstyle that is in the image), no CodeNarc
for Groovy, and no credo for Elixir: credo comes from Hex, which is a
download at run time, and a page check runs with `--network none`. No
dialyzer: `/usr/bin/dialyzer` is in the `erlang27-dialyzer` package only, and
that package depends on `erlang27-wx`, which pulls wxwidgets, GTK and WebKit
(about 570 MB), so this image has no working dialyzer — the erlang image
(`--erlang`) is the one to type check in. For the same reason there is no
observer GUI.

## Ruby image programs

The ruby image (`Dockerfile.ruby.opencode`) has Ruby (`ruby`, `irb`, `gem`,
`erb`) and the same Ruby tooling as the script image:

- `rubocop 1.92.2`: `rubocop file.rb` lints; `rubocop -a file.rb` applies the
  safe autocorrections. It is the only gem the image adds on purpose, and
  `ruby-racc` from apk is what keeps its install from compiling racc.
- `shellcheck 0.11.0`: `shellcheck script.sh`.
- `shfmt 3.13.1`: `shfmt -d script.sh` prints the diff, `shfmt -w
  script.sh` rewrites the file. Its `--version` prints `(devel)`.
- `bandit 1.9.4` with the Python gates, as in the other images.

The same smoke test covers this image:

```bash
docker run --rm -i --entrypoint sh IMAGE -s ruby < smoke_tools.sh
```

Deliberately missing: no `ruby-lsp`; rubocop has `--lsp` for editors, and
the language server gem is a large dependency that no page runs.

## Erlang image programs

The erlang image (`Dockerfile.erlang.opencode`) is Erlang/OTP alone, for the
type checks and the examples of the Erlang pages, with:

- `dialyzer` (OTP 27.3.4.19): `dialyzer --build_plt --apps erts kernel stdlib`
  builds the PLT once, then `dialyzer file.beam` checks the types, and
  `rebar3 dialyzer` does it for a project. Dialyzer is here and not in the vm
  image: the `erlang27-dialyzer` package depends on `erlang27-wx`, which is
  the wxwidgets/GTK/WebKit stack (about 570 MB), and this image pays for it
  because dialyzer is the main Erlang checker.
- `rebar3 3.27.3`: `rebar3 compile`, `rebar3 xref`, `rebar3 eunit` and
  `rebar3 dialyzer` inside a project.
- `elvis 4.0.0`, the style linter: `elvis rock` checks the project's
  `src/**/*.erl` with the default rules, `elvis rock file.erl` one file.
- `shellcheck 0.11.0`, `shfmt 3.13.1`, and `bandit 1.9.4` with the Python
  gates.

The compiler checks a file on its own: `erlc +warn_unused_vars
+warnings_as_errors file.erl`, and `escript script` runs an escript.

The same smoke test covers this image:

```bash
docker run --rm -i --entrypoint sh IMAGE -s erlang < smoke_tools.sh
```

Deliberately missing: no `erlfmt` (it is a rebar3 plugin, which is a download
at run time), and no newer elvis: 5.x and 6.x publish only tags, with no
escript asset, so 4.0.0 is the newest release that installs without a build.
