#!/usr/bin/env python3
# version 0.1.0
"""Onboard a project that new-project.sh made: README.md, sections 4 to 10.

Three ways through the same steps:

  python3 container/tools/onboard.py              all of them in one run, asking when it
                                                  needs you (container/tools/auto_onboard.sh)
  python3 container/tools/onboard.py step [ARG]   the step the project is at, then the next
                                                  one shown (container/tools/onboard.sh)
  python3 container/tools/onboard.py next         only where the project is
                                                  (container/tools/new-project.sh --next)
  python3 container/tools/onboard.py next --id    the step as one word, for scripts

The steps, found from what the project holds, not from a record of earlier
runs, so a step done by hand counts too:

  docker    Docker is not running (it is not started for you: that needs sudo)
  start     the site container is not running: docker compose up
  copy      container/src is still the stub: rsync the old site in (ARG: its
            folder), then fix the file modes the copy kept
  discover  the profile is as new-project.sh wrote it: discover_site.py --url
            suggests lines; the walk writes them on yes
  hosting   SITE_HOSTING is not set: the hosting the site is uploaded to (ARG:
            its name), then htaccess_hosting.py checks .htaccess against it
  gate      no commit yet: first README section 8 (modes, old sitemaps, the
            fixers one by one), then new-project.sh --check, then the first
            commit of README section 9
  sitemap   no container/src/sitemap.xml: generate-sitemap.sh with SITE_URL
  tests     the page tests

Every command is printed the way it can be typed, then run in the project
root. --root works on another project than the one this file lives in.
"""

from __future__ import annotations

import argparse
import os
import shlex
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
OWN_ROOT = HERE.parents[1]

STUB_TEXT = "Drop the pages of the new site into container/src"
EXAMPLE_URL = "https://example.com"
DISCOVER_REPORT = "container/reports/discover-site.txt"
SCRIPT_SUFFIXES = (".sh", ".cgi", ".pl", ".py")
# The first commit, as README section 9 gives it.
COMMIT_PATHS = (".gitignore", ".pre-commit-config.yaml", ".python-version", "AGENTS.md", "CLAUDE.md", "opencode.jsonc", ".claude", ".opencode", "container")
HOSTINGS = (
    ("beget", "Beget (nginx in front, Apache with mod_php)"),
    ("dreamhost", "DreamHost (Apache with TLS, PHP as FastCGI)"),
    ("apache-proxy", "another Apache behind a TLS proxy"),
    ("apache-tls", "another Apache with TLS itself"),
    ("litespeed", "LiteSpeed"),
    ("nginx", "nginx only (reads no .htaccess)"),
    ("unknown", "not decided yet"),
)
MODE_FIX = (
    ["find", "container/src", "-type", "f", "-exec", "chmod", "644", "{}", "+"],
    ["find", "container/src", "-type", "d", "-exec", "chmod", "755", "{}", "+"],
    ["find", "container/src", "-type", "f", "(", "-name", "*.sh", "-o", "-name", "*.cgi", "-o", "-name", "*.pl", "-o", "-name", "*.py", ")", "-exec", "chmod", "755", "{}", "+"],
)


@dataclass(frozen=True)
class Fixer:
    """One fixer of README section 8."""

    label: str
    command: list[str]
    changes_files: bool  # True: shown with git diff --stat, then accepted or undone


FIXERS = (
    Fixer("line endings (CRLF to LF)", ["container/tools/linters/check-line-endings.sh", "--fix", "--all", "--src", "src"], True),
    Fixer("lines that hold only spaces", ["container/tools/linters/check-php-blank-lines.sh", "--fix", "--all", "--src", "src"], True),
    Fixer("the PHP opening line", ["container/tools/linters/check-php-header.sh", "--fix", "--all", "--src", "src"], True),
    Fixer("PHP style (phpcs fixes what it can; Docker, the slowest)", ["container/tools/linters/lint-static.sh", "--tool", "phpcs", "--fix", "--all"], True),
    Fixer("PHP syntax (php -l lists each broken file; fixed by hand)", ["container/tools/linters/lint-php.sh", "--all"], False),
    Fixer("broken URLs (listed, fixed by hand)", ["container/tools/linters/find-malformed-urls.sh", "--all", "--src", "src"], False),
)


class Stop(Exception):  # noqa: N818  # not an error: the walk ends here
    """The walk ends here; run it again to go on."""

    def __init__(self, message: str, code: int = 0) -> None:
        super().__init__(message)
        self.code = code


# --- the project -------------------------------------------------------------


def main_checkout(root: Path) -> Path:
    """A linked worktree belongs to its main checkout, as in new-project.sh."""
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True, check=False)
    common = Path(result.stdout.strip()) if result.returncode == 0 else None
    return common.parent if common is not None and common.name == ".git" else root


def read_env(path: Path) -> dict[str, str]:
    """KEY=VALUE lines, quotes stripped, the way the tools read the profile."""
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass
class Project:
    """A project root and what is read from it."""

    root: Path

    @property
    def src(self) -> Path:
        return self.root / "container" / "src"

    @property
    def profile_path(self) -> Path:
        return self.root / "container" / "site-profile.env"

    def profile(self) -> dict[str, str]:
        return read_env(self.profile_path)

    def slug(self) -> str:
        return read_env(self.root / "container" / ".env").get("SITE_PROJECT", "")

    def set_profile_line(self, line: str) -> None:
        """KEY=VALUE replaces the line of KEY, or is appended; written by rename."""
        key = line.split("=", 1)[0]
        old = self.profile_path.read_text(encoding="utf-8").splitlines() if self.profile_path.is_file() else []
        new, done = [], False
        for current in old:
            if current.startswith(f"{key}="):
                if not done:
                    new.append(line)
                done = True
            else:
                new.append(current)
        if not done:
            new.append(line)
        tmp = self.profile_path.with_name(self.profile_path.name + ".tmp")
        tmp.write_text("\n".join(new) + "\n", encoding="utf-8")
        if self.profile_path.exists():
            os.chmod(tmp, stat.S_IMODE(self.profile_path.stat().st_mode))
        tmp.replace(self.profile_path)

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, check=False)


def docker_runs() -> bool:
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


def site_runs(slug: str) -> bool:
    result = subprocess.run(["docker", "ps", "-q", "--filter", f"name=^{slug}-apache$"], capture_output=True, text=True, check=False)
    return bool(result.stdout.strip())


def src_is_stub(project: Project) -> bool:
    entries = sorted(entry.name for entry in project.src.iterdir() if entry.name != ".php") if project.src.is_dir() else []
    index = project.src / "index.php"
    return entries == ["index.php"] and STUB_TEXT in index.read_text(encoding="utf-8", errors="replace")


def profile_untouched(project: Project) -> bool:
    """The profile as new-project.sh wrote it: the example plus the name and port."""

    def lines(path: Path, skip: tuple[str, ...]) -> list[str]:
        if not path.is_file():
            return []
        kept = (line for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#"))
        return sorted(line for line in kept if not line.startswith(skip))

    if not project.profile_path.is_file():
        return True
    own = ("SITE_LOCAL_URL=", "SITE_HOSTING=")
    return lines(project.profile_path, (*own, "SITE_NAME=")) == lines(project.root / "container" / "site-profile.env.example", own)


def executable_files(project: Project) -> list[Path]:
    """Files a copy made executable that are no scripts."""
    found = []
    for path in project.src.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.stat().st_mode & stat.S_IXUSR and path.suffix not in SCRIPT_SUFFIXES:
            found.append(path)
    return found


def old_sitemaps(project: Project) -> list[Path]:
    return sorted(project.src.glob("sitemap*.xml"))


def staged_src(project: Project) -> bool:
    return project.git("diff", "--cached", "--quiet", "--", "container/src").returncode != 0


@dataclass
class State:
    """The step a project is at, and what is already done."""

    step: str
    done: list[str] = field(default_factory=list)


def current(project: Project) -> State:
    """The first step whose check fails; every check before it is done."""
    profile = project.profile()
    hosting = profile.get("SITE_HOSTING", "")
    checks = (
        ("docker", docker_runs, ""),
        ("start", lambda: site_runs(project.slug()), f"the site runs at {profile.get('SITE_LOCAL_URL', '')}/"),
        ("copy", lambda: not src_is_stub(project), "the old site is in container/src"),
        ("discover", lambda: not profile_untouched(project), "the profile is filled"),
        ("hosting", lambda: bool(hosting), f"the hosting is {hosting}"),
        ("gate", lambda: project.git("rev-parse", "-q", "--verify", "HEAD").returncode == 0, "the first commit is made"),
        ("sitemap", (project.src / "sitemap.xml").is_file, "the sitemap exists"),
    )
    state = State("tests")
    for step, passed, done in checks:
        if not passed():
            state.step = step
            break
        if done:
            state.done.append(done)
    return state


# --- what each step is, for next -------------------------------------------


@dataclass(frozen=True)
class StepInfo:
    number: str
    what: str
    section: str
    by_hand: tuple[tuple[bool, str], ...]  # (is a command, text)
    argument: str = ""  # what onboard.sh takes for this step


def step_info(step: str, profile: dict[str, str]) -> StepInfo:
    up = (True, "(cd container && docker compose up -d --build php-apache)")
    hostings = ", ".join(name for name, _ in HOSTINGS)
    infos = {
        "docker": StepInfo("2", "start Docker, then the empty site", "4", ((False, "Docker is not running: start it (sudo systemctl start docker), then:"), up)),
        "start": StepInfo("2", "start the site", "4", (up,)),
        "copy": StepInfo(
            "3",
            "copy the old site in",
            "5",
            (
                (True, "rsync -a --exclude .git/ --info=progress2 --no-inc-recursive /path/to/old-site/ container/src/"),
                (False, "(/path/to/old-site/ is the folder that holds its index.php; keep the trailing slashes)"),
            ),
            " /path/to/old-site/",
        ),
        "discover": StepInfo("4", "let the pages suggest the profile", "6", ((True, "python3 container/tools/discover_site.py --url"), (False, "then copy the suggested lines you agree with into container/site-profile.env"))),
        "hosting": StepInfo(
            "5",
            "name the hosting the site goes to",
            "6",
            ((False, f"set SITE_HOSTING in container/site-profile.env ({hostings}), then check the .htaccess files against it:"), (True, "python3 container/tools/htaccess_hosting.py")),
            " <hosting>",
        ),
        "gate": StepInfo(
            "6 and 7",
            "the gate, then the first commit",
            "7 to 9",
            ((False, "first fix in the order of README section 8, then:"), (True, "container/tools/new-project.sh --check"), (False, "until it prints All checks passed, then the first commit (README section 9):"), (True, f'git add -- {" ".join(COMMIT_PATHS)} && git commit -m "Start {profile.get("SITE_NAME", "<site>")}"')),
        ),
        "sitemap": StepInfo("8", "generate the sitemap", "10", ((True, f"(cd container && BASE_URL={shlex.quote(profile.get('SITE_URL', EXAMPLE_URL))} tools/generate-sitemap.sh)"),)),
        "tests": StepInfo("8", "test the pages", "10", ((True, "container/tests/run_test.sh -y sitemap internal_links img_src content_loaded"), (False, "the onboarding ends here: README sections 11 (design files) and 12 (deploy) follow"))),
    }
    return infos[step]


def show_next(project: Project, state: State) -> None:
    """Where the project is and the three ways to the next step."""
    info = step_info(state.step, project.profile())
    go = "" if Path.cwd().resolve() == project.root.resolve() else f"cd {shlex.quote(str(project.root))} && "
    print(f"Project {project.slug()}, {project.root}")
    if state.done:
        print(f"Done: {', '.join(state.done)}.")
    print(f"\nNext, step {info.number} of 8: {info.what} (README section {info.section})\n")
    print(f"  recommended:    {go}container/tools/auto_onboard.sh")
    print("                  all the remaining steps; it asks when it needs you")
    print(f"  this step only: {go}container/tools/onboard.sh{info.argument}")
    print("  by hand:")
    for is_command, text in info.by_hand:
        print(f"    {go if is_command else ''}{text}")


# --- running commands --------------------------------------------------------


class Runner:
    """Prints each command the way it can be typed, then runs it in the root."""

    def __init__(self, project: Project) -> None:
        self.project = project

    def run(self, command: list[str], *, cwd: str = "", env: dict[str, str] | None = None, shown: str = "") -> int:
        print(f"\n$ {shown or shlex.join(command)}", flush=True)
        where = self.project.root / cwd if cwd else self.project.root
        return subprocess.run(command, cwd=where, env={**os.environ, **env} if env else None, check=False).returncode

    def run_to_file(self, command: list[str], report: Path) -> int:
        """The output on the screen and in report, as with tee."""
        print(f"\n$ {shlex.join(command)} | tee {report.relative_to(self.project.root)}", flush=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("w", encoding="utf-8") as out, subprocess.Popen(command, cwd=self.project.root, stdout=subprocess.PIPE, text=True) as proc:
            assert proc.stdout is not None
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                out.write(line)
        return proc.returncode


# --- the steps ---------------------------------------------------------------


def old_site_dir(project: Project, answer: str) -> Path:
    """The folder of the old site, checked; ValueError says what is wrong."""
    old = Path(answer).expanduser()
    if not old.is_dir():
        raise ValueError(f"{answer} is not a folder.")
    old = old.resolve()
    if old == project.src.resolve():
        raise ValueError("that is this project's own container/src.")
    if not (old / "index.php").exists() and not (old / "index.html").exists():
        raise ValueError(f"{old} holds no index.php or index.html. Give the folder of the site itself (often public_html/ inside a hosting copy).")
    return old


def step_start(run: Runner) -> int:
    return run.run(["docker", "compose", "up", "-d", "--build", "php-apache"], cwd="container", shown="(cd container && docker compose up -d --build php-apache)")


def step_copy(run: Runner, old: Path) -> int:
    print(f"Copying {old}/ into container/src/; the original stays as it is.")
    print("rsync counts the files first, which takes a while on a big site; then one line")
    print("shows the progress: copied, percent, speed, time left (xfr#copied, to-chk=left/total).")
    code = run.run(["rsync", "-a", "--exclude", ".git/", "--info=progress2", "--no-inc-recursive", f"{old}/", "container/src/"])
    print(f"copied: {sum(1 for p in run.project.src.rglob('*') if p.is_file())} files in container/src")
    return code


def print_mode_fix() -> None:
    for command in MODE_FIX:
        print(f"  {shlex.join(command)}")


def step_modes(run: Runner) -> int:
    for command in MODE_FIX:
        if code := run.run(command):
            return code
    print(f"executable files left that are no scripts: {len(executable_files(run.project))}")
    return 0


def step_old_sitemaps(run: Runner) -> int:
    (run.project.root / "backup" / "old-site").mkdir(parents=True, exist_ok=True)
    print("\n$ mkdir -p backup/old-site")
    return run.run(["mv", *(str(p.relative_to(run.project.root)) for p in old_sitemaps(run.project)), "backup/old-site/"])


def step_discover(run: Runner) -> tuple[int, list[str]]:
    """The discovery, its report in DISCOVER_REPORT, and its suggested lines."""
    report = run.project.root / DISCOVER_REPORT
    code = run.run_to_file(["python3", "container/tools/discover_site.py", "--url"], report)
    return code, suggested_lines(report)


def suggested_lines(report: Path) -> list[str]:
    lines = report.read_text(encoding="utf-8").splitlines() if report.is_file() else []
    if not any(line.startswith("== Suggested lines") for line in lines):
        return []
    tail = lines[next(i for i, line in enumerate(lines) if line.startswith("== Suggested lines")) + 1 :]
    return [line.strip() for line in tail if line.startswith("  SITE_") and "=" in line]


def step_hosting(run: Runner, name: str) -> int:
    if name not in dict(HOSTINGS):
        print(f"ERROR: '{name}' is none of: {', '.join(n for n, _ in HOSTINGS)}", file=sys.stderr)
        return 2
    run.project.set_profile_line(f'SITE_HOSTING="{name}"')
    print(f'written: SITE_HOSTING="{name}" in container/site-profile.env')
    run.run(["python3", "container/tools/htaccess_hosting.py"])  # breaking rules stop the gate, not this step
    return 0


def print_section_8(project: Project) -> None:
    lines = (project.root / "README.md").read_text(encoding="utf-8").splitlines()
    inside = False
    for line in lines:
        if line.startswith("## 8."):
            inside = True
        elif line.startswith("## 9."):
            break
        if inside:
            print(line)


def step_gate(run: Runner) -> int:
    return run.run(["container/tools/new-project.sh", "--check"])


def commit_command(project: Project) -> tuple[list[str], list[str]]:
    paths = [path for path in COMMIT_PATHS if (project.root / path).exists()]
    return ["git", "add", "--", *paths], ["git", "commit", "-m", f"Start {project.profile().get('SITE_NAME', project.slug())}"]


def step_commit(run: Runner) -> int:
    add, commit = commit_command(run.project)
    if run.run(add):
        return 1
    if run.run(commit) == 0:
        return 0
    print("\nThe commit stopped. When it was the image-list hook writing container/docs/image-files.txt,")
    print("staging again and committing once more finishes it (README section 9):")
    if run.run(add):
        return 1
    return run.run(commit)


def step_sitemap(run: Runner, url: str) -> int:
    return run.run(["tools/generate-sitemap.sh"], cwd="container", env={"BASE_URL": url}, shown=f"(cd container && BASE_URL={shlex.quote(url)} tools/generate-sitemap.sh)")


def step_tests(run: Runner) -> int:
    return run.run(["container/tests/run_test.sh", "-y", "sitemap", "internal_links", "img_src", "content_loaded"])


# --- one step (onboard.sh) ---------------------------------------------------


def print_gate_plan(project: Project) -> None:
    """What an imported site needs before the first full gate, with every command."""
    print("An imported site fails most checks at first, and the full gate reads every file")
    print("(half an hour on a big site). First, in this order (README section 8):")
    count = len(executable_files(project))
    if count:
        print(f"\nFile modes: {count} files are executable but are no scripts:")
        print_mode_fix()
    if sitemaps := old_sitemaps(project):
        print(f"\nThe old sitemaps ({' '.join(p.name for p in sitemaps)}): step 8 writes a new one; move them aside:")
        print("  mkdir -p backup/old-site && mv container/src/sitemap*.xml backup/old-site/")
    print("\nStage the copy once, so git shows what each fixer changes (nothing is committed):")
    print("  git add -- container/src")
    print("\nThe fixers; after each, git diff --stat -- container/src shows the change,")
    print("git add -- container/src accepts it and git checkout -- container/src undoes it:")
    for fixer in FIXERS:
        print(f"  {shlex.join(fixer.command)}\n      {fixer.label}")
    print("\nThen run container/tools/onboard.sh again: with the copy staged it runs the gate.")
    print("(auto_onboard.sh asks for each of these and runs them for you.)")


# Each one runs its step; None: done, show the next step; a number: exit with it.
def one_docker(project: Project, _run: Runner, _argument: str) -> int | None:
    show_next(project, current(project))
    return 1


def one_start(_project: Project, run: Runner, _argument: str) -> int | None:
    return 1 if step_start(run) else None


def one_copy(project: Project, run: Runner, argument: str) -> int | None:
    if not argument:
        print("Give the folder that holds the old site's index.php:\n  container/tools/onboard.sh /path/to/old-site/")
        return 2
    try:
        old = old_site_dir(project, argument)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if step_copy(run, old):
        return 1
    if count := len(executable_files(project)):
        print(f"\n{count} files are executable but are no scripts (the copy kept their modes). Fix:")
        print_mode_fix()
    return None


def one_discover(_project: Project, run: Runner, _argument: str) -> int | None:
    code, _ = step_discover(run)
    print("\nCopy the suggested lines you agree with into container/site-profile.env")
    print("(check each one in a page's source first), then run: container/tools/onboard.sh")
    return code


def one_hosting(_project: Project, run: Runner, argument: str) -> int | None:
    if not argument:
        print("Name the hosting the site is uploaded to (not the one it came from):")
        for name, label in HOSTINGS:
            print(f"  {name:<13} {label}")
        print("  container/tools/onboard.sh <hosting>")
        return 2
    return 2 if step_hosting(run, argument) else None


def one_gate(project: Project, run: Runner, _argument: str) -> int | None:
    if not staged_src(project):
        print_gate_plan(project)
        return 0
    if step_gate(run):
        print("\nFix in the order of README section 8, then run: container/tools/onboard.sh")
        return 1
    add, commit = commit_command(project)
    print("\nNow the first commit (README section 9); auto_onboard.sh can run it for you:")
    print(f"  {shlex.join(add)}\n  {shlex.join(commit)}\nthen run: container/tools/onboard.sh")
    return 0


def one_sitemap(project: Project, run: Runner, _argument: str) -> int | None:
    return 1 if step_sitemap(run, project.profile().get("SITE_URL", EXAMPLE_URL)) else None


def one_tests(_project: Project, run: Runner, _argument: str) -> int | None:
    code = step_tests(run)
    print("\nThe onboarding is done: README sections 11 (design files) and 12 (deploy) follow.")
    return code


ONE_STEP = {
    "docker": one_docker,
    "start": one_start,
    "copy": one_copy,
    "discover": one_discover,
    "hosting": one_hosting,
    "gate": one_gate,
    "sitemap": one_sitemap,
    "tests": one_tests,
}


def one_step(project: Project, argument: str) -> int:
    """The step the project is at (onboard.sh), then the one after it."""
    state = current(project)
    print(f"=== step: {state.step}")
    code = ONE_STEP[state.step](project, Runner(project), argument)
    if code is not None:
        return code
    print()
    show_next(project, current(project))
    return 0


# --- the walk (auto_onboard.sh) ---------------------------------------------


class Walk:
    """All the steps in one run; asks when it needs the user."""

    DESCRIBE = {
        "docker": "Docker",
        "start": "start the site (README section 4)",
        "copy": "copy the old site in (README section 5)",
        "discover": "let the pages suggest the profile (README section 6)",
        "hosting": "the hosting the site goes to (README section 6)",
        "gate": "the gate and the first commit (README sections 7 to 9)",
        "sitemap": "the sitemap (README section 10)",
        "tests": "the page tests (README section 10)",
    }

    def __init__(self, project: Project) -> None:
        self.project = project
        self.run = Runner(project)
        self.gate_prepared = False

    def ask(self, prompt: str) -> str:
        try:
            return input(f"\n{prompt}").strip()
        except EOFError:
            print()
            raise Stop("No answer: stopping here.") from None

    def choose(self, prompt: str, allowed: str) -> str:
        """One letter of allowed (lower case); q stops; anything else asks again."""
        while True:
            answer = self.ask(prompt).lower()[:1]
            if answer == "q":
                raise Stop("Stopped.")
            if answer in allowed:
                return answer
            print(f"Answer one of: {', '.join(allowed)}, q")

    def offer_modes(self) -> None:
        count = len(executable_files(self.project))
        if not count:
            return
        print(f"\n{count} files in container/src are executable but are no scripts: the copy kept the")
        print("modes of the old site. git would record every page and image as a program. The fix:")
        print_mode_fix()
        if self.choose("[y] run these   [s] skip   [q] stop: ", "ys") == "y":
            step_modes(self.run)

    def before_gate(self) -> None:
        print("An imported site fails most checks at first; README section 8 gives the order")
        print("to fix them. The full gate reads every file and can take half an hour.")
        if self.choose("[y] print README section 8   [n] go on   [q] stop: ", "yn") == "y":
            print()
            print_section_8(self.project)
        self.offer_modes()
        if sitemaps := old_sitemaps(self.project):
            print(f"\nThe old site's sitemaps: {' '.join(p.name for p in sitemaps)}")
            print("Step 8 writes a new sitemap.xml; until then an old one with duplicate URLs fails the gate.")
            if self.choose("[y] move them to backup/old-site/ (git-ignored)   [s] keep them   [q] stop: ", "ys") == "y":
                step_old_sitemaps(self.run)
        if self.choose("[y] run the fixers of section 8 one by one   [g] straight to the gate   [q] stop: ", "yg") == "g":
            return
        if not staged_src(self.project):
            print("\nFirst the files are staged once, so git can show what each fixer changes (nothing is committed):")
            self.run.run(["git", "add", "--", "container/src"])
        for fixer in FIXERS:
            print(f"\n--- {fixer.label}\n    {shlex.join(fixer.command)}")
            if self.choose("[y] run   [s] skip   [q] stop: ", "ys") == "s":
                continue
            self.run.run(fixer.command)
            if not fixer.changes_files:
                continue
            stat_out = self.project.git("diff", "--stat", "--", "container/src").stdout.splitlines()
            print("\n$ git diff --stat -- container/src | tail -n 3")
            print("\n".join(stat_out[-3:]) if stat_out else "(no change)")
            if not stat_out:
                continue
            answer = self.choose("[y] accept these changes (git add)   [u] undo them   [q] stop: ", "yu")
            self.run.run(["git", "add", "--", "container/src"] if answer == "y" else ["git", "checkout", "--", "container/src"])

    def step(self, name: str) -> bool:
        """Run one step; False when the walk is over."""
        handler: Callable[[], bool] = getattr(self, f"on_{name}")
        return handler()

    def on_docker(self) -> bool:
        print("Docker is not running. Start it, e.g.: sudo systemctl start docker")
        if self.ask("Press Enter when it runs, or q to stop: ").lower() == "q":
            raise Stop("Stopped.")
        return True

    def on_start(self) -> bool:
        if step_start(self.run):
            raise Stop("The site did not start: see the output above.", 1)
        return True

    def on_copy(self) -> bool:
        answer = self.ask("Folder of the old site (the one that holds its index.php), or q to stop: ")
        if answer == "q":
            raise Stop("Stopped.")
        try:
            old = old_site_dir(self.project, answer)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return True
        if step_copy(self.run, old):
            raise Stop("The copy failed: see the output above.", 1)
        self.offer_modes()
        return True

    def on_discover(self) -> bool:
        code, lines = step_discover(self.run)
        if code:
            raise Stop("The discovery failed: see the output above.", 1)
        if lines:
            print("\nSuggested for container/site-profile.env (each is a piece of the page HTML or a value the files show):")
            print("\n".join(f"  {line}" for line in lines))
            answer = self.choose("[y] write these lines   [e] edit the profile yourself   [q] stop: ", "ye")
        else:
            print("\nNothing certain enough to suggest.")
            answer = self.choose("[e] edit the profile yourself   [q] stop: ", "e")
        if answer == "y":
            for line in lines:
                self.project.set_profile_line(line)
            print(f"written: {len(lines)} lines in container/site-profile.env")
        else:
            self.run.run([*shlex.split(os.environ.get("EDITOR", "nano")), "container/site-profile.env"])
        if profile_untouched(self.project):
            raise Stop("The profile is still as new-project.sh wrote it.", 1)
        return True

    def on_hosting(self) -> bool:
        print("Where will the site be uploaded? (The hosting it came from can differ: the")
        print(".htaccess files are checked against the target.)")
        for number, (key, label) in enumerate(HOSTINGS, 1):
            print(f"  [{number}] {key:<13} {label}")
        answer = self.ask("Number, or q to stop: ")
        if answer == "q":
            raise Stop("Stopped.")
        if not answer.isdigit() or not 1 <= int(answer) <= len(HOSTINGS):
            print("ERROR: give a number from the list.", file=sys.stderr)
            return True
        step_hosting(self.run, HOSTINGS[int(answer) - 1][0])
        return True

    def on_gate(self) -> bool:
        if not self.gate_prepared:
            self.gate_prepared = True
            self.before_gate()
        if step_gate(self.run):
            print("\nFix the failures in the order of README section 8; each one names its row")
            print("in container/docs/site-checks.md.")
            if self.ask("Press Enter to run the gate again, or q to stop: ").lower() == "q":
                raise Stop("Stopped.")
            return True
        print("\nThe gate passed. Next the first commit (README section 9). What git would take in:")
        status = self.project.git("status", "--short").stdout.splitlines()
        print("\n$ git status --short | head -n 30")
        print("\n".join(status[:30]))
        print(f"({len(status)} entries; no secret and no junk should be among them)")
        if self.choose("[y] run the first commit   [n] I commit myself (then run auto_onboard.sh again)   [q] stop: ", "yn") == "n":
            raise Stop("Commit with the paths you mean, as README section 9 shows.")
        if step_commit(self.run):
            raise Stop("The commit failed: see the hook output above (README section 9).", 1)
        return True

    def on_sitemap(self) -> bool:
        url = self.project.profile().get("SITE_URL", "")
        if not url or url == EXAMPLE_URL:
            answer = self.ask("The site's public address, e.g. https://www.my-site.example (q to stop): ")
            if answer == "q":
                raise Stop("Stopped.")
            if not answer.startswith(("http://", "https://")):
                print("ERROR: give the address with http:// or https://.", file=sys.stderr)
                return True
            url = answer.rstrip("/")
            self.project.set_profile_line(f'SITE_URL="{url}"')
            print(f'written: SITE_URL="{url}" in container/site-profile.env')
        if step_sitemap(self.run, url):
            raise Stop("The sitemap failed: see the output above.", 1)
        return True

    def on_tests(self) -> bool:
        step_tests(self.run)
        print("\nThe onboarding is done. README sections 11 (design files) and 12 (deploy) follow.")
        return False

    def walk(self) -> int:
        last, repeats = "", 0
        try:
            while True:
                name = current(self.project).step
                repeats = repeats + 1 if name == last else 0
                last = name
                if name in ("start", "sitemap") and repeats:
                    raise Stop(f"The step '{name}' did not finish: see the output above.", 1)
                print(f"\n=== {self.DESCRIBE[name]}", flush=True)
                if not self.step(name):
                    return 0
        except Stop as stop:
            print(f"\n{stop}")
            print("Run container/tools/auto_onboard.sh again to go on.")
            return stop.code


# --- command line ------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onboard.py", description="Onboard a project that new-project.sh made (README.md, sections 4 to 10).")
    parser.add_argument("--root", type=Path, help="the project (default: the one this file lives in)")
    sub = parser.add_subparsers(dest="mode")
    one = sub.add_parser("step", help="run the step the project is at, then show the next")
    one.add_argument("argument", nargs="?", default="", help="the old site's folder (copy step) or the hosting (hosting step)")
    show = sub.add_parser("next", help="show where the project is")
    show.add_argument("--id", action="store_true", help="only the step, as one word")
    args = parser.parse_args(argv)
    root = main_checkout((args.root or OWN_ROOT).resolve())
    if not (root / "container" / ".env").is_file():
        print(f"ERROR: {root} has no container/.env: not a project new-project.sh made.", file=sys.stderr)
        return 2
    project = Project(root)
    if args.mode == "next":
        state = current(project)
        if args.id:
            print(state.step)
        else:
            show_next(project, state)
        return 0
    os.chdir(root)
    if args.mode == "step":
        return one_step(project, args.argument)
    return Walk(project).walk()


if __name__ == "__main__":
    sys.exit(main())
