# What to improve in the translation tooling

Written during the English-tree job (phases 1-4). Each item says what, why,
and where in the code. Items marked **done** were implemented in this round.

## Done in this round

0e. **The English tree's remaining Cyrillic is catalogued.** After the
   Russian-to-English pass, 37 files still contain Cyrillic; each one is a
   deliberate subject case (a language/encoding example, a real URL, real
   output, a quoted UI label, test data, a Russian acronym or a proper name).
   `remaining-cyrillic.md` lists every such file with its reason, so a later
   round can tell a subject case from an untranslated page without re-reading
   the tree. No tool change was needed for this: the tail was finished with
   the normal extract → import → verify → apply loop, plus literal edits for
   commented labels the extractor cannot see.

0d. **`adapt_to_en` takes the RelatedArticles namespace from the file the
   page requires** (0.7.2; host review, round 12). It used to derive the folder from
   `$page_dir`, so a page living in one folder while `$page_dir` pointed at
   another got a `use` line for a class it never loads: `linux/rocky/network/iperf.php`
   was pointed at `Site\Linux\Centos\Network\RelatedArticles` and
   `linux/debian/network/iperf.php` at the Ubuntu namespace, and both renders
   died with `Class not found`. `required_related_file()` now reads the page's
   `require_once … RelatedArticles.php` (including `$page_dir . 'RelatedArticles.php'`),
   falls back to `$page_dir`, then to the sibling. The per-page repairs of
   `new_interface.php` and the two iperf pages became unnecessary. Test:
   `test_adapt_to_en_uses_the_required_file_not_page_dir` (the iperf case).

0. **`--engine import` keeps the chunk's padding, and apply keeps CRLF**
   (host review, items 6 and 7). `pad_target()` re-applies the source
   chunk's leading/trailing whitespace (and, for a code comment, the comment
   marker) that `import_targets()` used to drop: every imported sentence lost
   the space that separated it from the next one, so a paragraph came out as
   `page<a href=...>Literature</a>` and `...does not.Git does not...`.
   `write_output()` renders `output.php` with the source file's own line
   endings, so a CRLF page stays CRLF after `apply`. Tests:
   `test_import_keeps_leading_and_trailing_whitespace`,
   `test_apply_keeps_crlf`.

0b. **verify rejects a translation that glues a word to an inline tag**
   (host review, round 4). `check_tag_spacing()` counts, for every
   `a/b/i/em/strong/span/code/tt/sup/sub` tag, whether it is preceded (or
   followed) by a space, an opening punctuation mark or a closing one, and
   fails the chunk when the target keeps fewer of them than the source:
   `management<a>` instead of `management (<a>`, `</a>with` instead of
   `</a> with`, `(...)` around a link. Test:
   `test_verify_flags_space_and_punctuation_lost_next_to_an_inline_tag`
   (the companies.php case).

0c. **`adapt_to_en` no longer rewrites the `use ...\RelatedArticles;` line to
   a namespace that defines no class.** A section folder may keep a
   function-only `RelatedArticles.php` (as `dev/python/future/` does); the old
   code pointed the page at it and the render died with
   `Class ... not found`. The line is left alone and reported when the
   sibling has no `class RelatedArticles`. Test:
   `test_adapt_to_en_keeps_the_use_line_of_a_function_only_sibling`.

1. **ru-en adapts the page to the English site** (mirror of en-ru).
   `adapt_to_en()` in `ru_en_translate.py`, called from `cmd_verify`.
   Before this the pipeline only flipped `lang: 'ru'` to `'en'` when no
   Cyrillic was left, and left `use Site\Ru\Php\...`,
   `$root . '/ru/.php/...'`, `$page_dir = '/ru/...'`, the Russian
   `RelatedArticles` namespace, the Russian author and every `/ru/` link in
   place. Now it maps them: the folder map of `en-ru-folders.md` reversed,
   the page's own `RelatedArticles.php` namespace, `AUTHOR_PAIRS`, and
   `/ru/` links through `english_twin()`. A link or folder without an English
   twin is reported in `report.json` and left as it is.

2. **`#` comments inside code blocks are extracted.**
   `CODE_COMMENT_HASH` in `code_comment_chunks()`. Python, bash and YAML
   examples use `#`, and the old `//`/`/* */` patterns saw none of them: a
   page could not pass verify because its code comments stayed Russian.

3. **HTML comments no longer fail verify.**
   `cmd_verify` strips `<!-- ... -->` before the Cyrillic scan. Comments are
   invisible to the reader, and a Russian comment in the markup is not
   untranslated prose.

4. **`verify --allow REGEX`** keeps chosen lines with Cyrillic: real program
   output from a Russian locale (`Авиасейлз, Travel`), a test password in a
   quoted command, a URL that contains Russian
   (`Desktop_environment_(Русский)`). Repeatable; documented in the parser.

5. **The author is not a translation chunk.**
   `extract_segments()` now skips `AUTHOR_ARG` in ru-en too (en-ru already
   did), so a person's name is never machine-translated; `adapt_to_en` swaps
   it from the author table.

6. **`en_ru_pairs.py`** - the pairing command. Writes
   `en_ru_pairs.tsv` (`en_path`, `ru_path` or empty, `method`, `score`).
   Candidates come from the folder map plus the remaining path, the copies in
   `duplicate-pages.md`, cross-language links and language-independent
   content (code lines, image file names, external URLs), and every candidate
   gets a topic check on its title. One twin per page, greedy by score, the
   runners-up printed as `note:` lines.

18. **`en_ru_pairs.py` reads the pairing comments as the truth** (done, 0.2.0).
   After the English tree was translated, content and title no longer match
   the Russian pages: a rerun lost pairs such as
   `en/hw/disk_b/index.php <-> ru/hw/disk_b/index.php` and lowered about 80
   scores. Every page now carries `# ru translation: ...` / `# en translation:
   ...` (or `not found`), and `read_page()` reads that header line.
   `pair_pages()` takes the English side's pair as fixed (method `comment`,
   score 100) before `FORCED_PAIRS` and all matching; a conflict with the
   Russian side is reported and the English pair still wins. A `not found`
   page stays unpaired and a strong candidate is only a suggestion. Pages
   with no comment still use the old methods. `--write-comments` writes or
   replaces the line on both sides from the result (placement after `<?php`
   and any leading comment block, CRLF kept, `--dry-run` prints only, `ao/`,
   `zh/`, `feed/` and `.php/` engine folders skipped), and `--check` reports a
   missing or duplicated comment, an asymmetric pair, a missing or non-page
   target, and a `--tsv` table that disagrees, exiting 1.

7. **A `wspw` paragraph that holds prose is translated** (done, 0.8.0, both
   directions). `wspw_prose()` decides for a `<p class="... wspw">`: prose
   when `wspw` is its only code class (no `err`, `cout1`, `pout1`, `cmd1`,
   `path`, `fname`, `*code`), it has no tree characters (box drawing, `|--`,
   `+--`) and at least half of its lines have three words of the source
   language. For English the lines must also end a sentence or a clause and
   the block must hold no banner (`@@@@`) or log line, because program output
   is English too. Over the whole tree this adds chunks in 22 Russian and 11
   English pages (`rocky/dnf_yum.php`, `python/edu/tutors/`, the `copy`
   docstring) and takes none away. A block the heuristic gets wrong is kept
   with `verify --allow`. Tests:
   `test_wspw_paragraph_with_prose_is_extracted_and_a_tree_is_not`,
   `test_en_ru_wspw_paragraph_with_prose_is_extracted`.

8. **Cyrillic in an attribute value is not Russian text** (done, 0.8.0).
   `html_text_chunks()` tests the text of a chunk with its tags removed, and
   `verify` drops `class="..."` before the Cyrillic scan, so the highlighter
   classes of `qa/robot/tags/index.php` (`class="сd0d0d0"`) no longer produce
   chunks of English code or fail the page. Test:
   `test_cyrillic_in_a_class_name_is_not_russian_text`.

9. **A re-extract keeps the targets** (done, 0.8.0, both directions). Chunk
   ids are still positional, but `extract` in a workdir that already has
   targets for the same page and direction gives each new chunk the target of
   the old chunk with the same kind and source text (`carry_targets()`), so
   a page edited after extract costs only the chunks that changed.
   `--fresh` drops them. Tests:
   `test_carry_targets_matches_on_source_text_not_on_id`,
   `test_cli_reextract_keeps_targets_unless_fresh`.

13. **A broad `--allow` is reported** (done, 0.8.0). When one pattern keeps
   more than five lines (ru-en) or chunks (en-ru), verify adds a warning to
   the console and `report.json`. A pattern that is not a regular expression
   is an error message, not a traceback. Test:
   `test_verify_warns_about_a_broad_allow_and_ignores_class_names`.

14. **`translate --reflow`** (done, 0.8.0, both directions, import and the
   engines). A one-line target of a chunk that spans several lines is wrapped
   at the source's longest line with the indent of its second line; a tag is
   never broken and a target with its own line breaks is kept. Opt-in: in a
   `wspw` block the line breaks show on the page. Test:
   `test_reflow_wraps_like_the_source_and_keeps_tags_whole`.

17. **An interpolated double-quoted string is translated in parts** (done,
   0.8.0, ru-en). `quoted_spans(split_interpolated=True)` returns the literal
   runs between `$var`, `$var[...]`, `$var->name`, `{$...}` and `${...}`;
   the variables stay outside every chunk. A `$name` that a target brings
   into a double-quoted string is escaped. en-ru keeps skipping such strings:
   every English string in PHP code would become a chunk. Tests:
   `test_interpolated_string_is_split_into_literal_parts`,
   `test_translation_cannot_bring_in_a_live_variable`.

19. **en-ru `verify --allow REGEX`** (done, 0.8.0). A chunk without Cyrillic
   passes when a pattern matches its target (a keyword list, an error
   message, a code sample). en-ru verify also checks placeholders and
   entities per chunk now, as ru-en does. Tests:
   `test_en_ru_allow_keeps_a_chunk_that_is_rightly_not_russian`,
   `test_en_ru_check_flags_a_dropped_placeholder_and_a_damaged_entity`.

## Still to do

10. **`en_ru_pairs.py` could read the sitemap.**
    Pairing is file-based on purpose (a stale sitemap must not hide a page),
    but the sitemap is still the authority for which URL a folder page
    answers at (`/x/` vs `/x.php`). Use it only to disambiguate, never to
    exclude.

11. **Pairs of duplicated pages.**
    "One twin per page" means `/locust/index.php` and `/qa/locust/index.php`
    both point at one Russian page, so one of them is reported as
    "not found" even though it is a copy of a paired page. A `--copies` mode
    that lets copies share a twin (and says so in the `method` column) would
    make the comment on the copy honest.

12. **`translate_batch.py` still offers only `argos` and `ollama`.**
    In this environment neither is installed, so every page went through
    `--engine import` with hand-written targets. `import` should be a
    first-class choice there too (it already is in `ru_en_translate.py`).

15. **A repair mode for pages translated before `pad_target`.**
    `check_tag_spacing` now *detects* the glued-tag cases, but repairing an
    already-translated page still needs a script: a space before an inline
    tag whose inner text starts a word, a space after one whose inner text
    ends a word (a single-letter fragment such as `<b>M</b>icro needs the
    first but not the second), punctuation the translation dropped outside a
    link. That logic lives in a throwaway script; it belongs next to `verify`
    as `--fix-spacing`, with the same masking of code, preformatted and
    highlighter blocks.

16. **Bare HTML in an include is extracted** (done, round 7).
    `extract_segments()` walks heredocs and quoted strings; a plain HTML
    fragment with no PHP around it (`windows/bash_win10_inc.php`, the `*_inc.php`
    menus) yielded only its `alt="…"` values. It now also runs
    `html_text_chunks()` over every region outside a `<?php … ?>` block and
    drops any chunk that overlaps one from the string walker (an attribute is
    found twice otherwise, and overlapping chunks break `render_output`).
    Test: `test_bare_html_include_is_extracted`.
