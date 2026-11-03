# HTML structure validation (`validate-local-html.sh`)

Validates the HTML of locally served pages with `html-validate`
(pinned v7 — stock Ubuntu 22.04 ships node 16, v8+ needs node 22+).
The authoritative alternative, Nu Html Checker (`vnu.jar`, the W3C
engine), needs Java 17; this machine has Java 11, so it cannot run
here. `apt install tidy` exists as a weaker fallback.

Pure host-side check: `curl` the local PHP server, validate the
fetched HTML, no Docker, no installs (first run lets `npx` fetch
`html-validate@7`).

## Prerequisites

The local server must be up (`http://127.0.0.1:8080` by default,
override with `BASE_URL`):

```bash
BASE_URL=http://127.0.0.1:8080 container/tools/linters/html/validate-local-html.sh /
```

## Usage

All commands run from `container/` (paths below are relative to it).
One page:

```bash
tools/linters/html/validate-local-html.sh /
```

Whole directory — maps every routable
`.php` to its URL (`…/index.php` → `…/`, skips `RelatedArticles.php`
and `*_inc.php` includes which are not pages):

```bash
find src -name '*.php' ! -name 'RelatedArticles.php' ! -name '*_inc.php' | sort | while read -r f; do u="/${f#src/}"; case "$u" in */index.php) u="${u%index.php}";; esac; echo "== $u"; tools/linters/html/validate-local-html.sh "$u" 2>&1 | tail -n 2; done
```

## What it checks

Project rules: `container/tools/linters/html/.htmlvalidate.json`
(`html-validate:recommended` minus house-style noise:
`no-inline-style`, `attr-delimiter`, `no-trailing-whitespace` —
the codebase violates those on purpose).

Useful signal, in order of appearance:

| Rule | Meaning |
|---|---|
| `element-required-attributes` | e.g. `<button>` without `type` (engine templates) |
| `element-permitted-content` | wrong nesting (`<tr>` under `<td>`, `<div>` in table soup) |
| `no-implicit-close` | tag closed implicitly by a sibling (missing `</td>`/`</tr>`) |
| `close-order` | mismatched / missing close tags at end of document |
| `prefer-tbody`, `void-style`, `attr-quotes`, `deprecated` | minor; triage as noise unless touching that markup |

Exit code: nonzero when findings exist (pipe through `tail` per URL
in directory mode, as above).

## Reading the report

```text
/tmp/tmp.XXXX.html
    96:123   error  Element <td> is implicitly closed by sibling   no-implicit-close
```

`line:col` refers to the **fetched HTML**, not the `.php` source —
map back via distinctive nearby text. Told-you-so classes of
pre-existing debt (unclosed `td`/`tr` in legacy hand tables) show up
as `no-implicit-close`/`close-order` cascades; our `<p>` work is
clean when none of the findings mention `p` nesting.

## Limits

- `curl` fetches server-rendered HTML once: ad slots are random per
  request, so re-runs may differ in advert markup; JS runs nowhere
  (rotation scripts don't execute — unrotated slider stacks in the
  raw dump are expected, not findings).
- Redirects (e.g. `/linux/virtualbox.php` → `/dvps/virtualbox/`)
  are followed by `curl -L`; the validated bytes are the final URL's.

## Files

```text
container/tools/linters/html/validate-local-html.sh   the wrapper (BASE_URL + path)
container/tools/linters/html/.htmlvalidate.json  project rule set
container/tools/linters/html/VALIDATE_LOCAL_HTML.md          this manual
```
