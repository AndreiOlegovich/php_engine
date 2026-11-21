---
name: site-map
description: "Decide page versus include and find the URL of a file before editing or creating any PHP file under container/src"
---
## Use when
The task edits, fixes or creates a file under `container/src`, or asks where a
URL is served from.
## Do not use for
Tools, tests and scripts (`site-tooling`); redirects (`site-htaccess`).
## Workflow
1. URL to path, one to one: `/about/` is `container/src/about/index.php`,
   `/about.php` is `container/src/about.php`, `/` is `container/src/index.php`.
   Confirm the URL in `container/src/sitemap.xml` before you patch.
2. A file is not a page when it sits in a folder whose name starts with a dot
   (`.php/` holds the engine, `.css/` the styles: the sitemap generator and
   `check-no-redirected-pages.sh` skip them), when `container/tools/sitemap-excludes.conf`
   names it, or when sibling pages only `require` it. Never give such a file
   a URL, a title or a sitemap entry; do not copy an include into another
   folder to keep a page working.
3. A new page copies its nearest sibling: the same opening header (`<?php`,
   one empty line), the same page class and includes. The page classes and
   markers of the site are the `SITE_PAGE_CLASSES` and `SITE_PAGE_MARKERS`
   keys of `container/site-profile.env`; read them, do not name one site's.
4. After adding or removing a page: `cd container && ./tools/generate-sitemap.sh`,
   then `./tools/check-sitemap.sh`. Not after an edit of an existing page.
5. A page that is not a clear case (a form handler, a service page, a page
   with no article body): stop and ask which way to take it.
## Done means
The answer names the file, says whether it is a page or an include, and, for a
page, gives the URL and its sitemap line. `php -l` and
`pre-commit run --files <file>` ran on the changed files.
