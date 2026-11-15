# version 0.4.1
"""Tests for the PHP site search (src/.php/SiteSearch.php).

The tokenizer test compares SiteSearch::tokens() with the Python
site_index.tokens() on the cases a PHP port usually gets wrong: C++, C#,
.NET, digits next to letters, the plural strip, ss words, Cyrillic and
punctuation. The search tests build a small export under tmp_path and call
the class through a tiny PHP CLI harness, so ranking is checked without a
web server. The handle tests run one request of the search page with a
Captcha that has a fixed key. All skip when php is not installed.
"""

import importlib
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

CONTAINER = Path(__file__).resolve().parents[2]
SITE_SEARCH = CONTAINER / "src" / ".php" / "SiteSearch.php"
if not SITE_SEARCH.is_file():
    pytest.skip(f"no search class at {SITE_SEARCH}: this module checks a site search", allow_module_level=True)
CAPTCHA = CONTAINER / "src" / ".php" / "Captcha.php"
SECRET = "k" * 40
sys.path.insert(0, str(CONTAINER / "tools" / "search"))

PHP = shutil.which("php")
pytestmark = pytest.mark.skipif(PHP is None, reason="php is not installed")
PhpRun = Callable[[dict[str, object]], str]

HARNESS = """<?php
require %s;
require %s;
$input = json_decode(stream_get_contents(STDIN), true);
if ($input['mode'] === 'stopwords') {
    echo json_encode((new ReflectionClass(\\SiteSearch::class))->getConstant('STOPWORDS'), JSON_UNESCAPED_UNICODE);
    exit(0);
}
if ($input['mode'] === 'tokens') {
    echo json_encode(array_map([\\SiteSearch::class, 'tokens'], $input['texts']), JSON_UNESCAPED_UNICODE);
    exit(0);
}
$search = new \\SiteSearch($input['docroot'], $input['lang'] ?? 'en');
if ($input['mode'] === 'search') {
    echo json_encode($search->search($input['query']), JSON_UNESCAPED_UNICODE);
    exit(0);
}
if ($input['mode'] === 'handle') {
    $captcha = new \\Captcha($input['docroot'], 'search', 'en', 'sum', $input['secret']);
    $get = $input['get'];
    $cookies = [];
    if (!empty($input['solve'])) {
        // Ask for a question, read its sum from the form and answer it.
        $html = $search->handle($get, [], '/search/', $captcha)['html'];
        preg_match('/(\\d) (.) (\\d) =/u', $html, $sum);
        preg_match('/name="captcha_token" value="([^"]+)"/', $html, $token);
        $get['captcha_token'] = $token[1];
        $get['captcha'] = (string) ($sum[2] === '+' ? $sum[1] + $sum[3] : $sum[1] - $sum[3]);
        if ($input['solve'] === 'wrong') {
            $get['captcha'] = (string) ((int) $get['captcha'] + 1);
        }
        if ($input['solve'] === 'trap') {
            $get['captcha_site'] = 'http://spam.example';
        }
    }
    if (!empty($input['pass'])) {
        $cookies[$captcha->cookieName()] = $captcha->pass();
    }
    echo json_encode($search->handle($get, $cookies, '/search/', $captcha), JSON_UNESCAPED_UNICODE);
    exit(0);
}
echo $search->render($input['query']);
"""

TEXTS = [
    "C++",
    "C#",
    ".NET",
    "OpenCode 2.0",
    "Docker24",
    "24docker",
    "buses",
    "classes",
    "glass",
    "Python's",
    "Привет, мир!",
    "k8s",
    "K8s-cluster, k8s_install",
    "mhash_keygen_s2k",
    "k8sx",
    "node.js",
    "HTTPS",
]


@pytest.fixture(name="si")
def fixture_si() -> ModuleType:
    """tools/search/site_index.py, imported when a test runs."""
    return importlib.import_module("site_index")


@pytest.fixture(name="php_run")
def fixture_php_run(tmp_path: Path) -> PhpRun:
    """Run the PHP harness with a JSON payload on stdin and return stdout."""
    script = tmp_path / "site_search_harness.php"
    script.write_text(HARNESS % (json.dumps(str(SITE_SEARCH)), json.dumps(str(CAPTCHA))), encoding="utf-8")

    def run(payload: dict[str, object]) -> str:
        assert PHP is not None
        result = subprocess.run(
            [PHP, str(script)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    return run


def token_field(tokens: list[str]) -> str:
    """The export's sorted, unique, space-separated token string."""
    return " ".join(sorted(set(tokens)))


def head(title: str) -> str:
    """Title before ':' or ' - ', enough for the test titles."""
    return title.split(":", 1)[0].split(" - ", 1)[0].strip()


def page(
    si: ModuleType,
    url: str,
    title: str,
    description: str = "",
    folders: list[str] | None = None,
    keywords: str = "",
    headings: tuple[str, str, str] = ("", "", ""),
) -> dict[str, object]:
    """One export record with its token fields computed like site_index.py; headings is the h1, h2 and h3 text."""
    if folders is None:
        folders = [part for part in url.strip("/").split("/") if part]
    levels = {f"h{level}_tokens": token_field(si.tokens(text)) for level, text in enumerate(headings, 1) if text}
    return {
        **levels,
        "url": url,
        "title": title,
        "description": description,
        "folders": [token_field(si.tokens(folder)) for folder in folders],
        "title_tokens": token_field(si.tokens(title)),
        "head_tokens": token_field(si.tokens(head(title))),
        "keyword_tokens": token_field(si.tokens(keywords)),
        "description_tokens": token_field(si.tokens(description)),
    }


def write_index(
    tmp_path: Path,
    pages: list[dict[str, object]],
    lang: str = "en",
    aliases: list[list[str]] | None = None,
) -> None:
    """Write a small export where SiteSearch looks for it."""
    search_dir = tmp_path / "search"
    search_dir.mkdir()
    data = {"version": 3, "lang": lang, "aliases": aliases or [], "pages": pages}
    (search_dir / f"site_index.{lang}.json").write_text(json.dumps(data), encoding="utf-8")


def run_search(php_run: PhpRun, tmp_path: Path, query: str, lang: str = "en") -> dict[str, Any]:
    """Call SiteSearch::search() on the temp docroot."""
    return json.loads(php_run({"mode": "search", "docroot": str(tmp_path), "query": query, "lang": lang}))


def run_handle(php_run: PhpRun, tmp_path: Path, get: dict[str, object], **options: object) -> dict[str, Any]:
    """One request of the search page: SiteSearch::handle() with a fixed captcha key."""
    payload = {"mode": "handle", "docroot": str(tmp_path), "secret": SECRET, "get": get, **options}
    return json.loads(php_run(payload))


def urls(found: dict[str, Any]) -> list[str]:
    """URLs in ranked order."""
    return [result["url"] for result in found["results"]]


def test_tokens_match_python(si: ModuleType, php_run: PhpRun) -> None:
    """The PHP tokenizer gives exactly the Python output on every case."""
    got = json.loads(php_run({"mode": "tokens", "texts": TEXTS}))
    assert got == [si.tokens(text) for text in TEXTS]


def test_exact_token_beats_prefix(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """An exact token ranks above a prefix, and a shorter URL wins a tie."""
    write_index(
        tmp_path,
        [
            page(si, "/docker/", "Docker page"),
            page(si, "/dock/", "Dock page"),
            page(si, "/guide/docker/", "Guide"),
        ],
    )
    assert urls(run_search(php_run, tmp_path, "dock")) == [
        "/dock/",
        "/docker/",
        "/guide/docker/",
    ]


def test_field_weights_and_folder_tiebreak(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """Last folder > head > description; equal scores prefer fewer folders."""
    write_index(
        tmp_path,
        [
            page(si, "/one/", "Alpha guide"),
            page(si, "/two/", "Two", description="alpha"),
            page(si, "/three/alpha/", "Three"),
            page(si, "/alpha/", "Something"),
        ],
    )
    assert urls(run_search(php_run, tmp_path, "alpha")) == [
        "/alpha/",
        "/three/alpha/",
        "/one/",
        "/two/",
    ]


def test_whole_query_equal_to_head_gets_bonus(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """A page whose head is the whole query beats a shallower URL."""
    write_index(
        tmp_path,
        [
            page(si, "/docker/", "Other"),
            page(si, "/a/b/docker/", "Docker"),
        ],
    )
    assert urls(run_search(php_run, tmp_path, "docker")) == ["/a/b/docker/", "/docker/"]


def test_last_folder_all_matched_gets_about_bonus(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """A page whose last folder is fully matched beats a page with extra words."""
    write_index(
        tmp_path,
        [
            page(
                si,
                "/business/docker_kafka_test_pipeline.php",
                "Docker/Kafka test pipeline with data-driven execution",
            ),
            page(si, "/business/load_testing_docker_scaling.php", "Load testing scaled with Docker containers"),
            page(si, "/dvps/docker/", "Docker"),
            page(si, "/ai/opencode/docker/", "OpenCode in Docker on Ubuntu 24.04: a safe setup step by step"),
        ],
    )
    assert urls(run_search(php_run, tmp_path, "dock"))[0] == "/dvps/docker/"

    docker = urls(run_search(php_run, tmp_path, "docker"))
    assert docker[0] == "/dvps/docker/"
    assert docker.index("/ai/opencode/docker/") < docker.index("/business/docker_kafka_test_pipeline.php")
    assert docker.index("/ai/opencode/docker/") < docker.index("/business/load_testing_docker_scaling.php")


def test_all_tokens_first_then_most_tokens(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """Pages with every word win; otherwise only the best partial matches show."""
    write_index(
        tmp_path,
        [
            page(si, "/git/", "Git"),
            page(si, "/hub/", "Hub"),
            page(si, "/git/hub/", "Git Hub"),
        ],
    )
    full = run_search(php_run, tmp_path, "git hub")
    assert full["all"] is True
    assert urls(full) == ["/git/hub/"]

    partial = run_search(php_run, tmp_path, "git missing")
    assert partial["all"] is False
    assert urls(partial) == ["/git/", "/git/hub/"]
    assert all(result["matched"] == 1 for result in partial["results"])


def test_render_escapes_and_keeps_empty_query_empty(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """Result text is escaped and an empty query renders no block."""
    write_index(tmp_path, [page(si, "/bad/", "Bad <script> & more", description='A "quote"')])
    assert php_run({"mode": "render", "docroot": str(tmp_path), "query": ""}) == ""
    html = php_run({"mode": "render", "docroot": str(tmp_path), "query": "bad"})
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&quot;quote&quot;" in html


def test_render_singular_and_plural_summary(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """One match says '1 page found', two matches say '2 pages found'."""
    write_index(tmp_path, [page(si, "/only/", "Only result"), page(si, "/other/", "Other result")])
    one = php_run({"mode": "render", "docroot": str(tmp_path), "query": "only"})
    assert "1 page found for “only”" in one
    assert "1 pages" not in one
    two = php_run({"mode": "render", "docroot": str(tmp_path), "query": "result"})
    assert "2 pages found for “result”" in two


def test_missing_index_is_reported(php_run: PhpRun, tmp_path: Path) -> None:
    """A missing export shows the unavailable line instead of a fatal error."""
    html = php_run({"mode": "render", "docroot": str(tmp_path), "query": "docker"})
    assert "Search is not available right now." in html


def test_stopwords_are_optional(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """ "how to install docker" needs install and docker only; a query of stopwords still searches."""
    write_index(
        tmp_path,
        [
            page(si, "/docker/install/", "Install Docker"),
            page(si, "/docker/", "Docker"),
            page(si, "/misc/the.php", "The"),
        ],
    )
    found = run_search(php_run, tmp_path, "how to install docker")
    assert found["tokens"] == ["install", "docker"]
    assert found["all"] is True
    assert urls(found) == ["/docker/install/"]
    assert urls(run_search(php_run, tmp_path, "the")) == ["/misc/the.php"]


def test_query_words_are_capped_and_deduplicated(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """A hundred words cost no more than eight, and a repeated word counts once."""
    write_index(tmp_path, [page(si, "/docker/", "Docker")])
    many = run_search(php_run, tmp_path, " ".join(f"w{number}x" for number in range(100)))
    assert len(many["tokens"]) == 8
    repeated = run_search(php_run, tmp_path, "docker " * 20)
    assert repeated["tokens"] == ["docker"]
    assert urls(repeated) == ["/docker/"]


def test_typo_is_corrected(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """A word that is in no page is replaced by the closest title word."""
    write_index(tmp_path, [page(si, "/dev/python/", "Python"), page(si, "/dvps/docker/", "Docker")])
    found = run_search(php_run, tmp_path, "pyhton")
    assert found["corrected"] == "python"
    assert urls(found) == ["/dev/python/"]
    html = php_run({"mode": "render", "docroot": str(tmp_path), "query": "pyhton"})
    assert "Nothing found for “pyhton”. Showing the results for “python”." in html
    far = run_search(php_run, tmp_path, "zzzzzz")
    assert far["corrected"] is None
    assert far["total"] == 0


def test_russian_word_ending(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """A Russian word finds its other forms: "ветка" matches a page about "ветки"."""
    write_index(tmp_path, [page(si, "/ru/i/git/branch/", "Ветки в Git")], lang="ru")
    found = run_search(php_run, tmp_path, "ветка", lang="ru")
    assert urls(found) == ["/ru/i/git/branch/"]
    assert found["corrected"] is None


def test_only_site_paths_are_linked(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """A damaged index cannot link outside the site or run a script."""
    write_index(
        tmp_path,
        [
            page(si, "javascript:alert(1)", "Docker one", folders=["docker"]),
            page(si, "//evil.example/docker/", "Docker two", folders=["docker"]),
            page(si, "/\\evil.example/docker/", "Docker three", folders=["docker"]),
            page(si, "https://evil.example/docker/", "Docker four", folders=["docker"]),
            page(si, "/docker/", "Docker"),
        ],
    )
    assert urls(run_search(php_run, tmp_path, "docker")) == ["/docker/"]


def test_clean_query(php_run: PhpRun, tmp_path: Path) -> None:
    """Markup is escaped, invisible characters and broken input are dropped, the length is capped."""
    script = "<script>alert(1)</script>\"'><img src=x onerror=alert(1)>"
    form = run_handle(php_run, tmp_path, {"q": script}, **{"pass": True})["html"]
    assert "<script" not in form
    assert "<img" not in form
    assert "&lt;script&gt;alert(1)&lt;/script&gt;&quot;&#039;&gt;" in form
    hidden = run_handle(php_run, tmp_path, {"q": "do\u202ecker\u200b\x00\x07  x"}, **{"pass": True})["html"]
    assert 'value="do cker x"' in hidden
    array = run_handle(php_run, tmp_path, {"q": ["a", "b"]}, **{"pass": True})
    assert 'value=""' in array["html"]
    long = run_handle(php_run, tmp_path, {"q": "я" * 5000}, **{"pass": True})["html"]
    assert 'value="' + "я" * 200 + '"' in long


def test_first_search_asks_for_captcha(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """Without a pass the form holds a question and no results, and is not indexed or cached."""
    write_index(tmp_path, [page(si, "/docker/", "Docker")])
    asked = run_handle(php_run, tmp_path, {"q": "docker"})
    assert 'class="captcha"' in asked["html"]
    assert 'value="docker"' in asked["html"]
    assert 'maxlength="200"' in asked["html"]
    assert "sitesearch__results" not in asked["html"]
    assert "X-Robots-Tag: noindex, follow" in asked["headers"]
    assert "Cache-Control: private, no-store" in asked["headers"]
    assert "X-Content-Type-Options: nosniff" in asked["headers"]
    assert asked["pass"] is False


def test_solved_captcha_gives_pass_and_redirect(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """The right answer gives the pass and the clean result address; a pass shows results."""
    write_index(tmp_path, [page(si, "/docker/", "Docker")])
    solved = run_handle(php_run, tmp_path, {"q": "docker compose"}, solve="right")
    assert solved["pass"] is True
    assert solved["redirect"] == "/search/?q=docker%20compose"
    passed = run_handle(php_run, tmp_path, {"q": "docker"}, **{"pass": True})
    assert 'class="captcha"' not in passed["html"]
    assert 'href="/docker/"' in passed["html"]


def test_wrong_answer_and_trap_field_are_refused(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """A wrong answer or a filled trap field gives a new question and no pass."""
    write_index(tmp_path, [page(si, "/docker/", "Docker")])
    for solve in ("wrong", "trap"):
        refused = run_handle(php_run, tmp_path, {"q": "docker"}, solve=solve)
        assert refused["pass"] is False
        assert refused["redirect"] is None
        assert "captcha__error" in refused["html"]
        assert "sitesearch__results" not in refused["html"]


def test_aliases_find_the_other_names(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """A name of a synonym group finds the pages of every name, also inside a longer query."""
    write_index(
        tmp_path,
        [
            page(si, "/dvps/k8s/", "K8s cluster"),
            page(si, "/dvps/kubernetes/install/", "Install Kubernetes"),
            page(si, "/dev/perl/regex/", "Regex in Perl"),
            page(si, "/dev/ruby/re/", "Regular expressions in Ruby"),
            page(si, "/dev/ruby/", "Ruby"),
            page(si, "/misc/regular.php", "Regular customers"),
        ],
        aliases=[["kubernete", "kube"], ["regular expression", "regex", "regexp"]],
    )
    both = ["/dvps/k8s/", "/dvps/kubernetes/install/"]
    assert sorted(urls(run_search(php_run, tmp_path, "k8s"))) == both
    assert sorted(urls(run_search(php_run, tmp_path, "Kubernetes"))) == both
    assert urls(run_search(php_run, tmp_path, "install k8s")) == ["/dvps/kubernetes/install/"]

    assert sorted(urls(run_search(php_run, tmp_path, "regex"))) == ["/dev/perl/regex/", "/dev/ruby/re/"]
    phrase = run_search(php_run, tmp_path, "regular expressions")
    assert sorted(urls(phrase)) == ["/dev/perl/regex/", "/dev/ruby/re/"]
    assert phrase["all"] is True
    ruby = run_search(php_run, tmp_path, "ruby regexp")
    assert urls(ruby) == ["/dev/ruby/re/"]
    assert ruby["all"] is True


def test_alias_name_is_not_corrected_and_bad_groups_are_ignored(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """A synonym name without pages is not replaced by a close word; broken groups load nothing."""
    write_index(
        tmp_path,
        [page(si, "/dev/regen/", "Regen"), page(si, "/docker/", "Docker")],
        aliases=[["regex", "regexp"], ["alone"], "text", [["nested"], 5], ["<b>", "docker"]],  # type: ignore[list-item]
    )
    found = run_search(php_run, tmp_path, "regex")
    assert found["corrected"] is None
    assert found["total"] == 0
    assert urls(run_search(php_run, tmp_path, "docker")) == ["/docker/"]


def test_russian_alias_finds_latin_pages(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """A Russian name, also in another case form, finds the pages of the Latin name."""
    write_index(
        tmp_path,
        [
            page(si, "/ru/dvps/docker/", "Docker"),
            page(si, "/ru/dvps/docker/install/", "Установка Docker"),
            page(si, "/ru/i/git/", "Git"),
        ],
        lang="ru",
        aliases=[["docker", "докер"], ["git", "гит"]],
    )
    both = ["/ru/dvps/docker/", "/ru/dvps/docker/install/"]
    assert sorted(urls(run_search(php_run, tmp_path, "докер", lang="ru"))) == both
    assert sorted(urls(run_search(php_run, tmp_path, "докера", lang="ru"))) == both
    installed = run_search(php_run, tmp_path, "установка докера", lang="ru")
    assert urls(installed) == ["/ru/dvps/docker/install/"]
    assert installed["all"] is True
    assert installed["corrected"] is None


def test_heading_words_find_the_page(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """A word of an h1, h2 or h3 finds its page, below a page that has it in the title."""
    write_index(
        tmp_path,
        [
            page(si, "/rfid/tags.php", "RFID tags", headings=("Smartrac MiniWeb", "", "")),
            page(si, "/ai/agents/", "AI agents", headings=("", "Agent vs chatbot", "Canary tasks")),
            page(si, "/ai/chat/", "Chatbot basics"),
            page(si, "/ai/audit/", "Audit", headings=("", "", "Chatbot notes")),
        ],
    )
    assert urls(run_search(php_run, tmp_path, "miniweb")) == ["/rfid/tags.php"]
    assert urls(run_search(php_run, tmp_path, "chatbot")) == ["/ai/chat/", "/ai/agents/", "/ai/audit/"]
    canary = run_search(php_run, tmp_path, "canary")
    assert urls(canary) == ["/ai/agents/"]
    assert canary["corrected"] is None


def test_typo_is_corrected_to_a_heading_word(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """The words of the headings are words a typo is corrected to."""
    write_index(tmp_path, [page(si, "/ai/agents/", "AI agents", headings=("", "Agent vs chatbot", ""))])
    found = run_search(php_run, tmp_path, "chatbto")
    assert found["corrected"] == "chatbot"
    assert urls(found) == ["/ai/agents/"]


def test_word_itself_beats_longer_word_in_a_stronger_field(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """ "serve" lists the page with the word, in any field, above /server/, whose name only starts with it."""
    write_index(
        tmp_path,
        [
            page(si, "/git/gitlab/server/", "GitLab server"),
            page(si, "/ai/ollama/basics/", "Ollama basics", description="Run ollama serve and pull a model"),
            page(si, "/ai/ollama/", "Ollama", headings=("", "Serve on another port", "")),
        ],
    )
    found = run_search(php_run, tmp_path, "serve")
    assert urls(found) == ["/ai/ollama/", "/ai/ollama/basics/", "/git/gitlab/server/"]
    assert [result["exact"] for result in found["results"]] == [1, 1, 0]


def test_other_number_form_is_the_same_word(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """ "boundaries" and "boundary", "passes" and "pass", "boxes" and "box" find each other as exact words."""
    write_index(
        tmp_path,
        [
            page(si, "/a/", "Boundary of a set"),
            page(si, "/b/", "Checking boundaries"),
            page(si, "/c/", "Two passes"),
            page(si, "/d/", "One pass"),
            page(si, "/e/", "Boxes"),
            page(si, "/f/", "Box"),
        ],
    )
    for query, expected in (
        ("boundaries", ["/a/", "/b/"]),
        ("boundary", ["/a/", "/b/"]),
        ("passes", ["/c/", "/d/"]),
        ("pass", ["/c/", "/d/"]),
        ("boxes", ["/e/", "/f/"]),
        ("box", ["/e/", "/f/"]),
    ):
        found = run_search(php_run, tmp_path, query)
        assert sorted(urls(found)) == expected, query
        assert all(result["exact"] == 1 for result in found["results"]), query


def test_alias_of_several_words_needs_one_field(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """ "docker compose" is not a page with "docker" in its URL and "compose" in its description."""
    write_index(
        tmp_path,
        [
            page(si, "/dvps/compose/", "Docker Compose"),
            page(si, "/dvps/docker/music/", "Players", description="How to compose a playlist"),
            page(si, "/dvps/dc/", "The dc tool"),
        ],
        aliases=[["docker compose", "dc"]],
    )
    assert sorted(urls(run_search(php_run, tmp_path, "dc"))) == ["/dvps/compose/", "/dvps/dc/"]


def test_k8s_is_one_word(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """ "k8s" and "kubernetes" find each other without a synonym group, and not a page with k, 8 and s."""
    write_index(
        tmp_path,
        [
            page(si, "/dvps/kubernetes/", "Kubernetes"),
            page(si, "/section/functions/mhash_keygen_s2k/", "mhash_keygen_s2k", keywords="s2k, 2.5.8"),
            page(si, "/dvps/k8s/", "K8s cluster"),
        ],
    )
    both = ["/dvps/k8s/", "/dvps/kubernetes/"]
    assert sorted(urls(run_search(php_run, tmp_path, "kubernetes"))) == both
    assert sorted(urls(run_search(php_run, tmp_path, "k8s"))) == both


def test_heading_function_words_are_optional_query_words(si: ModuleType, php_run: PhpRun, tmp_path: Path) -> None:
    """The export drops these words from the heading fields, so the search must not require them."""
    stopwords = set(json.loads(php_run({"mode": "stopwords"})))
    dropped = {token for word in si.HEADING_FUNCTION_WORDS for token in si.tokens(word)}
    assert dropped <= stopwords, sorted(dropped - stopwords)
    write_index(
        tmp_path,
        [
            page(si, "/notes/release/", "Release notes", headings=("", "Changed", "")),
            page(si, "/misc/story/", "A story", description="It was a long day"),
        ],
    )
    found = run_search(php_run, tmp_path, "what was changed")
    assert urls(found) == ["/notes/release/"]
    assert found["all"] is True
