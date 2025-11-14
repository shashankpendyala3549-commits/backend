import os
import subprocess
import tempfile
from pathlib import Path
import git
import re

COMMON_PACKAGE_EXCEPTIONS = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
}

def detect_imports(repo_dir):
    """Scan Python files and detect imports"""
    packages = set()
    py_files = list(Path(repo_dir).rglob("*.py"))
    for file in py_files:
        with open(file, "r", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.startswith("import "):
                    pkg = line.split()[1].split(".")[0]
                    packages.add(COMMON_PACKAGE_EXCEPTIONS.get(pkg, pkg))
                elif line.startswith("from "):
                    pkg = line.split()[1].split(".")[0]
                    packages.add(COMMON_PACKAGE_EXCEPTIONS.get(pkg, pkg))
    return list(packages)

def setup_repo(repo_url):
    temp_dir = tempfile.mkdtemp(prefix="onboardmate_")
    repo_dir = Path(temp_dir) / "repo"
    venv_path = Path(temp_dir) / "venv"

    # Clone repo
    git.Repo.clone_from(repo_url, repo_dir)

    # Create virtualenv
    subprocess.run(["python", "-m", "venv", str(venv_path)])

    # Install dependencies
    requirements_file = repo_dir / "requirements.txt"
    pip_path = venv_path / "bin" / "pip"
    if not pip_path.exists():
        pip_path = venv_path / "Scripts" / "pip.exe"  # Windows

    if requirements_file.exists():
        subprocess.run([str(pip_path), "install", "-r", str(requirements_file)])
        installed = "Installed from requirements.txt"
    else:
        # Auto-detect imports
        packages = detect_imports(repo_dir)
        if packages:
            subprocess.run([str(pip_path), "install"] + packages)
            installed = f"Installed detected packages: {packages}"
        else:
            installed = "No dependencies detected"

    # Smoke test
    main_script = repo_dir / "main.py"
    smoke_output = ""
    if main_script.exists():
        python_path = venv_path / "bin" / "python"
        if not python_path.exists():
            python_path = venv_path / "Scripts" / "python.exe"
        result = subprocess.run([str(python_path), str(main_script)],
                                capture_output=True, text=True)
        smoke_output = result.stdout if result.returncode == 0 else result.stderr
    else:
        smoke_output = "No main.py found, smoke test skipped."

    # First task guide
    guide = f"""
=== OnboardMate First Task Guide ===
1. Activate virtual environment:
   - Linux/macOS: source {venv_path}/bin/activate
   - Windows: {venv_path}\\Scripts\\activate
2. Run the main script to verify setup:
   - python main.py
3. If no main script, open README or run small tests to explore.
4. First contribution idea: fix a small typo, add logging, or update docs.

Note: On the server, a temporary virtual environment was created; to reproduce locally, create a venv and install dependencies.
"""

    return {
        "repo_name": repo_dir.name,
        "dependencies": installed,
        "smoke_test": smoke_output,
        "first_task_guide": guide,
    }
