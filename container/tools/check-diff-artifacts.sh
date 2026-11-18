#!/usr/bin/env bash
# version 0.2.0
# Block the byte-level damage that scripted rewrites (sed, Python
# read/write round trips, editors) leave in a diff, and nothing else.
#
# Blocking (ARTIFACT, exit 1), each judged against the base side:
#   1. line endings: CRLF lost in a CRLF file together with other changes,
#      an LF file flipped to CRLF, or a file with one ending style left with
#      mixed endings. A file whose only change is CRLF -> LF passes with a
#      NOTE, so a repository can move to LF in conversion-only commits.
#   2. U+FFFD replacement chars introduced into an existing file (a lossy
#      decode)
#   3. mojibake introduced: UTF-8 decoded as cp1252/latin-1 or cp1251 and
#      written back, 10 or more new sequences (an e-acute turned into
#      A-tilde + copyright sign, Russian turned into runs of R/S pairs)
#   4. a valid UTF-8 file no longer valid UTF-8 (saved as cp1251/latin-1)
#   5. a UTF-8 BOM added to an existing file or to a new PHP file (PHP
#      prints it before any header)
# Not blocking (NOTE, exit stays 0): whitespace-only diffs, a BOM removed,
# stray CRs dropped from a mostly-LF file, and a new file with mixed endings,
# U+FFFD or (not PHP) a BOM: a new file has no base to be damaged against.
# A 1:1 rewrite of many lines (translation, path rename, regenerated
# sitemap) is an ordinary change and is not reported: the EOL and encoding
# checks above catch the damage the old "full-file rewrite" rule stood for.
# The rolling feed JSON files skip the line-ending and whitespace checks:
# the feed writer serializes them whole. Generated CSS concatenations
# (css_pipeline/build/) take their line endings from their sources, which
# are checked themselves, so a line-ending hit there is only a NOTE.
#
# Usage:
#   check-diff-artifacts.sh [--staged | --all | --commit REV]
#     (default)     unstaged changes: index -> working tree
#     --staged      what the next commit records: HEAD -> index (pre-commit)
#     --all         HEAD -> working tree, staged and unstaged together
#     --commit REV  an existing commit: its first parent -> REV
# Exit 0 when clean (notes allowed), 1 when artifacts are found, 2 on a
# usage error. Untracked, deleted, binary, symlink and submodule entries
# are skipped. A renamed or copied file is compared with its source.
set -u

usage() { sed -n '/^# Usage:/,/^# are skipped/{s/^# \{0,1\}//;p}' "$0"; }

MODE="worktree"
REV=""
while [ $# -gt 0 ]; do
    case "$1" in
        --staged) MODE="staged" ;;
        --all) MODE="all" ;;
        --commit)
            if [ $# -lt 2 ]; then usage >&2; exit 2; fi
            MODE="commit"; REV="$2"; shift ;;
        --commit=*) MODE="commit"; REV="${1#--commit=}" ;;
        -h|--help) usage; exit 0 ;;
        *) echo "check-diff-artifacts: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

cd "$(git rev-parse --show-toplevel)" || exit 2

EMPTY_TREE=$(git hash-object -t tree /dev/null)
head_or_empty() {
    if git rev-parse -q --verify HEAD >/dev/null; then echo HEAD; else echo "$EMPTY_TREE"; fi
}
# The same pairs for --raw (blob ids), --numstat and -b --numstat.
case "$MODE" in
    worktree) LABEL="worktree"; RANGE=(--diff-filter=ACM) ;;
    staged) LABEL="staged"; RANGE=(--cached -M --diff-filter=ACMR "$(head_or_empty)") ;;
    all) LABEL="all"; RANGE=(-M --diff-filter=ACMR "$(head_or_empty)") ;;
    commit)
        if ! COMMIT=$(git rev-parse -q --verify "$REV^{commit}"); then
            echo "check-diff-artifacts: not a commit: $REV" >&2; exit 2
        fi
        PARENT=$(git rev-parse -q --verify "$COMMIT^1") || PARENT="$EMPTY_TREE"
        LABEL="commit ${COMMIT:0:9}"
        RANGE=(-M --diff-filter=ACMR "$PARENT" "$COMMIT") ;;
esac
GIT_DIFF=(git diff -z --no-ext-diff --no-color)

TMP=$(mktemp -d) || exit 2
trap 'rm -rf "$TMP"' EXIT

HAVE_PCRE=1
printf 'x\n' | LC_ALL=C grep -qP 'x' 2>/dev/null || HAVE_PCRE=0
HAVE_ICONV=1
command -v iconv >/dev/null 2>&1 || HAVE_ICONV=0

# Double-encoded UTF-8, as bytes (this file stays ASCII so it never matches
# itself). Latin: a lead byte C2-DF read as cp1252 or latin-1 (U+00C2-U+00DF)
# followed by what its continuation byte turns into; E2 80 xx punctuation
# (dashes, quotes) read as cp1252 or latin-1. Cyrillic: a lead D0/D1 read as
# cp1251 (U+0420/U+0421) followed by what the second byte turns into, minus
# the pairs real Russian text has (before NBSP, guillemets, degree, ellipsis,
# dashes, quotes, yo); E2 80 xx punctuation read as cp1251.
MOJIBAKE='\xC3[\x82-\x9F](?:\xC2[\x80-\xBF]|\xE2\x80[\x93-\xBA]|\xE2\x82\xAC|\xE2\x84\xA2|\xC5[\x92\x93\xA0\xA1\xB8\xBD\xBE]|\xC6\x92|\xCB[\x86\x9C])|\xC3\xA2(?:\xE2\x82\xAC|\xC2[\x80-\x9F])|\xD0[\xA0\xA1](?:\xD0[\x82-\x8F]|\xD1[\x92-\x9F]|\xD2[\x90\x91]|\xC2[\xA4-\xAA\xAC-\xAF\xB1-\xBA\xBC-\xBF]|\xE2\x80[\x9A\x9E\xA0\xA1\xA2\xB0\xB9\xBA]|\xE2\x82\xAC|\xE2\x84[\x96\xA2])|\xD0\xB2\xD0\x82'
# New sequences needed: an article may quote a short sample (one mojibake
# word is 2-6 sequences); one damaged line of Russian is 20 or more.
MOJIBAKE_MIN=10

FEED_DIR="container/src/${SITE_FEED_DIR:-feed}"
FOUND=0
NOTES=0
report() { echo "ARTIFACT: $1"; FOUND=$((FOUND + 1)); }
note() { echo "NOTE: $1"; NOTES=$((NOTES + 1)); }

is_zero() { case "$1" in *[!0]*) return 1 ;; *) return 0 ;; esac; }
lines() { local n; n=$(wc -l < "$1"); echo $((n)); }
cr_lines() { LC_ALL=C grep -ac $'\r$' "$1" || true; }
non_ascii() { LC_ALL=C grep -aq $'[\x80-\xff]' "$1"; }
fffd() { LC_ALL=C grep -ac $'\xef\xbf\xbd' "$1" || true; }
mojibake() { local n; n=$(LC_ALL=C grep -aoP "$MOJIBAKE" "$1" 2>/dev/null | wc -l); echo $((n)); }
has_bom() {
    local head=""
    IFS= read -r -n 3 head < "$1" || true   # 3 bytes, or 1-3 chars in a UTF-8 locale
    [[ $head == $'\xef\xbb\xbf'* ]]
}
utf8_error() {   # prints iconv's complaint and fails when $1 is not UTF-8
    local err
    err=$(iconv -f UTF-8 -t UTF-8 "$1" 2>&1 >/dev/null) && return 0
    printf '%s' "${err##*: }"
    return 1
}
# True when $2 is exactly $1 with the CR before each line break removed:
# a conversion to LF and nothing else.
only_cr_removed() { LC_ALL=C sed 's/\r$//' "$1" | cmp -s - "$2"; }
# Ending style of a file: none (no line break), lf, crlf or mixed.
eol_style() {
    local n="$1" c="$2"
    if [ "$n" -eq 0 ]; then echo none
    elif [ "$c" -eq 0 ]; then echo lf
    elif [ "$c" -ge "$n" ]; then echo crlf
    else echo mixed
    fi
}

# $1 path, $2 display name, $3 base file or "", $4 new file.
check_eol() {
    local path="$1" f="$2" base="$3" new="$4" bn=0 bc=0 bs="absent" nn nc ns hit=report
    case "$path" in
        container/css_pipeline/build/*) hit=note ;;
    esac
    nc=$(cr_lines "$new")
    [ -z "$base" ] || bc=$(cr_lines "$base")
    [ "$nc" -gt 0 ] || [ "$bc" -gt 0 ] || return 0   # LF on both sides
    if [ -n "$base" ]; then
        bn=$(lines "$base"); bs=$(eol_style "$bn" "$bc")
    fi
    nn=$(lines "$new"); ns=$(eol_style "$nn" "$nc")
    case "$bs:$ns" in
        crlf:lf)
            if only_cr_removed "$base" "$new"; then
                note "$f [$LABEL]: converted from CRLF to LF, nothing else changed ($nn lines)"
            else
                "$hit" "$f [$LABEL]: CRLF line endings lost (base CRLF on all $bn lines, now LF) together with other changes; a conversion alone passes"
            fi ;;
        crlf:mixed) "$hit" "$f [$LABEL]: mixed line endings, $((nn - nc)) of $nn lines now end in LF in a CRLF file" ;;
        lf:crlf) "$hit" "$f [$LABEL]: line endings flipped from LF to CRLF ($nn lines)" ;;
        lf:mixed) "$hit" "$f [$LABEL]: mixed line endings, $nc of $nn lines now end in CRLF in an LF file" ;;
        mixed:lf)
            if only_cr_removed "$base" "$new"; then
                note "$f [$LABEL]: converted from mixed endings to LF, nothing else changed ($bc of $bn lines had CRLF)"
            elif [ $((bc * 2)) -ge "$bn" ]; then
                "$hit" "$f [$LABEL]: CRLF line endings lost ($bc of $bn base lines had CRLF, now none) together with other changes; a conversion alone passes"
            else
                note "$f [$LABEL]: stray CRs removed ($bc of $bn base lines), the file is LF now"
            fi ;;
        absent:mixed) note "$f [$LABEL]: new file with mixed line endings ($nc of $nn lines CRLF)" ;;
    esac
}

# $1 path, $2 display name, $3 base file or "", $4 new file.
check_encoding() {
    local path="$1" f="$2" base="$3" new="$4" b n err
    if ! non_ascii "$new"; then   # plain ASCII: valid UTF-8, nothing to damage
        if [ -n "$base" ] && has_bom "$base"; then note "$f [$LABEL]: UTF-8 BOM removed"; fi
        return 0
    fi
    n=$(fffd "$new")
    if [ "$n" -gt 0 ] && [ -z "$base" ]; then
        # A new page may show the character on purpose (unichr, /dev/urandom).
        note "$f [$LABEL]: new file with U+FFFD on $n line(s)"
    elif [ "$n" -gt 0 ]; then
        b=$(fffd "$base")
        if [ "$n" -gt "$b" ]; then
            report "$f [$LABEL]: U+FFFD replacement chars introduced on $((n - b)) more line(s) ($b -> $n, a lossy decode?)"
        fi
    fi
    if [ "$HAVE_PCRE" -eq 1 ]; then
        n=$(mojibake "$new")
        if [ "$n" -ge "$MOJIBAKE_MIN" ]; then
            b=0; [ -z "$base" ] || b=$(mojibake "$base")
            if [ $((n - b)) -ge "$MOJIBAKE_MIN" ]; then
                report "$f [$LABEL]: mojibake introduced ($b -> $n double-encoded sequences: UTF-8 read as cp1252, latin-1 or cp1251 and saved again)"
            fi
        fi
    fi
    if [ "$HAVE_ICONV" -eq 1 ] && [ -n "$base" ] && ! err=$(utf8_error "$new"); then
        if utf8_error "$base" >/dev/null; then
            report "$f [$LABEL]: no longer valid UTF-8 (${err:-invalid byte}), saved in another encoding?"
        fi
    fi
    if has_bom "$new"; then
        if [ -n "$base" ] && ! has_bom "$base"; then
            report "$f [$LABEL]: UTF-8 BOM added (PHP prints it before any header)"
        elif [ -z "$base" ]; then
            case "$path" in
                *.php) report "$f [$LABEL]: new PHP file with a UTF-8 BOM (printed before any header)" ;;
                *) note "$f [$LABEL]: new file with a UTF-8 BOM" ;;
            esac
        fi
    elif [ -n "$base" ] && has_bom "$base"; then
        note "$f [$LABEL]: UTF-8 BOM removed"
    fi
}

# $1 path, $2 source path (rename/copy) or "", $3 base sha (zeros: none),
# $4 new sha (zeros: read the working tree file).
check_file() {
    local f="$1" src="$2" ssha="$3" dsha="$4" name base="" new stat adds dels before notes_before eol_notes
    name="$f"
    [ -z "$src" ] || [ "$src" = "$f" ] || name="$f (from $src)"
    if ! is_zero "$ssha"; then
        base="$TMP/base"
        git cat-file blob "$ssha" > "$base" 2>/dev/null || return 0
    fi
    if is_zero "$dsha"; then
        [ -f "$f" ] && [ ! -L "$f" ] || return 0
        new="$f"
    else
        new="$TMP/new"
        git cat-file blob "$dsha" > "$new" 2>/dev/null || return 0
    fi
    # git's own text/binary call (NUL in the first 8000 bytes, attributes):
    # binary files show "-" counts and none of the line or text checks apply.
    stat="${NUMSTAT[$f]:-}"
    [ -n "$stat" ] || return 0
    read -r adds dels <<< "$stat"
    [ "$adds" != "-" ] && [ "$dels" != "-" ] || return 0
    before=$FOUND
    notes_before=$NOTES
    case "$f" in
        "$FEED_DIR"/feed.json|"$FEED_DIR"/arch.json) ;;
        *) check_eol "$f" "$name" "$base" "$new" ;;
    esac
    eol_notes=$((NOTES - notes_before))
    check_encoding "$f" "$name" "$base" "$new"
    # A line-ending note already explains a whitespace-only diff.
    if [ "$FOUND" -eq "$before" ] && [ "$eol_notes" -eq 0 ] && [ -n "$base" ]; then
        case "$f" in
            "$FEED_DIR"/feed.json|"$FEED_DIR"/arch.json) ;;
            *)
                # -b, not -w: -w also hides a space added or removed inside
                # a line ("Java Script" -> "JavaScript"), a real text change.
                if [ -z "${NONWS[$f]:-}" ]; then
                    note "$name [$LABEL]: whitespace-only change ($adds+/${dels}-: indentation, trailing spaces), not blocking"
                fi ;;
        esac
    fi
}

# numstat -z: "adds<TAB>dels<TAB>path" or, for a rename or copy,
# "adds<TAB>dels<TAB>" then the source and the destination as two fields.
declare -A NUMSTAT=() NONWS=()
read_numstat() {   # $1: NUMSTAT or NONWS, the array to fill; the rest: diff options
    local which="$1" rec adds dels path
    shift
    while IFS= read -r -d '' rec; do
        IFS=$'\t' read -r adds dels path <<< "$rec"
        if [ -z "$path" ]; then
            IFS= read -r -d '' _ || break      # rename/copy source
            IFS= read -r -d '' path || break
        fi
        if [ "$which" = NONWS ]; then
            NONWS["$path"]="$adds $dels"
        else
            NUMSTAT["$path"]="$adds $dels"
        fi
    done < <("${GIT_DIFF[@]}" --numstat "$@" "${RANGE[@]}")
}
read_numstat NUMSTAT
read_numstat NONWS -b
# -b keeps a whitespace-only change listed with 0 0: drop those.
for k in "${!NONWS[@]}"; do
    [ "${NONWS[$k]}" != "0 0" ] || unset 'NONWS[$k]'
done

while IFS= read -r -d '' meta; do
    IFS= read -r -d '' path || break
    src=""
    read -r smode dmode ssha dsha status <<< "${meta#:}"
    case "$status" in
        R*|C*) src="$path"; IFS= read -r -d '' path || break ;;
    esac
    case "$smode:$dmode" in
        120000:*|*:120000|160000:*|*:160000) continue ;;
    esac
    [ "$ssha" != "$dsha" ] || continue   # pure rename or mode change
    check_file "$path" "$src" "$ssha" "$dsha"
done < <("${GIT_DIFF[@]}" --raw --no-abbrev "${RANGE[@]}")

if [ "$HAVE_PCRE" -eq 0 ]; then
    echo "check-diff-artifacts: grep -P is not available, the mojibake check was skipped" >&2
fi
if [ "$HAVE_ICONV" -eq 0 ]; then
    echo "check-diff-artifacts: iconv is not available, the UTF-8 validity check was skipped" >&2
fi
if [ "$FOUND" -gt 0 ]; then
    echo "check-diff-artifacts: $FOUND artifact(s). Fix them, or audit and skip this hook only: container/tools/CHECK_DIFF_ARTIFACTS.md"
    exit 1
fi
if [ "$NOTES" -gt 0 ]; then
    echo "check-diff-artifacts: clean ($NOTES note(s), not blocking)"
else
    echo "check-diff-artifacts: clean"
fi
exit 0
