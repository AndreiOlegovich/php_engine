# erlcode-lint

Compiles, dialyzes and replays the Erlang examples of a lesson page, so
that an example that does not build or a transcript that no longer matches
cannot stay on the page.

## What it does

1. Finds the `<p class="erlangcode ...">` blocks of a `.php` lesson. Each
   `<code>` child is one source line; colour `<span>` markup is dropped.
   `cmd1` holds a shell command and `cout1` its output, never Erlang
   source, so neither is compiled.
2. A block whose first line is `-module(Name).` is written to
   `Name.erl` in a temporary folder and compiled with
   `erlc +debug_info +warnings_as_errors`. A compiler warning is an
   error: an unused variable or a shadowed name fails the lesson.
3. A block whose first line is a `#!` shebang is written to a temporary
   `.escript` file and compiled, not run, with
   `escript:extract(File, [compile_source])`.
4. Any other `erlangcode` block is skipped and counted: it is a snippet
   without a module header, not a module.
5. Every `cout1` block with `1> `, `2> ` prompts is a shell session. The
   lines after the prompts are the input, the lines between them the
   answer the page claims. All inputs of a session are replayed in one
   `erl` process, in page order, so bindings, `rd` records, `b()`, `h()`
   and `v(N)` behave as they do interactively. `-noshell` would not
   evaluate stdin as forms at all, so the linter starts a normal `erl`
   with stdin redirected from a file and appends a final `halt().`; a
   session that still runs after `--session-timeout` seconds is a
   failure. The printed
   answer, with the prompts and startup banner removed, must equal the
   page text. A bare `1>` with no expression is the startup banner: it is
   counted and not replayed.
6. Every beam that compiled is checked with `dialyzer` against a PLT of
   erts, kernel and stdlib. The PLT is built once per OTP release and
   cached in `~/.cache/erlcode-lint/otp<release>-plt`, outside the
   worktree (`ERLCODE_LINT_CACHE` or `--plt` move it).

The run prints one line per session, `session N: OK` or `session N: FAIL`
with the first line where the page and the runtime disagree.

Exit status: 0 all blocks and sessions pass, 1 at least one warning,
error or session failure, 2 the linter could not run (missing
`erlc`/`dialyzer`, or a PLT that does not build).

## Use

From the repo root:

```sh
python3 container/tools/erlcode-lint/erlcode_lint.py \
    container/src/dev/erlang/course/01_what_is_erlang.php
```

A directory is read recursively, minus the scratch copies (`* copy.php`,
`sess_*.php`) the other repo linters skip. The first run builds the PLT
and takes a minute or two; later runs take seconds.

## Tests

```sh
container/.venv/bin/python -m pytest container/tools/erlcode-lint/tests -q
```

The suite covers a clean module, an unused variable, a dialyzer type
error, an escript block, a matching session, a session with a wrong
answer, a session that hangs until the timeout, and a startup banner. It
needs `erl`, `erlc` and `dialyzer` on the machine and skips itself when
`erlc` or `dialyzer` is missing.

The `# version` header on line 2 and the `VERSION` constant of the script
move together; `--version` prints the constant.
