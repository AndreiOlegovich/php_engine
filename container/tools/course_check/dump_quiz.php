<?php
// version 1.0.0
// usage: php dump_quiz.php PAGE_OR_DIR [PAGE_OR_DIR ...]
// Prints a JSON list of the questions of every LessonQuiz::render() call:
// {path, file, n, q, options, answer, why}.
//
// token_get_all() finds the first argument of the call, so the array is read
// from the source text; only that literal expression is evaluated, the page
// itself is never included and none of its code runs.

declare(strict_types=1);

/**
 * The .php files behind one path, folders walked recursively.
 *
 * @return list<string>
 */
function collect_pages(string $path): array
{
    if (!is_dir($path)) {
        return array($path);
    }
    $files = array();
    $iterator = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator($path, FilesystemIterator::SKIP_DOTS)
    );
    foreach ($iterator as $file) {
        if ($file->isFile() && strtolower($file->getExtension()) === 'php') {
            $files[] = $file->getPathname();
        }
    }
    sort($files);
    return $files;
}

/**
 * True when the tokens at $i are LessonQuiz::render(.
 *
 * @param list<array{0: int, 1: string, 2: int}|string> $tokens
 * @param list<string> $texts
 */
function is_render_call(array $tokens, array $texts, int $i): bool
{
    if (!isset($texts[$i + 3])) {
        return false;
    }
    $class = $tokens[$i];
    $name = $tokens[$i + 2];
    if (!is_array($class) || $class[0] !== T_STRING || $class[1] !== (getenv('SITE_QUIZ_CLASS') ?: 'LessonQuiz')) {
        return false;
    }
    if (!is_array($name) || $name[0] !== T_STRING || $name[1] !== 'render') {
        return false;
    }
    return $texts[$i + 1] === '::' && $texts[$i + 3] === '(';
}

/**
 * The first argument of every LessonQuiz::render() call, as source text.
 *
 * @return list<string>
 */
function render_arguments(string $source): array
{
    $tokens = token_get_all($source);
    $texts = array();
    foreach ($tokens as $token) {
        $texts[] = is_array($token) ? $token[1] : $token;
    }
    $count = count($texts);
    $arguments = array();
    for ($i = 0; $i < $count; $i++) {
        if (!is_render_call($tokens, $texts, $i)) {
            continue;
        }
        $depth = 1;
        $argument = '';
        $unsupported = '';
        for ($j = $i + 4; $j < $count; $j++) {
            $text = $texts[$j];
            if ($text === '(' || $text === '[' || $text === '{') {
                $depth++;
            } elseif ($text === ')' || $text === ']' || $text === '}') {
                $depth--;
                if ($depth === 0) {
                    break;
                }
            } elseif ($depth === 1 && $text === ',') {
                break;
            }
            if (is_array($tokens[$j]) && $tokens[$j][0] === T_VARIABLE) {
                $unsupported = 'render() is called with a variable, not an array literal';
            }
            $argument .= $text;
        }
        $arguments[] = $unsupported === '' ? $argument : '!' . $unsupported;
    }
    return $arguments;
}

/**
 * One question row, or an error row when the argument cannot be read.
 *
 * @return list<array<string, mixed>>
 */
function read_page(string $path): array
{
    $source = file_get_contents($path);
    if ($source === false) {
        return array(array('path' => $path, 'file' => basename($path), 'error' => 'cannot read the file'));
    }
    $rows = array();
    foreach (render_arguments($source) as $argument) {
        if (str_starts_with($argument, '!')) {
            $rows[] = array('path' => $path, 'file' => basename($path), 'error' => substr($argument, 1));
            continue;
        }
        $expression = trim($argument);
        if (!preg_match('/^(array\s*\(|\[)/i', $expression)) {
            $rows[] = array(
                'path' => $path,
                'file' => basename($path),
                'error' => 'render() is not called with an array literal',
            );
            continue;
        }
        try {
            /** @var mixed $questions */
            $questions = eval('return ' . $expression . ';');
        } catch (Throwable $error) {
            $rows[] = array(
                'path' => $path,
                'file' => basename($path),
                'error' => 'the array cannot be read: ' . $error->getMessage(),
            );
            continue;
        }
        if (!is_array($questions)) {
            $rows[] = array('path' => $path, 'file' => basename($path), 'error' => 'the argument is not an array');
            continue;
        }
        $number = 0;
        foreach ($questions as $question) {
            $number++;
            if (!is_array($question)) {
                $rows[] = array('path' => $path, 'file' => basename($path), 'n' => $number, 'error' => 'not an array');
                continue;
            }
            $options = isset($question['options']) && is_array($question['options'])
                ? array_values(array_map('strval', $question['options']))
                : array();
            $rows[] = array(
                'path' => $path,
                'file' => basename($path),
                'n' => $number,
                'q' => isset($question['q']) ? (string) $question['q'] : '',
                'options' => $options,
                'answer' => $question['answer'] ?? null,
                'why' => isset($question['why']) ? (string) $question['why'] : '',
            );
        }
    }
    return $rows;
}

$rows = array();
$arguments = array_slice($argv, 1);
if ($arguments === array()) {
    fwrite(STDERR, "usage: php dump_quiz.php PAGE_OR_DIR [PAGE_OR_DIR ...]\n");
    exit(2);
}
foreach ($arguments as $argument) {
    foreach (collect_pages($argument) as $path) {
        $rows = array_merge($rows, read_page($path));
    }
}
echo json_encode($rows, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR), "\n";
