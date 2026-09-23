#!/usr/bin/env bash
# Build a static HTML report from the latest pytest_web Allure results.
# No server needed: open the output file directly in a browser.
#
# Usage:
#   docker compose run --rm pytest_web   # (re)run the link check first
#   ./build-static-report.sh
#   # -> reports/sitemap-check-report.html
set -euo pipefail

OUTDIR="reports"
mkdir -p "$OUTDIR"

VOL="$(docker volume ls -q | grep 'allure-results' | head -n 1)"
[ -n "$VOL" ] || { echo "ERROR: allure-results volume not found." >&2; exit 1; }

CSV_SRC="$(docker run --rm -v "$VOL:/r" alpine sh -c "grep -l '^sitemap_url,checked_url' /r/* 2>/dev/null | head -n 1")"
[ -n "$CSV_SRC" ] || { echo "ERROR: no results yet, run pytest_web first." >&2; exit 1; }

docker run --rm -v "$VOL:/r" -v "$PWD/$OUTDIR:/o" alpine cp "$CSV_SRC" /o/all-urls.csv
# stolen from a root-owned container copy: re-own via copy+move (no sudo needed)
cp "$OUTDIR/all-urls.csv" "$OUTDIR/.all-urls.tmp" && mv "$OUTDIR/.all-urls.tmp" "$OUTDIR/all-urls.csv"

python3 - "$OUTDIR/all-urls.csv" "$OUTDIR/sitemap-check-report.html" <<'EOF'
import csv, json, sys, html

csv_path, html_path = sys.argv[1], sys.argv[2]
rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
total = len(rows)
groups = {"200": 0, "404": 0, "other": 0, "error": 0}
for r in rows:
    s = r["status"]
    groups["200" if s == "200" else "404" if s == "404"
           else "other" if s.isdigit() else "error"] += 1

def cls(s):
    return "ok" if s == "200" else "nf" if s == "404" else "oth" if s.isdigit() else "err"

data = [{"u": r["sitemap_url"], "c": r["checked_url"],
         "s": r["status"], "r": r.get("redirects", ""),
         "k": cls(r["status"])} for r in rows]

page = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Sitemap link check</title>
<style>
body{font-family:sans-serif;margin:20px;color:#222}
.cards{display:flex;gap:10px;margin:15px 0;flex-wrap:wrap}
.card{border:1px solid #ccc;border-radius:8px;padding:10px 18px;min-width:110px;text-align:center}
.card b{font-size:24px;display:block}
.ok{color:#137333}.nf{color:#b3261e}.oth{color:#b06000}.err{color:#9400d3}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:10px}
th,td{border:1px solid #ddd;padding:4px 8px;text-align:left;overflow-wrap:anywhere}
th{background:#f2f2f2;position:sticky;top:0}
tr.ok td.s{color:#137333;font-weight:bold}
tr.nf td.s{color:#b3261e;font-weight:bold}
tr.oth td.s{color:#b06000;font-weight:bold}
tr.err td.s{color:#9400d3;font-weight:bold}
.toolbar{margin:10px 0;display:flex;gap:8px;flex-wrap:wrap}
button{padding:5px 12px;cursor:pointer}
#q{padding:5px;width:320px}
</style></head><body>
<h1>Sitemap link check</h1>
<div class="cards">
<div class="card"><b>__TOTAL__</b>total</div>
<div class="card"><b class="ok">__OK__</b>200</div>
<div class="card"><b class="nf">__NF__</b>404</div>
<div class="card"><b class="oth">__OTH__</b>other</div>
<div class="card"><b class="err">__ERR__</b>error</div>
</div>
<div class="toolbar">
<button data-f="all">All</button><button data-f="ok">200</button>
<button data-f="nf">404</button><button data-f="oth">other</button>
<button data-f="err">error</button>
<input id="q" placeholder="filter by URL substring...">
</div>
<table><thead><tr><th>#</th><th>sitemap URL</th><th>checked URL</th><th>status</th><th>redirects</th></tr></thead>
<tbody id="tb"></tbody></table>
<script>
var DATA = __DATA__;
var f = "all", q = "";
function render(){
  var tb = document.getElementById("tb"), h = "", n = 0;
  for (var i = 0; i < DATA.length; i++){
    var r = DATA[i];
    if (f !== "all" && r.k !== f) continue;
    if (q && r.u.indexOf(q) < 0 && r.c.indexOf(q) < 0) continue;
    n++;
    h += "<tr class='" + r.k + "'><td>" + n + "</td><td>" + r.u +
         "</td><td>" + r.c + "</td><td class='s'>" + r.s +
         "</td><td>" + r.r + "</td></tr>";
  }
  tb.innerHTML = h;
}
document.querySelectorAll("button").forEach(function(b){
  b.onclick = function(){ f = b.getAttribute("data-f"); render(); };
});
document.getElementById("q").oninput = function(e){ q = e.target.value; render(); };
render();
</script></body></html>"""

# escape URLs for HTML (data is inserted as text, not attributes)
for r in data:
    r["u"] = html.escape(r["u"])
    r["c"] = html.escape(r["c"])
    r["s"] = html.escape(r["s"])

page = page.replace("__TOTAL__", str(total)).replace("__OK__", str(groups["200"]))
page = page.replace("__NF__", str(groups["404"])).replace("__OTH__", str(groups["other"]))
page = page.replace("__ERR__", str(groups["error"]))
page = page.replace("__DATA__", json.dumps(data))
open(html_path, "w", encoding="utf-8").write(page)
print(f"wrote {html_path}: {total} urls "
      f"(200:{groups['200']} 404:{groups['404']} other:{groups['other']} error:{groups['error']})")
EOF

echo "Open $OUTDIR/sitemap-check-report.html in your browser (double-click works, no server needed)."
