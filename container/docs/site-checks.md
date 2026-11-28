# Site checks on container/src

The commit gate runs these on every commit that touches the docroot, and
`image-list` on every commit. Each is also a plain script: run it by hand on
the file you are fixing.

| Hook | What it blocks | Run it by hand | What a failure means |
|---|---|---|---|
| `site-line-endings` | a file whose endings differ from LF, or a file with mixed endings | `container/tools/linters/check-line-endings.sh --search --all --src src` | one line was pasted from another source, or a script wrote the file in text mode. `--fix` converts; review the diff. |
| `site-php-blank-lines` | a line holding only spaces or tabs in a `.php` file | `container/tools/linters/check-php-blank-lines.sh --search --all --src src` | an editor kept indentation on an empty line. `--fix` strips it. |
| `site-php-header` | a `.php` file that does not start with `<?php`, an empty line, then the code | `container/tools/linters/check-php-header.sh --search --all --src src` | code on the opening line, or no blank line after it. `--fix` rewrites the header. |
| `site-malformed-urls` | a URL with two schemes or a dotless host (`https://example.comhttps://…`) | `container/tools/linters/find-malformed-urls.sh --all --src src` | a lost `"` in an `href` joined two URLs, or a host was typed without its dot. |
| `site-snake-calls` | `->create_head()` where the engine declares `createHead()` | `python3 container/tools/linters/find-snake-calls.py --src container/src` | phpcs checks declarations, not call sites; this call passes lint and fatals at runtime. |
| `site-sitemap` | a `sitemap.xml` that does not parse, holds two documents, or repeats a `<loc>` | `container/tools/check-sitemap.sh container/src/sitemap.xml` | a second sitemap was concatenated onto the file, or the generator was interrupted. Regenerate with `container/tools/generate-sitemap.sh`. |
| `site-htaccess-hosting` | an `.htaccess` rule that breaks on the hosting `SITE_HOSTING` names: an HTTPS redirect that loops, `php_value` where PHP is not mod_php (500 on every page), a PHP handler of another hosting, any `.htaccess` on nginx | `python3 container/tools/htaccess_hosting.py` (`--hosting <name>` tries another) | the site was downloaded from one hosting and goes to another. The output gives the line and the form that works on the target; warnings (a plain-HTTP hop, a path of the old account) do not stop the commit. Without `SITE_HOSTING` it only reports what the files were written for. |
| `site-redirected-pages` | a new page whose URL matches an `.htaccess` redirect source | `container/tools/check-no-redirected-pages.sh` | the page is unreachable from the moment it is committed. Remove the redirect or pick another URL. |
| `image-list` | a commit while `container/docs/image-files.txt` does not match the image files on disk (git ignores the images, so nothing else shows that one changed) | `python3 container/tools/images/image_list.py --check` (`--write` rewrites the list) | an image was added, replaced or removed. The hook has already written the new list: `git add container/docs/image-files.txt` and commit again. In a worktree without the images it passes and leaves the list alone. |

The sitemap and redirect hooks print a SKIP and pass while `sitemap.xml` or
`.htaccess` does not exist yet, so a new project starts green.

Skipped on purpose:

- `container/tools/linters/html/validate-local-html.sh` fetches the page from
  the local server. It is a pre-deploy check, not a commit hook: run it with
  the site up.
- phpcs and `php -l` are already hooks of their own.
