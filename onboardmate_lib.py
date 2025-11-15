import os
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional


# -----------------------------
# Helpers
# -----------------------------
def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _run_git_clone(repo_url: str, project_dir: str) -> None:
    """Clone repo into project_dir (shallow clone), if not already cloned."""
    if any(Path(project_dir).iterdir()):
        # Directory not empty, assume already cloned
        return

    cmd = ["git", "clone", "--depth", "1", repo_url, project_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Git clone failed: {proc.stderr.strip() or proc.stdout.strip()}")


# -----------------------------
# Detection: language & project type
# -----------------------------
def detect_language_and_type(project_dir: str) -> Dict[str, Any]:
    p = Path(project_dir)
    files = {f.name for f in p.rglob("*") if f.is_file()}

    # Simple helpers
    has = lambda name: name in files

    language = "unknown"
    project_type = "generic"
    frameworks: List[str] = []

    # Node / JS / TS
    if has("package.json"):
        language = "JavaScript/TypeScript"
        package_json_path = p / "package.json"
        pkg = {}
        try:
            pkg = json.loads(_safe_read_text(package_json_path) or "{}")
        except Exception:
            pkg = {}

        deps = {
            **pkg.get("dependencies", {}),
            **pkg.get("devDependencies", {})
        }

        # Framework heuristics
        if "react" in deps or "next" in deps or "nextjs" in deps:
            project_type = "React/Next.js web app"
            frameworks.append("React/Next.js")
        elif "vue" in deps:
            project_type = "Vue.js web app"
            frameworks.append("Vue")
        elif "angular" in deps or "@angular/core" in deps:
            project_type = "Angular web app"
            frameworks.append("Angular")
        elif "express" in deps:
            project_type = "Node.js API (Express)"
            frameworks.append("Express")
        else:
            project_type = "Node.js project"
            frameworks.append("Node.js")

        return {
            "language": language,
            "project_type": project_type,
            "frameworks": frameworks,
        }

    # Python
    py_files = [f for f in files if f.endswith(".py")]
    if py_files:
        language = "Python"
        if has("manage.py"):
            project_type = "Django web app"
            frameworks.append("Django")
        elif has("app.py") or has("main.py"):
            app_txt = _safe_read_text(p / "app.py") + _safe_read_text(p / "main.py")
            if "flask" in app_txt.lower():
                project_type = "Flask web app"
                frameworks.append("Flask")
            elif "fastapi" in app_txt.lower():
                project_type = "FastAPI service"
                frameworks.append("FastAPI")
            else:
                project_type = "Python application"
        else:
            project_type = "Python project"

        return {
            "language": language,
            "project_type": project_type,
            "frameworks": frameworks,
        }

    # Java
    if has("pom.xml") or has("build.gradle") or has("build.gradle.kts"):
        language = "Java/Kotlin"
        project_type = "Spring/Java backend" if has("pom.xml") else "Gradle-based Java project"
        frameworks.append("Spring Boot")
        return {
            "language": language,
            "project_type": project_type,
            "frameworks": frameworks,
        }

    # Go
    if has("go.mod"):
        language = "Go"
        project_type = "Go service"
        frameworks.append("Go modules")
        return {
            "language": language,
            "project_type": project_type,
            "frameworks": frameworks,
        }

    # PHP
    if has("composer.json"):
        language = "PHP"
        project_type = "PHP project"
        frameworks.append("Composer-based")
        return {
            "language": language,
            "project_type": project_type,
            "frameworks": frameworks,
        }

    # Ruby
    if has("Gemfile"):
        language = "Ruby"
        project_type = "Ruby project"
        frameworks.append("Ruby/Gemfile")
        return {
            "language": language,
            "project_type": project_type,
            "frameworks": frameworks,
        }

    # .NET
    csproj = [f for f in files if f.endswith(".csproj")]
    if csproj:
        language = "C#"
        project_type = ".NET project"
        frameworks.append(".NET")
        return {
            "language": language,
            "project_type": project_type,
            "frameworks": frameworks,
        }

    return {
        "language": language,
        "project_type": project_type,
        "frameworks": frameworks,
    }


# -----------------------------
# Detection: dependencies
# -----------------------------
def detect_dependencies(project_dir: str) -> List[str]:
    p = Path(project_dir)
    deps: List[str] = []

    # Node
    pkg_path = p / "package.json"
    if pkg_path.exists():
        try:
            pkg = json.loads(_safe_read_text(pkg_path) or "{}")
            for name, ver in (pkg.get("dependencies") or {}).items():
                deps.append(f"{name}@{ver}")
            for name, ver in (pkg.get("devDependencies") or {}).items():
                deps.append(f"{name}@{ver} (dev)")
        except Exception:
            pass

    # Python
    for fname in ["requirements.txt", "requirements-dev.txt", "Pipfile", "pyproject.toml"]:
        fpath = p / fname
        if fpath.exists():
            text = _safe_read_text(fpath)
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                deps.append(line)

    # Go
    gomod = p / "go.mod"
    if gomod.exists():
        text = _safe_read_text(gomod)
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("require"):
                # e.g. require github.com/gin-gonic/gin v1.8.1
                line = line.replace("require", "").strip()
                if line:
                    deps.append(line)

    # PHP
    composer = p / "composer.json"
    if composer.exists():
        try:
            c = json.loads(_safe_read_text(composer) or "{}")
            for name, ver in (c.get("require") or {}).items():
                deps.append(f"{name}:{ver}")
        except Exception:
            pass

    # Ruby
    gemfile = p / "Gemfile"
    if gemfile.exists():
        text = _safe_read_text(gemfile)
        for line in text.splitlines():
            m = re.search(r'gem\s+["\']([^"\']+)["\']', line)
            if m:
                deps.append(m.group(1))

    # .NET
    for f in p.rglob("*.csproj"):
        text = _safe_read_text(f)
        matches = re.findall(r"<PackageReference Include=\"([^\"]+)\" Version=\"([^\"]+)\"", text)
        for name, ver in matches:
            deps.append(f"{name}:{ver}")

    # Deduplicate
    unique: List[str] = []
    seen = set()
    for d in deps:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


# -----------------------------
# Detection: commands
# -----------------------------
def detect_node_commands(project_dir: str) -> Dict[str, List[str]]:
    p = Path(project_dir)
    pkg_path = p / "package.json"
    cmds = {"install": [], "run": [], "test": []}
    if not pkg_path.exists():
        return cmds

    try:
        pkg = json.loads(_safe_read_text(pkg_path) or "{}")
        scripts = pkg.get("scripts") or {}
    except Exception:
        scripts = {}

    # install
    cmds["install"].append("npm install")
    cmds["install"].append("yarn install")

    # run
    if "dev" in scripts:
        cmds["run"].append("npm run dev")
        cmds["run"].append("yarn dev")
    if "start" in scripts:
        cmds["run"].append("npm start")
        cmds["run"].append("yarn start")
    if not cmds["run"]:
        cmds["run"].append("npm run start || npm run dev")

    # test
    if "test" in scripts:
        cmds["test"].append("npm test")
        cmds["test"].append("yarn test")

    return cmds


def detect_python_commands(project_dir: str) -> Dict[str, List[str]]:
    p = Path(project_dir)
    cmds = {"install": [], "run": [], "test": []}

    # install
    if (p / "requirements.txt").exists():
        cmds["install"].append("pip install -r requirements.txt")
    if (p / "pyproject.toml").exists():
        cmds["install"].append("pip install .")

    # run
    if (p / "manage.py").exists():
        cmds["run"].append("python manage.py runserver")
    elif (p / "app.py").exists():
        cmds["run"].append("python app.py")
    elif (p / "main.py").exists():
        cmds["run"].append("python main.py")

    # test
    if any(f.name.startswith("test_") for f in p.rglob("test_*.py")) or any(
        f.name.endswith("_test.py") for f in p.rglob("*_test.py")
    ):
        cmds["test"].append("pytest")
        cmds["test"].append("python -m pytest")

    return cmds


def detect_java_commands(project_dir: str) -> Dict[str, List[str]]:
    p = Path(project_dir)
    cmds = {"install": [], "run": [], "test": []}
    if (p / "pom.xml").exists():
        cmds["install"].append("mvn clean install")
        cmds["run"].append("mvn spring-boot:run")
        cmds["test"].append("mvn test")
    if (p / "build.gradle").exists() or (p / "build.gradle.kts").exists():
        cmds["install"].append("./gradlew build")
        cmds["run"].append("./gradlew bootRun")
        cmds["test"].append("./gradlew test")
    return cmds


def detect_go_commands(project_dir: str) -> Dict[str, List[str]]:
    p = Path(project_dir)
    cmds = {"install": [], "run": [], "test": []}
    if (p / "go.mod").exists():
        cmds["install"].append("go mod tidy")
        cmds["run"].append("go run ./...")
        cmds["test"].append("go test ./...")
    return cmds


def detect_docker_commands(project_dir: str) -> Dict[str, List[str]]:
    p = Path(project_dir)
    cmds = {"install": [], "run": [], "test": []}
    if (p / "Dockerfile").exists():
        cmds["install"].append("docker build -t myapp .")
        cmds["run"].append("docker run -p 3000:3000 myapp")
    return cmds


def merge_commands(*cmd_dicts: Dict[str, List[str]]) -> Dict[str, List[str]]:
    merged = {"install": [], "run": [], "test": []}
    seen = {"install": set(), "run": set(), "test": set()}
    for d in cmd_dicts:
        for key in ["install", "run", "test"]:
            for cmd in d.get(key, []):
                if cmd not in seen[key]:
                    seen[key].add(cmd)
                    merged[key].append(cmd)
    return merged


# -----------------------------
# Detection: env hints & tests
# -----------------------------
def detect_env_hints(project_dir: str) -> Dict[str, Any]:
    p = Path(project_dir)
    env_files = []
    env_vars = set()

    for name in [".env", ".env.local", ".env.development", ".env.example"]:
        f = p / name
        if f.exists():
            env_files.append(str(f.relative_to(p)))
            text = _safe_read_text(f)
            for line in text.splitlines():
                m = re.match(r"\s*([A-Z0-9_]+)\s*=", line)
                if m:
                    env_vars.add(m.group(1))

    return {
        "env_files": env_files,
        "env_vars": sorted(env_vars),
    }


# -----------------------------
# First task & guide
# -----------------------------
def generate_first_task(meta: Dict[str, Any]) -> str:
    project_type = meta.get("project_type") or "project"
    language = meta.get("language") or "codebase"

    return (
        f"Verify you can run the {project_type} locally.\n"
        f"1. Install dependencies.\n"
        f"2. Start the local server or dev process.\n"
        f"3. Confirm the main page or API health endpoint works.\n"
        f"4. Run the test suite (if available)."
    )


def generate_first_task_guide(
    meta: Dict[str, Any],
    commands: Dict[str, List[str]],
    env_hints: Dict[str, Any]
) -> str:
    lines: List[str] = []
    project_type = meta.get("project_type") or "project"
    language = meta.get("language") or "codebase"

    lines.append(f"# First 10 minutes in this {project_type}")
    lines.append("")
    lines.append("## 1. Clone & navigate")
    lines.append("```bash")
    lines.append("# (Already handled by OnboardMate in analysis)")
    lines.append("```")
    lines.append("")

    # Install
    lines.append("## 2. Install dependencies")
    if commands.get("install"):
        lines.append("Run one of:")
        lines.append("```bash")
        for c in commands["install"]:
            lines.append(c)
        lines.append("```")
    else:
        lines.append("No specific install command detected. Check the README.")
    lines.append("")

    # Env
    env_files = env_hints.get("env_files") or []
    env_vars = env_hints.get("env_vars") or []
    if env_files or env_vars:
        lines.append("## 3. Configure environment variables")
        if env_files:
            lines.append("Check these files for environment settings:")
            for f in env_files:
                lines.append(f" - `{f}`")
        if env_vars:
            lines.append("")
            lines.append("Common required variables:")
            for v in env_vars:
                lines.append(f" - `{v}`")
        lines.append("")

    # Run
    lines.append("## 4. Start the project locally")
    if commands.get("run"):
        lines.append("Use one of:")
        lines.append("```bash")
        for c in commands["run"]:
            lines.append(c)
        lines.append("```")
    else:
        lines.append("No run command detected. Look for instructions in the README or package scripts.")
    lines.append("")

    # Test
    lines.append("## 5. Run tests")
    if commands.get("test"):
        lines.append("Try:")
        lines.append("```bash")
        for c in commands["test"]:
            lines.append(c)
        lines.append("```")
    else:
        lines.append("No test command detected. You can skip this for now if no tests exist.")
    lines.append("")

    lines.append("## 6. Make a tiny change")
    lines.append("- Add a console log / print statement")
    lines.append("- Restart / reload the app to confirm your change appears")
    lines.append("- Commit your change on a new branch")

    return "\n".join(lines)


def generate_smoke_test(commands: Dict[str, List[str]]) -> str:
    parts = []
    if commands.get("run"):
        parts.append("1. Start the app locally with one of:")
        parts.extend([f"   - {c}" for c in commands["run"]])
    if commands.get("test"):
        parts.append("2. Run the test suite:")
        parts.extend([f"   - {c}" for c in commands["test"]])
    if not parts:
        parts.append("Run the main application and ensure the home page or health endpoint responds.")

    return "\n".join(parts)


# -----------------------------
# Public API
# -----------------------------
def analyze_project(project_dir: str) -> Dict[str, Any]:
    meta = detect_language_and_type(project_dir)
    deps = detect_dependencies(project_dir)
    env_hints = detect_env_hints(project_dir)

    language = meta.get("language")

    cmd_node = detect_node_commands(project_dir) if language == "JavaScript/TypeScript" else {}
    cmd_py = detect_python_commands(project_dir) if language == "Python" else {}
    cmd_java = detect_java_commands(project_dir) if language and "Java" in language else {}
    cmd_go = detect_go_commands(project_dir) if language == "Go" else {}
    cmd_docker = detect_docker_commands(project_dir)

    commands = merge_commands(cmd_node, cmd_py, cmd_java, cmd_go, cmd_docker)

    first_task = generate_first_task(meta)
    guide = generate_first_task_guide(meta, commands, env_hints)
    smoke_test = generate_smoke_test(commands)

    return {
        "language": meta.get("language"),
        "project_type": meta.get("project_type"),
        "frameworks": meta.get("frameworks"),
        "dependencies": deps,
        "env_files": env_hints.get("env_files"),
        "env_vars": env_hints.get("env_vars"),
        "commands": commands,
        "first_task": first_task,
        "first_task_guide": guide,
        "smoke_test": smoke_test,
    }


def setup_repo(repo_url: str, project_dir: str) -> Dict[str, Any]:
    """
    Clone the repo (if needed) and analyze it for onboarding.
    """
    _run_git_clone(repo_url, project_dir)
    return analyze_project(project_dir)


def start_background_process(project_dir: str) -> Dict[str, Any]:
    """
    On Render free tier we do NOT actually start background processes.
    Instead we just re-use analyze_project() and return recommended commands
    for the user to run locally.
    """
    info = analyze_project(project_dir)
    return {
        "mode": "local_only",
        "note": (
            "Render free tier does not support long-lived background dev servers. "
            "Run these commands locally in your terminal."
        ),
        "commands": info.get("commands", {}),
        "project_type": info.get("project_type"),
        "language": info.get("language"),
    }

