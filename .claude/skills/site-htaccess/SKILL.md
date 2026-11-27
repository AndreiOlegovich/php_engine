---
name: site-htaccess
description: "Redirects, 404 rules and rewrite rules in container/src/.htaccess, written for the hosting the site goes to (SITE_HOSTING) as mod_rewrite pairs that keep https behind a hosting proxy"
---
## Use when
The task adds, changes or removes a redirect, a 404 rule or a rewrite rule.
## Do not use for
PHP pages (`site-map`), tools and tests (`site-tooling`).
## Workflow
1. The hosting decides the form. Read `SITE_HOSTING` in
   `container/site-profile.env`: the hosting the site is uploaded to, which
   can differ from the one it was downloaded from. Run
   `python3 container/tools/htaccess_hosting.py` before and after the change
   (`--hosting <name>` tries another target). Whatever the target:
   - the HTTPS redirect fires only when both signals say plain HTTP, so it
     works behind a TLS proxy (Beget) and with TLS in Apache (DreamHost):
     ```
     RewriteCond %{HTTPS} off
     RewriteCond %{HTTP:X-Forwarded-Proto} !https
     RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]
     ```
   - `php_value` and `php_flag` go inside `<IfModule mod_php.c>`: where PHP
     runs as FastCGI they give every page a 500;
   - no `AddHandler`/`SetHandler` for PHP: that names a PHP of one hosting;
   - on `beget` the front server answers an existing static file itself, so
     a rule for an image or CSS path never runs while the file exists;
   - on `nginx` no `.htaccess` is read: tell the user the rules must go into
     the hosting's server configuration, and write none.
   `SITE_HOSTING` empty or `unknown`: use these portable forms and say that
   the target is not set.
2. Write a redirect as a mod_rewrite pair, not as `Redirect` or `RedirectMatch`
   with a bare path: when TLS ends at the hosting's proxy, Apache answers with
   an `http://` Location and the visitor makes a plain-HTTP hop. The target
   takes its scheme from the request environment:
   ```
   # /old/page.php moved to /new/page/
   RewriteCond %{REQUEST_URI} ^/old/page\.php$
   RewriteRule ^ %{ENV:SITE_SCHEME}://%{HTTP_HOST}/new/page/ [R=301,L]
   ```
   `SITE_SCHEME` is set once, near the top of the file
   (`RewriteRule ^ - [E=SITE_SCHEME:https]` in the rewrite block); if the
   file has no such line yet, add it there. The RewriteCond matches the full
   path, leading slash included, anchored. Groups it captures are `%1`, not
   `$1`. A gone page: `RewriteRule ^ - [R=404,L]`. An external target keeps
   its own `https://`.
3. Order: the block that sets the scheme and enforces HTTPS stays first; exact
   rules go before broader prefix rules; the trailing-slash rule stays last.
   Comment every non-obvious rule.
4. One `.htaccess` holds the redirects: the root file of `container/src`. A
   subfolder file with `RewriteEngine On` switches the root rules off in that
   folder unless it also has `RewriteOptions Inherit`.
5. Before adding a page or a redirect, ask which rules match a path:
   `python3 container/tools/htaccess_redirects.py match container/src/.htaccess /path/`
   (exit 1 means redirected). The `site-redirected-pages` hook runs the same
   check on staged new pages. The parser reads the pair form above and the
   old `Redirect` lines; a rule written another way is invisible to it.
6. When a page that is itself a redirect target becomes a redirect, repoint
   every earlier redirect to it in the same change, so no URL needs two hops:
   `git grep -n -F '/old/page' -- container/src/.htaccess container/tests`.
7. Verify: from `container/`, `./tools/run-tests-venv.sh -- tests/test_redirects.py -q`
   against the local site; `tests/redirect_cases.example.py` shows the
   cases file. In a git worktree the local site serves the main checkout, so
   check the rules offline there with the `match` command of step 4.
## Done means
Each old URL matches one rule that names the final target, the final target
matches none, and the redirect tests pass where they can run.
