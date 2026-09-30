<?php

namespace PhpEngine\Modules;

# Yandex.Metrica counter module (correct spelling: Metrica, one k).
#
# Renders counters from environment. Code defaults below are placeholders
# (no real IDs ship here) — set real ones via environment (see
# container/scripts/site.env.example, YANDEX_METRICA_*):
#   YANDEX_METRICA_ID       production counter
#   YANDEX_METRICA_DEV_ID   dev counter
#   YANDEX_METRICA_AO_ID    legacy counter
#   YANDEX_METRICA_URN_ID   legacy counter
#
# Lookup order per ID: getenv() / $_ENV / $_SERVER, then the file pointed at
# by $SITE_ENV (if set), then ~/.config/aredel/ftp-creds.env (optional
# overrides, see deploy-ftp.creds.example), then the code default below.
class Metrica
{
    private const DEFAULT_AREDEL_ID = '00000000';
    private const DEFAULT_DEV_ID = '00000000';
    private const DEFAULT_AO_ID = '00000000';
    private const DEFAULT_URN_ID = '00000000';

    /** @var array<string,array<string,string>> parsed env files by path */
    private static array $fileCache = [];

    private static function resolveId(string $var, string $default): string
    {
        $v = getenv($var);
        if (is_string($v) && $v !== '' && ctype_digit($v)) {
            return $v;
        }
        if (isset($_ENV[$var]) && is_string($_ENV[$var]) && $_ENV[$var] !== '' && ctype_digit($_ENV[$var])) {
            return $_ENV[$var];
        }
        $srv = $_SERVER[$var] ?? null;
        if (is_string($srv) && $srv !== '' && ctype_digit($srv)) {
            return $srv;
        }
        foreach (self::envFileCandidates() as $path) {
            $fromFile = self::readVarFromFile($path, $var);
            if ($fromFile !== null && $fromFile !== '' && ctype_digit($fromFile)) {
                return $fromFile;
            }
        }
        return $default;
    }

    /** @return list<string> */
    private static function envFileCandidates(): array
    {
        $out = [];
        foreach (['SITE_ENV'] as $ptr) {
            $p = getenv($ptr);
            if (is_string($p) && $p !== '') {
                $out[] = $p;
            }
        }
        if (isset($_ENV['SITE_ENV']) && is_string($_ENV['SITE_ENV']) && $_ENV['SITE_ENV'] !== '') {
            $out[] = $_ENV['SITE_ENV'];
        }
        if (isset($_SERVER['SITE_ENV']) && is_string($_SERVER['SITE_ENV']) && $_SERVER['SITE_ENV'] !== '') {
            $out[] = $_SERVER['SITE_ENV'];
        }
        $home = getenv('HOME');
        if (is_string($home) && $home !== '') {
            $out[] = $home . '/.config/aredel/ftp-creds.env';
        }
        return $out;
    }

    private static function readVarFromFile(string $path, string $var): ?string
    {
        if (!is_file($path) || !is_readable($path)) {
            return null;
        }
        if (!isset(self::$fileCache[$path])) {
            $parsed = [];
            $lines = @file($path, FILE_IGNORE_NEW_LINES);
            if (is_array($lines)) {
                foreach ($lines as $line) {
                    $line = trim($line);
                    if ($line === '' || $line[0] === '#') {
                        continue;
                    }
                    $pat = '/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*'
                        . '(?:"([^"]*)"|\'([^\']*)\'|([^\s#]+))/';
                    if (!preg_match($pat, $line, $m)) {
                        continue;
                    }
                    $val = ($m[2] ?? '') !== '' ? $m[2] : ((($m[3] ?? '') !== '') ? $m[3] : $m[4]);
                    $parsed[$m[1]] = $val;
                }
            }
            self::$fileCache[$path] = $parsed;
        }
        return self::$fileCache[$path][$var] ?? null;
    }

    public static function render(string|int $id, ?string $initOptions = null): string
    {
        $id = (string) $id;
        if ($id === '' || !ctype_digit($id)) {
            $id = self::DEFAULT_AREDEL_ID;
        }
        $safe = htmlspecialchars($id, ENT_QUOTES, 'UTF-8');
        $init = $initOptions ?? 'clickmap:true, trackLinks:true, accurateTrackBounce:true, webvisor:true';
        $loader = '(function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};'
            . 'm[i].l=1*new Date();k=e.createElement(t),a=e.getElementsByTagName(t)[0],'
            . 'k.async=1,k.src=r,a.parentNode.insertBefore(k,a)})'
            . '(window, document, "script", "https://mc.yandex.ru/metrika/tag.js", "ym")';
        return '<!-- Yandex.Metrika counter --><script type="text/javascript" >'
            . $loader
            . '; ym(' . $safe . ', "init", { ' . $init . ' });</script>'
            . '<noscript><div><img src="https://mc.yandex.ru/watch/' . $safe . '"'
            . ' style="position:absolute; left:-9999px;" alt="" /></div></noscript>'
            . '<!-- /Yandex.Metrika counter -->';
    }

    public function aredel(): string
    {
        return self::render(self::resolveId('YANDEX_METRICA_ID', self::DEFAULT_AREDEL_ID));
    }

    public function dev(): string
    {
        return self::render(self::resolveId('YANDEX_METRICA_DEV_ID', self::DEFAULT_DEV_ID));
    }

    public function ao(): string
    {
        // Historic ao payload also enables hash tracking + ecommerce layer.
        return self::render(
            self::resolveId('YANDEX_METRICA_AO_ID', self::DEFAULT_AO_ID),
            'clickmap:true,trackLinks:true,accurateTrackBounce:true,webvisor:true,trackHash:true,ecommerce:"dataLayer"'
        );
    }

    public function urn(): string
    {
        return self::render(self::resolveId('YANDEX_METRICA_URN_ID', self::DEFAULT_URN_ID));
    }

    /**
     * All known counters, rendered. Keys: aredel, dev, ao, urn.
     *
     * @return array<string,string>
     */
    public function all(): array
    {
        return [
            'aredel' => $this->aredel(),
            'dev' => $this->dev(),
            'ao' => $this->ao(),
            'urn' => $this->urn(),
        ];
    }
}
