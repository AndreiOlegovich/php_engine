<?php

# version 0.1.0
# Batch runner of the site search for tests/test_search_quality.py:
#
#   php run_search.php <docroot> <lang> <queries.tsv>
#
# One search per TSV line (query, label, expected URL; tab separated) with
# src/.php/SiteSearch.php of the docroot and its search/site_index.<lang>.json,
# the same class and index as the search page, without the captcha. Prints
# one JSON object per line: query, label, expected, total, corrected, the
# first ten result URLs (top) and the rank of the expected URL among the
# links the search shows (null when it is not one of them).

if ($argc < 4) {
    fwrite(STDERR, "usage: php run_search.php <docroot> <lang> <queries.tsv>\n");
    exit(2);
}
require_once $argv[1] . '/.php/SiteSearch.php';

$search = new SiteSearch($argv[1], $argv[2]);
if (!$search->isAvailable()) {
    fwrite(STDERR, 'index not available for ' . $argv[2] . "\n");
    exit(1);
}
$lines = file($argv[3], FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
if ($lines === false) {
    fwrite(STDERR, 'cannot read ' . $argv[3] . "\n");
    exit(1);
}
foreach ($lines as $line) {
    [$query, $label, $expected] = array_pad(explode("\t", $line), 3, '');
    $found = $search->search($query);
    $urls = array_map(static fn (array $page): string => (string) $page['url'], $found['results']);
    $rank = $expected === '' ? false : array_search($expected, $urls, true);
    echo json_encode([
        'query' => $query,
        'label' => $label,
        'expected' => $expected,
        'total' => $found['total'],
        'corrected' => $found['corrected'],
        'top' => array_slice($urls, 0, 10),
        'rank' => $rank === false ? null : $rank + 1,
    ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE), "\n";
}
