"""Redirect cases of this site: (path, status, Location), one tuple per request.

Copy to redirect_cases.py and replace the samples: a path, the first status
the server answers (not followed), and for a redirect the exact Location
(a site path or an absolute URL), else None.
"""

CASES = (
    ("/old-page/", 301, "/new-page/"),
    ("/folder", 301, "/folder/"),
    ("/no-such-page.php", 404, None),
)
