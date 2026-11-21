# Site checks on container/src

The commit gate runs these on every commit that touches the docroot. Each is
also a plain script: run it by hand on the file you are fixing.

| Hook | What it blocks | Run it by hand | What a failure means |
|---|---|---|---|
| `site-line-endings` | a file whose endings differ from LF, or a file with mixed endings | `container/tools/linters/check-line-endings.sh --search --all --src src` | one line was pasted from another source, or a script wrote the file in text mode. `--fix` converts; review the diff. |
| `site-php-blank-lines` | a line holding only spaces or tabs in a `.php` file | `container/tools/linters/check-php-blank-lines.sh --search --all --src src` | an editor kept indentation on an empty line. `--fix` strips it. |
| `site-php-header` | a `.php` file that does not start with `<?php`, an empty line, then the code | `container/tools/linters/check-php-header.sh --search --all --src src` | code on the opening line, or no blank line after it. `--fix` rewrites the header. |
| `site-malformed-urls` | a URL with two schemes or a dotless host (`https://example.comhttps://…`) | `container/tools/linters/find-malformed-urls.sh --all --src src` | a lost `"` in an `href` joined two URLs, or a host was typed without its dot. |
| `site-snake-calls` | `->create_head()` where the engine declares `createHead()` | `python3 container/tools/linters/find-snake-calls.py --src container/src` | phpcs checks declarations, not call sites; this call passes lint and fatals at runtime. |
| `site-sitemap` | a `sitemap.xml` that does not parse, holds two documents, or repeats a `<loc>` | `container/tools/check-sitemap.sh container/src/sitemap.xml` | a second sitemap was concatenated onto the file, or the generator was interrupted. Regenerate with `container/tools/generate-sitemap.sh`. |
| `site-redirected-pages` | a new page whose URL matches an `.htaccess` redirect source | `container/tools/check-no-redirected-pages.sh` | the page is unreachable from the moment it is committed. Remove the redirect or pick another URL. |

The sitemap and redirect hooks print a SKIP and pass while `sitemap.xml` or
`.htaccess` does not exist yet, so a new project starts green.

Skipped on purpose:

- `container/tools/linters/html/validate-local-html.sh` fetches the page from
  the local server. It is a pre-deploy check, not a commit hook: run it with
  the site up.
- phpcs and `php -l` are already hooks of their own.
