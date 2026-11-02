#!/usr/bin/env php
<?php

/**
 * Fix PSR12.Classes.ClosingBrace.StatementAfter ("Closing brace must not be
 * followed by any comment or statement on the same line").
 *
 * That sniff is not auto-fixable ([ ]), so this script does the job with
 * PHP's own tokenizer (exact strings/heredoc handling, exact brace
 * matching) instead of regex.
 *
 * Usage:
 *   php scripts/fix-closing-brace.php [--force] [paths...]
 *
 * Modes:
 *   default  fix only clear cases: a lone ";" after "}" that closes a
 *            named function/class/interface/trait/enum, a control-structure
 *            block, declare or a bracketed namespace. Anything doubtful is
 *            left untouched and reported as skipped (exit 1).
 *   --force  additionally split "} //comment" and "} statement" tails onto
 *            their own line, and move a required ";" (closure, anonymous
 *            class) to its own line. Still never breaks code: every touched
 *            file must pass "php -l" or it is restored byte-for-byte.
 *   paths    files and/or directories (default: src/). Directories are
 *            scanned for *.php without descending into symlinked dirs.
 *
 * Safety:
 *   - ";" is dropped only when the brace owner provably does not need it.
 *     Closures ($f = function () {...};) and anonymous classes
 *     ($o = new class {...};) REQUIRE the semicolon: always skipped by
 *     default, moved (never dropped) under --force.
 *   - "} else {", "} catch", "} finally {", "} while" continuations are
 *     another sniff's business and are never touched.
 *   - CRLF/LF endings preserved; php -l gate before and after.
 */

const CB_DROPPABLE = 'droppable';
const CB_REQUIRED = 'required';

/**
 * CLI entry point. Returns exit code: 0 when nothing was skipped.
 *
 * @param list<string> $argv
 */
function main(array $argv): int
{
    array_shift($argv);
    $force = false;
    $paths = [];
    foreach ($argv as $arg) {
        if ($arg === '--force') {
            $force = true;
        } elseif ($arg === '-h' || $arg === '--help') {
            echo "Usage: php scripts/fix-closing-brace.php [--force] [paths...]\n";
            return 0;
        } elseif (str_starts_with($arg, '-')) {
            fwrite(STDERR, "ERROR: unknown option: $arg\n");
            return 2;
        } else {
            $paths[] = $arg;
        }
    }
    if ($paths === []) {
        $paths = ['src'];
    }
    // Resolve against the project root (parent of scripts/) when the
    // invocation cwd is elsewhere, so the tool works from any directory.
    $root = dirname(__DIR__);
    foreach ($paths as &$p) {
        if (!file_exists($p) && file_exists($root . '/' . $p)) {
            $p = $root . '/' . $p;
        }
    }
    unset($p);
    $files = collectPhpFiles($paths);
    if ($files === null) {
        return 2;
    }
    $fixed = 0;
    $skipped = 0;
    foreach ($files as $file) {
        [$f, $s] = processFile($file, $force);
        $fixed += $f;
        $skipped += $s;
    }
    echo "fix: $fixed brace(s) fixed, $skipped skipped.\n";
    return $skipped === 0 ? 0 : 1;
}

/**
 * Expand files/dirs to a sorted list of *.php paths.
 *
 * @param list<string> $paths
 * @return list<string>|null Null when a path does not exist.
 */
function collectPhpFiles(array $paths): ?array
{
    $files = [];
    foreach ($paths as $p) {
        if (is_file($p)) {
            $files[] = $p;
        } elseif (is_dir($p)) {
            $it = new RecursiveIteratorIterator(
                new RecursiveDirectoryIterator($p, FilesystemIterator::SKIP_DOTS)
            );
            foreach ($it as $file) {
                if (substr($file->getPathname(), -4) === '.php') {
                    $files[] = $file->getPathname();
                }
            }
        } else {
            fwrite(STDERR, "ERROR: path not found: $p\n");
            return null;
        }
    }
    sort($files);
    return $files;
}

/**
 * Run "php -l" with the interpreter executing this script.
 */
function phpLintOk(string $file): bool
{
    $out = [];
    $rc = 0;
    exec(PHP_BINARY . ' -l ' . escapeshellarg($file) . ' 2>&1', $out, $rc);
    return $rc === 0;
}

/**
 * Tokenize with byte offsets and start lines.
 *
 * @return array{flat: list<array{mixed,int}>, offsets: list<int>, code: string}
 */
function tokenize(string $code): array
{
    $flat = [];
    $offsets = [];
    $line = 1;
    $off = 0;
    foreach (token_get_all($code) as $t) {
        $text = is_array($t) ? $t[1] : $t;
        $flat[] = [$t, $line];
        $offsets[] = $off;
        $line += substr_count($text, "\n");
        $off += strlen($text);
    }
    return ['flat' => $flat, 'offsets' => $offsets, 'code' => $code];
}

/**
 * Map each "}" token index to its matching "{" token index.
 *
 * Only bare "{" counts as a block opener; "{$" / "${" inside strings are
 * tracked for balance but never treated as blocks.
 *
 * @param list<array{mixed,int}> $flat
 * @return array<int,int>
 */
function buildBraceMap(array $flat): array
{
    $stack = [];
    $pairs = [];
    foreach ($flat as $idx => $e) {
        $t = $e[0];
        $ch = is_array($t) ? null : $t;
        if ($ch === '{' || (is_array($t) && in_array($t[0], [T_CURLY_OPEN, T_DOLLAR_OPEN_CURLY_BRACES], true))) {
            $stack[] = $idx;
        } elseif ($ch === '}') {
            $open = array_pop($stack);
            if ($open !== null) {
                $pairs[$idx] = $open;
            }
        }
    }
    return $pairs;
}

/**
 * Previous significant (non-whitespace/comment) token index, or -1.
 *
 * @param list<array{mixed,int}> $flat
 */
function prevSignificant(array $flat, int $idx): int
{
    $j = $idx - 1;
    $n = count($flat);
    while ($j >= 0) {
        $t = $flat[$j][0];
        if (is_array($t) && in_array($t[0], [T_WHITESPACE, T_COMMENT, T_DOC_COMMENT], true)) {
            $j--;
            continue;
        }
        break;
    }
    return $j;
}

/**
 * Next significant token index, or count($flat).
 *
 * @param list<array{mixed,int}> $flat
 */
function nextSignificant(array $flat, int $idx): int
{
    $k = $idx + 1;
    $n = count($flat);
    while ($k < $n) {
        $t = $flat[$k][0];
        if (is_array($t) && in_array($t[0], [T_WHITESPACE, T_COMMENT, T_DOC_COMMENT], true)) {
            $k++;
            continue;
        }
        break;
    }
    return $k;
}

/**
 * Classify a block opener: does its ";" belong to an expression?
 *
 * Returns CB_REQUIRED for closures and anonymous classes (their ";" is
 * mandatory), CB_DROPPABLE for named functions/classes/interfaces/traits/
 * enums, control structures, declare and bracketed namespaces.
 *
 * @param list<array{mixed,int}> $flat
 */
function classifyBrace(array $flat, int $openIdx): string
{
    $j = prevSignificant($flat, $openIdx);
    if ($j < 0) {
        return CB_DROPPABLE;
    }
    // Skip balanced (...) so `function name(...) {` resolves to `function`.
    $t = $flat[$j][0];
    if (!is_array($t) && $t === ')') {
        $depth = 0;
        while ($j >= 0) {
            $tj = $flat[$j][0];
            $ch = is_array($tj) ? null : $tj;
            if ($ch === ')') {
                $depth++;
            } elseif ($ch === '(') {
                $depth--;
                if ($depth === 0) {
                    $j--;
                    break;
                }
            }
            $j--;
        }
        $j = prevSignificant($flat, $j + 1);
    }
    if ($j < 0) {
        return CB_DROPPABLE;
    }
    // A bare name means `function name(` / `class Name`: step to the keyword.
    $t = $flat[$j][0];
    if (is_array($t) && $t[0] === T_STRING) {
        $j = prevSignificant($flat, $j);
        if ($j < 0) {
            return CB_DROPPABLE;
        }
        $t = $flat[$j][0];
    }
    if (!is_array($t)) {
        return CB_DROPPABLE;
    }
    switch ($t[0]) {
        case T_FUNCTION:
            $k = nextSignificant($flat, $j);
            $after = $k < count($flat) ? $flat[$k][0] : null;
            $ac = is_array($after) ? $after[1] : $after;
            return ($ac === '(' || $ac === '&') ? CB_REQUIRED : CB_DROPPABLE;
        case T_CLASS:
            // `new #[Attr] class {...}`: skip attributes back to `new`.
            $m = prevSignificant($flat, $j);
            if ($m >= 0 && $flat[$m][0] === ']') {
                $depth = 0;
                while ($m >= 0) {
                    $tm = $flat[$m][0];
                    $ch = is_array($tm) ? null : $tm;
                    if ($ch === ']') {
                        $depth++;
                    } elseif ($ch === '[') {
                        $depth--;
                        if ($depth === 0) {
                            $m--;
                            break;
                        }
                    }
                    $m--;
                }
                $m = prevSignificant($flat, $m + 1);
            }
            $pv = $m >= 0 ? $flat[$m][0] : null;
            return (is_array($pv) && $pv[0] === T_NEW) ? CB_REQUIRED : CB_DROPPABLE;
        case T_INTERFACE:
        case T_TRAIT:
        case T_ENUM:
        case T_IF:
        case T_ELSE:
        case T_ELSEIF:
        case T_FOR:
        case T_FOREACH:
        case T_WHILE:
        case T_DO:
        case T_SWITCH:
        case T_TRY:
        case T_CATCH:
        case T_FINALLY:
        case T_DECLARE:
        case T_NAMESPACE:
            return CB_DROPPABLE;
        default:
            // Unknown owner (e.g. "${...}" interpolation): never touch.
            return CB_REQUIRED;
    }
}

/**
 * Describe what follows a "}" token on its line.
 *
 * One of: SEMI_ALONE (";" then EOL), SEMI_COMMENT (";" plus only
 * comments), CODE (anything else code-like), CONTINUATION ("else",
 * "catch", "finally", "while" — another sniff's business).
 *
 * @param list<array{mixed,int}> $flat
 */
function tailKind(array $flat, int $closeIdx): string
{
    $n = count($flat);
    $line = $flat[$closeIdx][1];
    $k = $closeIdx + 1;
    $seenSemi = false;
    $seenCode = false;
    while ($k < $n && $flat[$k][1] === $line) {
        $t = $flat[$k][0];
        if (is_array($t)) {
            if (in_array($t[0], [T_WHITESPACE, T_COMMENT, T_DOC_COMMENT], true)) {
                $k++;
                continue;
            }
            if (!$seenCode && !$seenSemi && in_array($t[0], [T_ELSE, T_ELSEIF, T_CATCH, T_FINALLY, T_WHILE], true)) {
                return 'CONTINUATION';
            }
            $seenCode = true;
        } else {
            if ($t === ';') {
                $seenSemi = true;
            } elseif ($t === '?' && ($flat[$k + 1][0] ?? null) === '>') {
                break; // close tag ends the line's code
            } else {
                $seenCode = true;
            }
        }
        $k++;
    }
    if ($seenCode) {
        return 'CODE';
    }
    return $seenSemi ? 'SEMI' : 'CLEAN';
}

/**
 * Decide one flagged "}" line. Returns [action, reason] where action is
 * DROP_SEMI, SPLIT_TAIL, SPLIT_SEMI or SKIP.
 *
 * @param list<array{mixed,int}> $flat
 * @return array{string,string}
 */
function decideFix(array $flat, int $closeIdx, bool $force, string $owner): array
{
    $tail = tailKind($flat, $closeIdx);
    if ($tail === 'CLEAN' || $tail === 'CONTINUATION') {
        return ['SKIP', $tail === 'CLEAN' ? 'nothing after brace' : 'control continuation'];
    }
    // Tail holds ";" plus at most comments/trailing whitespace here.
    $hasComment = false;
    $line = $flat[$closeIdx][1];
    for ($k = $closeIdx + 1, $n = count($flat); $k < $n && $flat[$k][1] === $line; $k++) {
        $t = $flat[$k][0];
        if (is_array($t) && ($t[0] === T_COMMENT || $t[0] === T_DOC_COMMENT)) {
            $hasComment = true;
        }
    }
    if ($tail === 'CODE') {
        if (!$force) {
            return ['SKIP', 'code after brace'];
        }
        return ['SPLIT_TAIL', ''];
    }
    if ($owner === CB_REQUIRED) {
        // The ";" is mandatory (closure, anonymous class): only --force
        // moves it to its own line; default leaves it alone.
        return $force ? ['SPLIT_SEMI', ''] : ['SKIP', 'required semicolon'];
    }
    if ($hasComment) {
        return $force ? ['SPLIT_TAIL', ''] : ['SKIP', 'trailing comment'];
    }
    return ['DROP_SEMI', ''];
}

/**
 * Apply one decided edit to the line list. Returns true when applied.
 *
 * Edits are byte-level but line-local; $lines keep their original EOLs.
 *
 * @param list<string> $lines File lines (EOLs included), modified in place.
 * @param list<array{mixed,int}> $flat
 * @param list<int> $offsets Byte offset per token.
 */
function applyEdit(
    array &$lines,
    array $flat,
    array $offsets,
    string $code,
    int $closeIdx,
    string $action,
    string $indent
): bool {
    $lineNo = $flat[$closeIdx][1];
    $lineStart = strrpos(substr($code, 0, $offsets[$closeIdx]), "\n");
    $lineStart = $lineStart === false ? 0 : $lineStart + 1;
    $closeOff = $offsets[$closeIdx] - $lineStart; // offset of "}" inside its line
    $line = $lines[$lineNo - 1];
    if (!isset($line[$closeOff]) || $line[$closeOff] !== '}') {
        return false;
    }
    if ($action === 'DROP_SEMI') {
        $semi = strpos($line, ';', $closeOff);
        if ($semi === false) {
            return false;
        }
        $lines[$lineNo - 1] = substr($line, 0, $semi) . substr($line, $semi + 1);
        return true;
    }
    // SPLIT_*: "}" stays, everything after it moves to a new indented line.
    $head = rtrim(substr($line, 0, $closeOff + 1));
    $tail = ltrim(substr($line, $closeOff + 1));
    if ($action === 'SPLIT_TAIL' && str_starts_with($tail, ';')) {
        $tail = ltrim(substr($tail, 1)); // droppable owner: ";" goes away
        if ($tail === '' || $tail === "\n" || $tail === "\r\n") {
            return false;
        }
    }
    $lineEol = str_ends_with($line, "\r\n") ? "\r\n" : "\n";
    $lines[$lineNo - 1] = $head . $lineEol;
    array_splice($lines, $lineNo, 0, [$indent . $tail]);
    return true;
}

/**
 * Fix one file. Returns [fixedCount, skippedCount].
 *
 * @return array{int,int}
 */
function processFile(string $file, bool $force): array
{
    if (!phpLintOk($file)) {
        echo "SKIPPED $file (pre-existing php -l failure)\n";
        return [0, 1];
    }
    $code = file_get_contents($file);
    if ($code === false || strpos($code, '<?') === false) {
        return [0, 0];
    }
    $t = tokenize($code);
    $flat = $t['flat'];
    $offsets = $t['offsets'];
    $pairs = buildBraceMap($flat);
    $lines = file($file);
    if ($lines === false) {
        return [0, 0];
    }
    $fixed = 0;
    $skipped = [];
    // Collect decisions first (token indexes stay valid until we write).
    $jobs = [];
    foreach ($pairs as $closeIdx => $openIdx) {
        $owner = classifyBrace($flat, $openIdx);
        [$action, $reason] = decideFix($flat, $closeIdx, $force, $owner);
        if ($action === 'SKIP') {
            // Bare "}" lines and control continuations are normal code.
            if ($reason === 'nothing after brace' || $reason === 'control continuation') {
                continue;
            }
            $skipped[$flat[$closeIdx][1]] = $reason;
        } else {
            $jobs[] = [$closeIdx, $action];
        }
    }
    if ($jobs === []) {
        if ($skipped === []) {
            echo "UNCHANGED $file\n";
        } else {
            echo "UNCHANGED $file skipped=" . count($skipped) . "\n";
        }
        return [0, count($skipped)];
    }
    // Apply back-to-front so earlier offsets stay valid (line-local anyway).
    usort($jobs, static fn($a, $b) => $b[0] <=> $a[0]);
    $indentOf = static function (string $line): string {
        return substr($line, 0, strspn($line, " \t"));
    };
    foreach ($jobs as [$closeIdx, $action]) {
        $lineNo = $flat[$closeIdx][1];
        if (
            !applyEdit(
                $lines,
                $flat,
                $offsets,
                $code,
                $closeIdx,
                $action,
                $indentOf($lines[$lineNo - 1])
            )
        ) {
            $skipped[$lineNo] = 'edit failed';
        } else {
            $fixed++;
        }
    }
    $backup = $code;
    file_put_contents($file, implode('', $lines));
    if (!phpLintOk($file)) {
        file_put_contents($file, $backup);
        echo "FAILED $file (php -l gate, restored)\n";
        return [0, 1];
    }
    echo "FIXED $file lines=$fixed skipped=" . count($skipped) . "\n";
    foreach ($skipped as $ln => $reason) {
        echo "  skipped $file:$ln ($reason)\n";
    }
    return [$fixed, count($skipped)];
}

exit(main($argv));
