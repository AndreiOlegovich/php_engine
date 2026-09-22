# Malformed-URL scan manual (`find-malformed-urls.sh`)

Finds broken URLs in the site tree: glued-together addresses left over from
years of copy-paste editing. All commands run from this directory (`scripts/`
— or from `container/` with a `scripts/` prefix; paths resolve the same).

## What it checks

| Check label | What it catches | Real examples found in this tree |
|---|---|---|
| `doubled-scheme` | `http(s)://` appearing twice — two URLs concatenated | `https://www.testsetup.ruhttps://aredel.com/…` (og:image), `https://https://aredel.com/ru/i/`, `http://http://www.mandriva.ru/`, `…/installhttps://aredel.com/ru/linux/` |
| `dotless-host` | scheme followed by a host with no dot — path text mistaken for a host | `https://ru/qa/aredel.com/ru/i/` (breadcrumb URLs; the intended host is `aredel.com`) |

Deliberately skipped (not reported): `localhost` (any port) and doc
placeholders (`http://url/`, `http://foo/bar`, `http://hostname/`,
`http://jenkins_url/`, …) — see `PLACEHOLDERS=` in the script.

## Usage

```bash
./find-malformed-urls.sh --help          # full help
./find-malformed-urls.sh --staged        # staged changes only (pre-commit check)
./find-malformed-urls.sh --committed     # git-tracked files
./find-malformed-urls.sh --files qa/jmeter/index.php ru/qa/
./find-malformed-urls.sh qa/locust/      # positional path, same thing
./find-malformed-urls.sh --all           # whole src/ (~100 hits, takes a bit)
```

Selection flags combine as a union (same convention as `lint-php.sh` /
`lint-js.sh` / `deploy-ftp.sh`): `--files`, positional paths, `--staged`,
`--committed`, `--all` (default when nothing else is given). Names with
spaces work (`--files "ao/chessboard copy/MinimalChess.js"`).

Exit code: `0` = clean, `1` = hits found, `2` = usage/setup error — so it
plugs straight into gates and hooks.

## Reading the report

Each hit is one line:

```text
ru/qa/locust/help.php:12:$page_og_image_url = 'https://www.testsetup.ruhttps://aredel.com/ru/code/python/img/python_log.svg.png'; [doubled-scheme]
```

| Field | Meaning |
|---|---|
| `ru/qa/locust/help.php` | path **relative to `src/`** |
| `:12:` | line number |
| rest of line | the full source line, so you see the context (variable, HTML tag) |
| `[doubled-scheme]` / `[dotless-host]` | which check fired (table above) |

The run ends with a summary:

```text
----
RESULT: 103 malformed URL hit(s).
```

or, when clean:

```text
----
OK: no malformed URLs.
```

## Fixing workflow (suggested)

1. Run scoped first: `./find-malformed-urls.sh --staged` (or `--files …`)
   before every commit/upload — small output, obvious fixes.
2. Judge each hit — the script cannot tell these apart, all need eyes:
   - **real bug** (majority so far): split the glued URLs, e.g.
     `https://www.testsetup.ruhttps://aredel.com/…` → keep the half that
     actually resolves (check with `curl -o /dev/null -w '%{http_code}'`);
     `https://ru/qa/aredel.com/ru/i/` → `https://aredel.com/ru/i/`.
   - **example text**: command output or docs *showing* an error
     (e.g. `locust: error: …`) — leave alone.
   - **new placeholder**: a dummy host not yet in the skip list — add it
     to `PLACEHOLDERS=` in the script instead of "fixing" the content.
3. Re-run the same scope until `OK: no malformed URLs.` (exit 0).
4. Full-tree `--all` is a backlog counter (~100 hits at the time of writing);
   work it down section by section, e.g. together with porting `ru/qa/`
   pages to `qa/`.

## Files

```text
find-malformed-urls.sh   the script (bash + grep -P, no dependencies)
```

## Notes & limits

- Scans `*.php`, `*.js`, `*.html` only; binary files skipped (`grep -I`).
- Reported paths are `src/`-relative; line numbers are 1-based (`grep -n`).
- The `dotless-host` pattern only matches bare labels (`ru`, `localhost`,
  …). Hosts containing `.` or `:` (`127.0.0.1:8089`, `0.0.0.0:8089`,
  `aredel.com`, …) never match.
- Needs `grep -P` (PCRE); errors out clearly where unavailable.
- Like the other scripts, it `cd`s to the project root on start, so you can
  invoke it from anywhere: `container/scripts/find-malformed-urls.sh --staged`.
