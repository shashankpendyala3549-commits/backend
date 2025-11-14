import os
import subprocess
import tempfile
import shutil
import sys
from pathlib import Path
import git
import re
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("onboardmate")

# Map common import names to pip package names
COMMON_PACKAGE_EXCEPTIONS = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
}

IMPORT_RE = re.compile(r'^\s*(?:from|import)\s+([A-Za-z0-9_\.]+)')

def run_cmd(cmd, cwd=None, timeout=300):
    """Run subprocess command and return (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "Command timed out"

def detect_imports(repo_dir):
    """Scan Python files and detect imported top-level packages."""
    packages = set()
    py_files = list(Path(repo_dir).rglob("*.py"))
    for file in py_files:
        try:
            text = file.read_text(errors="ignore")
        except Exception:
            continue
        for line in text.splitlines():
            m = IMPORT_RE.match(line)
            if m:
                pkg = m.group(1).split(".")[0]
                # skip relative imports
                if pkg in ("..", "."):
                    continue
                packages.add(COMMON_PACKAGE_EXCEPTIONS.get(pkg, pkg))
    # Filter out likely local modules (file exists with same name)
    filtered = []
    for pkg in packages:
        # if local file exists with that name skip (best-effort)
        if (Path(repo_dir) / (pkg + ".py")).exists():
            continue
        filtered.append(pkg)
    return sorted(filtered)

def prepare_clone_url(repo_url):
    """If GITHUB_TOKEN is set, insert it into HTTPS GitHub URLs for private repo access."""
    token = os.environ.get("GITHUB_TOKEN")
    if token and ("github.com" in repo_url) and repo_url.startswith("https://"):
        # avoid exposing token in logs: build safe clone url
        # e.g. https://<token>@github.com/user/repo.git
        # if repo_url already has .git or not, keep as-is
        safe = repo_url.replace("https://", f"https://{token}@")
        return safe
    return repo_url

def setup_repo(repo_url):
    """Main worker: clone repo, detect/install deps, run smoke test, return guide/results."""
    temp_dir = Path(tempfile.mkdtemp(prefix="onboardmate_"))
    repo_dir = temp_dir / "repo"
    venv_path = temp_dir / "venv"

    try:
        logger.info("Cloning repo...")
        clone_url = prepare_clone_url(repo_url)
        git.Repo.clone_from(clone_url, str(repo_dir))

        # Create virtualenv
        logger.info("Creating virtualenv...")
        python_exe = sys.executable  # use same python as runtime
        rc, out, err = run_cmd([python_exe, "-m", "venv", str(venv_path)], timeout=120)
        if rc != 0:
            logger.warning("venv creation failed: %s", err)

        # locate pip in venv
        pip_path = venv_path / "bin" / "pip"
        py_runner = venv_path / "bin" / "python"
        if not pip_path.exists():
            pip_path = venv_path / "Scripts" / "pip.exe"
            py_runner = venv_path / "Scripts" / "python.exe"

        installed_summary = ""
        requirements_file = repo_dir / "requirements.txt"
        if requirements_file.exists():
            logger.info("Installing requirements.txt...")
            rc, out, err = run_cmd([str(pip_path), "install", "-r", str(requirements_file)], cwd=str(repo_dir), timeout=600)
            installed_summary = "Installed from requirements.txt"
            if rc != 0:
                installed_summary += f" (errors: {err[:300]})"
        else:
            # auto-detect imports
            logger.info("Detecting imports...")
            packages = detect_imports(repo_dir)
            if packages:
                logger.info("Auto-installing packages: %s", packages)
                rc, out, err = run_cmd([str(pip_path), "install"] + packages, cwd=str(repo_dir), timeout=600)
                installed_summary = f"Installed detected packages: {packages}"
                if rc != 0:
                    installed_summary += f" (errors: {err[:300]})"
            else:
                installed_summary = "No dependencies detected"

        # Smoke test: try common entrypoints
        logger.info("Running smoke test...")
        smoke_output = ""
        candidate_scripts = ["main.py", "app.py", "run.py", "manage.py"]
        found = None
        for s in candidate_scripts:
            p = repo_dir / s
            if p.exists():
                found = p
                break

        if found:
            runner = str(py_runner) if py_runner.exists() else sys.executable
            rc, out, err = run_cmd([runner, str(found)], cwd=str(repo_dir), timeout=60)
            smoke_output = out if rc == 0 else f"ERROR: {err or out}"
        else:
            # try running pytest if tests exist
            tests = list(repo_dir.rglob("test_*.py")) + list(repo_dir.rglob("*_test.py"))
            if tests:
                rc, out, err = run_cmd([str(py_runner), "-m", "pytest", "-q"], cwd=str(repo_dir), timeout=120)
                smoke_output = out if rc == 0 else f"ERROR: {err or out}"
            else:
                smoke_output = "No entrypoint or tests found; smoke test skipped."

        # First task guide (safe string)
        guide = (
            f"=== OnboardMate First Task Guide ===\n"
            f"Project: {repo_dir.name}\n"
            f"1. Activate the virtual environment:\n"
            f"   - Linux/macOS: source {venv_path}/bin/activate\n"
            f"   - Windows: {venv_path}\\Scripts\\activate\n"
            f"2. Run the main script if present (e.g. python main.py) to verify setup.\n"
            f"3. If no main script, open the README or run small tests to explore.\n"
            f"4. Your first contribution: fix a small typo, add logging, or update docs.\n"
            f"\nNote: On the server we created a temp venv; to reproduce locally, create a venv and install dependencies.\n"
        )

        return {
            "repo_name": repo_dir.name,
            "dependencies": installed_summary,
            "smoke_test": smoke_output,
            "first_task_guide": guide,
        }
    finally:
        # Optional: keep temp dir for debugging; remove to free space
        # shutil.rmtree(temp_dir, ignore_errors=True)
        logger.info("Completed setup for %s (tempdir: %s)", repo_url, temp_dir)

