"""Pages that used to fatal in the error logs of this site.

Copy to error_regression_cases.py and list the paths; the test re-requests
each one and fails when the failure signature is still there.
"""

PATHS = ("/index.php",)
