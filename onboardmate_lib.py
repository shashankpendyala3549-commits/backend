import os
import re
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple, Any

# Optional: LLM support (safe import)
try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


IGNORED_DIRS = {
    ".git",
    ".github",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".idea",
    ".vscode",
}


def run_cmd(cmd: List[str], cwd: Path) -> Tuple[int, str, str]:
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        out, err = proc.communicate(timeout=60)
        return proc.returncode, out, err
    except Exception as e:
        return 1, "", str(e)


def clone_repo(repo_url: str, project_dir: Path) -> None:
    """Shallow clone the repo into project_dir (if not already cloned)."""
    if (project_dir / ".git").exists():
        return
    code, out, err = run_cmd(["git", "clone", "--depth", "1", repo_url, "."], project_dir)
    if code != 0:
        raise RuntimeError(f"Failed to clone repo: {err or out}")


def detect_languages(project_dir: Path) -> List[str]:
    exts_map = {
        ".js": "JavaScript",
        ".jsx": "JavaScript",
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".py": "Python",
        ".java": "Java",
        ".go": "Go",
        ".php": "PHP",
        ".rb": "Ruby",
        ".rs": "Rust",
        ".cs": "C#",
        ".cpp": "C++",
        ".c": "C",
    }
    langs = set()
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            ext = Path(f).suffix.lower()
            if ext in exts_map:
                langs.add(exts_map[ext])
    return sorted(langs)


def detect_manifests_and_deps(project_dir: Path) -> Tuple[List[str], List[str]]:
    manifests: List[str] = []
    deps: List[str] = []

    def add_from_file(file_path: Path):
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return
        rel = str(file_path.relative_to(project_dir))
        manifests.append(rel)

        name = file_path.name.lower()
        if name == "package.json":
            try:
                data = json.loads(text)
                for section in ("dependencies", "devDependencies"):
                    for k, v in (data.get(section) or {}).items():
                        deps.append(f"{k}@{v}")
            except Exception:
                pass
        elif name in ("requirements.txt", "requirements-dev.txt"):
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                deps.append(line)
        elif name in ("pyproject.toml", "poetry.lock", "pipfile", "pipfile.lock"):
            deps.append(f"(see {rel} for Python dependencies)")
        elif name in ("go.mod", "go.sum"):
            for line in text.splitlines():
                if line.strip().startswith("require "):
                    deps.append(line.strip())
        elif name in ("pom.xml", "build.gradle", "build.gradle.kts"):
            deps.append(f"(Java build file: {rel})")
        elif name == "composer.json":
            deps.append(f"(PHP composer manifest: {rel})")
        elif name in ("cargo.toml", "cargo.lock"):
            deps.append(f"(Rust Cargo manifest: {rel})")

    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            if f.lower() in {
                "package.json",
                "requirements.txt",
                "requirements-dev.txt",
                "pyproject.toml",
                "poetry.lock",
                "pipfile",
                "pipfile.lock",
                "go.mod",
                "go.sum",
                "pom.xml",
                "build.gradle",
                "build.gradle.kts",
                "composer.json",
                "cargo.toml",
                "cargo.lock",
            }:
                add_from_file(Path(root) / f)

    seen = set()
    unique_deps: List[str] = []
    for d in deps:
        if d not in seen:
            seen.add(d)
            unique_deps.append(d)

    return manifests, unique_deps


def detect_env_files(project_dir: Path) -> Tuple[List[str], List[str]]:
    env_files: List[str] = []
    env_keys = set()

    patterns = [
        ".env",
        ".env.example",
        ".env.local",
        "appsettings.json",
        "appsettings.Development.json",
        "config.py",
        "config.js",
        "config.ts",
        "settings.py",
    ]

    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            if f in patterns or f.lower().startswith(".env"):
                p = Path(root) / f
                rel = str(p.relative_to(project_dir))
                env_files.append(rel)
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                if f.startswith(".env"):
                    for line in text.splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key = line.split("=", 1)[0].strip()
                        if key:
                            env_keys.add(key)
                elif f.endswith(".json"):
                    try:
                        data = json.loads(text)
                        for k in data.keys():
                            env_keys.add(k)
                    except Exception:
                        pass
                elif f.endswith(".py"):
                    for match in re.finditer(r"os\\.environ\\[['\\\"](.*?)['\\\"]\\]", text):
                        env_keys.add(match.group(1))

    return sorted(env_files), sorted(env_keys)


def build_directory_tree(project_dir: Path, max_depth: int = 3, max_entries: int = 60) -> str:
    lines: List[str] = []
    count = 0

    def walk(path: Path, prefix: str, depth: int):
        nonlocal count
        if depth > max_depth or count >= max_entries:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except Exception:
            return
        for i, entry in enumerate(entries):
            if entry.name in IGNORED_DIRS:
                continue
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append(prefix + connector + entry.name)
            count += 1
            if count >= max_entries:
                lines.append(prefix + "└── ...")
                return
            if entry.is_dir():
                extension = "    " if is_last else "│   "
                walk(entry, prefix + extension, depth + 1)

    lines.append(project_dir.name + "/")
    walk(project_dir, "", 1)
    return "\n".join(lines)


def detect_run_and_build_commands(project_dir: Path) -> Dict[str, List[str]]:
    cmds: Dict[str, List[str]] = {
        "install": [],
        "build": [],
        "run": [],
        "test": [],
    }

    try:
        root_files = {p.name.lower() for p in project_dir.iterdir()}
    except Exception:
        root_files = set()

    # Node / JS / TS
    if "package.json" in root_files:
        cmds["install"].append("npm install  # or: pnpm install / yarn")
        cmds["run"].append("npm start  # or: npm run dev")
        cmds["build"].append("npm run build")
        cmds["test"].append("npm test  # if defined in package.json")

    # Python
    if "requirements.txt" in root_files or "pyproject.toml" in root_files:
        cmds["install"].append(
            "python -m venv .venv && source .venv/bin/activate  "
            "# Windows: .venv\\Scripts\\activate"
        )
        if "requirements.txt" in root_files:
            cmds["install"].append("pip install -r requirements.txt")
        else:
            cmds["install"].append("pip install -e .")

        main_candidates = list(project_dir.glob("**/main.py"))
        if main_candidates:
            rel = main_candidates[0].relative_to(project_dir)
            cmds["run"].append(f"python {rel}")
        cmds["test"].append("pytest  # or: python -m pytest / unittest")

    # Go
    if "go.mod" in root_files:
        cmds["install"].append("go mod tidy")
        cmds["build"].append("go build ./...")
        cmds["run"].append("go run ./...")
        cmds["test"].append("go test ./...")

    # Java / Maven / Gradle
    if "pom.xml" in root_files:
        cmds["install"].append("mvn clean install")
        cmds["build"].append("mvn package")
        cmds["test"].append("mvn test")
    if "build.gradle" in root_files or "build.gradle.kts" in root_files:
        cmds["install"].append("./gradlew build  # Windows: gradlew.bat build")
        cmds["build"].append("./gradlew assemble")
        cmds["test"].append("./gradlew test")

    # PHP
    if "composer.json" in root_files:
        cmds["install"].append("composer install")
        cmds["test"].append("composer test  # if defined in composer.json")

    # Rust
    if "cargo.toml" in root_files:
        cmds["install"].append("cargo fetch")
        cmds["build"].append("cargo build")
        cmds["run"].append("cargo run")
        cmds["test"].append("cargo test")

    # Generic fallbacks
    if not cmds["install"]:
        cmds["install"].append("Follow README.md for install instructions.")
    if not cmds["run"]:
        cmds["run"].append("Follow README.md for run/start command.")
    if not cmds["test"]:
        cmds["test"].append("No explicit test command detected.")

    return cmds


def maybe_use_llm(section_name: str, prompt: str, fallback: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or OpenAI is None:
        return fallback
    try:
        client = OpenAI(api_key=api_key)
        completion = client.responses.create(
            model="gpt-4.1-mini",
            input=prompt,
            max_output_tokens=400,
        )
        # Responses API: pull first text output
        for item in completion.output:
            if hasattr(item, "content"):
                for part in item.content:
                    if getattr(part, "type", "") == "output_text":
                        return getattr(part, "text", fallback)
        return fallback
    except Exception:
        return fallback


def build_architecture_overview(
    project_dir: Path,
    languages: List[str],
    manifests: List[str],
    tree: str,
) -> str:
    fallback = (
        "High-level architecture overview (heuristic):\n"
        f"- Primary languages: {', '.join(languages) if languages else 'Unknown'}\n"
        f"- Key manifests: {', '.join(manifests) if manifests else 'None detected'}\n"
        "- The repository appears to follow a standard project layout. "
        "See the directory tree above for more detail."
    )
    prompt = f"""You are helping a developer onboard to a new codebase.

Repository languages:
{languages}

Detected manifests / config files:
{manifests}

Truncated directory tree:
{tree}

Write a short 'Architecture Overview' (max ~200 words) explaining how the project is structured, what the main components are, and how they likely interact. Aim it at a developer joining the project for the first time. Use bullet points where helpful."""
    return maybe_use_llm("architecture_overview", prompt, fallback)


def build_must_know_section(
    languages: List[str],
    env_keys: List[str],
    run_cmds: Dict[str, List[str]],
) -> str:
    fallback = (
        "Before you start making changes, you should:\n"
        "- Make sure you can run the project locally using the commands above.\n"
        "- Understand how configuration and environment variables are managed.\n"
        "- Identify the main entrypoint files (e.g., app server, CLI, or frontend root).\n"
        "- Skim the tests to see how behavior is verified."
    )
    prompt = f"""Given this project information:

Languages: {languages}
Env var keys: {env_keys}
Run & build commands: {run_cmds}

Write a short section titled 'What you must know first' for a new developer. Focus on 4–7 bullet points that they should understand before making any non-trivial change."""
    return maybe_use_llm("what_you_must_know_first", prompt, fallback)


def build_first_issue(project_dir: Path, languages: List[str]) -> str:
    fallback = (
        "Suggestion: Pick a small, low-risk improvement, such as:\n"
        "- Run the project locally and document any missing steps in the README.\n"
        "- Improve logging or error messages around a common failure path.\n"
        "- Add or fix a unit test for a core function.\n"
        "- Clean up obvious TODOs or dead code in a small module."
    )
    prompt = f"""You're helping a newcomer pick a 'first issue' in a repository.

Languages: {languages}

Suggest one concrete, beginner-friendly task that:
- Is small and achievable in 1–3 hours,
- Requires reading real code,
- Adds real value (tests, docs, small refactor, missing validation, etc.).

Write it as a short paragraph plus 3–5 checklist steps."""
    return maybe_use_llm("first_issue", prompt, fallback)


def build_steps_to_run_locally(run_cmds: Dict[str, List[str]]) -> str:
    lines: List[str] = ["Recommended local run workflow:"]
    if run_cmds["install"]:
        lines.append("\n1) Install dependencies:")
        for c in run_cmds["install"]:
            lines.append(f"   - {c}")
    if run_cmds["build"]:
        lines.append("\n2) Build (if needed):")
        for c in run_cmds["build"]:
            lines.append(f"   - {c}")
    if run_cmds["run"]:
        lines.append("\n3) Run the app:")
        for c in run_cmds["run"]:
            lines.append(f"   - {c}")
    if run_cmds["test"]:
        lines.append("\n4) Run tests:")
        for c in run_cmds["test"]:
            lines.append(f"   - {c}")
    return "\n".join(lines)


def analyze_project(repo_url: str, project_dir: str) -> Dict[str, Any]:
    pdir = Path(project_dir)
    pdir.mkdir(parents=True, exist_ok=True)

    # 1) Clone (shallow)
    clone_repo(repo_url, pdir)

    # 2) Static analysis
    languages = detect_languages(pdir)
    manifests, deps = detect_manifests_and_deps(pdir)
    env_files, env_keys = detect_env_files(pdir)
    tree = build_directory_tree(pdir)
    run_cmds = detect_run_and_build_commands(pdir)

    architecture_overview = build_architecture_overview(pdir, languages, manifests, tree)
    must_know = build_must_know_section(languages, env_keys, run_cmds)
    first_issue = build_first_issue(pdir, languages)
    steps_run = build_steps_to_run_locally(run_cmds)

    # Derived legacy-like fields to keep frontend compatible
    first_task = first_issue.split("\n", 1)[0]
    guide_parts = [
        "# What you must know first",
        must_know,
        "",
        "# Steps to run locally",
        steps_run,
        "",
        "# Suggested first issue",
        first_issue,
        "",
        "# Architecture overview",
        architecture_overview,
    ]
    guide = "\n".join(guide_parts)

    return {
        "project_type": ", ".join(languages) if languages else "Unknown",
        "languages": languages,
        "manifests": manifests,
        "dependencies": deps,
        "env_files": env_files,
        "env_variables": env_keys,
        "directory_tree": tree,
        "commands": run_cmds,
        "architecture_overview": architecture_overview,
        "what_you_must_know_first": must_know,
        "first_issue": first_issue,
        "steps_to_run_locally": steps_run,
        # legacy-style fields used in existing frontend
        "first_task": first_task,
        "first_task_guide": guide,
        "smoke_test": "Run the app locally and ensure the main page loads without errors.",
    }


def setup_repo(repo_url: str, project_dir: str) -> Dict[str, Any]:
    """Entry point used by the Flask route. Thin wrapper around analyze_project."""
    return analyze_project(repo_url, project_dir)


def start_background_process(project_dir: str) -> Dict[str, Any]:
    """On free Render tier we don't actually start anything.

    Instead, we tell the frontend to look at 'steps_to_run_locally'.
    """
    return {
        "message": (
            "Background start is not available on this deployment. "
            "Use the 'Steps to run locally' section instead."
        )
    }


def get_status(project_dir: str) -> Dict[str, Any]:
    # Minimal stub – in future you could track a status.json in project_dir
    return {"status": "not-tracked"}
