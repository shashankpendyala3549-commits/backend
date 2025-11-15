import os
import subprocess
import git
from pathlib import Path
import json
import signal

COMMON_PACKAGE_EXCEPTIONS = {"cv2": "opencv-python", "PIL": "Pillow"}

# -------------------------------------------------------------
# 1. DETECT PROJECT TYPE
# -------------------------------------------------------------
def detect_project_type(repo_dir):
    files = os.listdir(repo_dir)

    if "package.json" in files:
        return "node"

    if "requirements.txt" in files:
        return "python"

    # Detect Python even without req file
    for f in Path(repo_dir).rglob("*.py"):
        return "python"

    # Detect JavaScript
    for f in Path(repo_dir).rglob("*.js"):
        return "javascript"

    return "unknown"


# -------------------------------------------------------------
# 2. DETECT IMPORTS / DEPENDENCIES
# -------------------------------------------------------------
def detect_imports(repo_dir):
    packages = set()
    for file in Path(repo_dir).rglob("*.py"):
        for line in open(file, "r", errors="ignore"):
            line = line.strip()
            if line.startswith("import "):
                pkg = line.split()[1].split(".")[0]
                packages.add(COMMON_PACKAGE_EXCEPTIONS.get(pkg, pkg))
            elif line.startswith("from "):
                pkg = line.split()[1].split(".")[0]
                packages.add(COMMON_PACKAGE_EXCEPTIONS.get(pkg, pkg))
    return list(packages)


# -------------------------------------------------------------
# 3. FIRST RUNNABLE TASK DETECTOR
# -------------------------------------------------------------
def find_first_task(repo_dir):
    # Python entry points
    if os.path.exists(os.path.join(repo_dir, "app.py")):
        return "Run: python app.py"
    if os.path.exists(os.path.join(repo_dir, "main.py")):
        return "Run: python main.py"
    if os.path.exists(os.path.join(repo_dir, "manage.py")):
        return "Run: python manage.py runserver"

    # Node entry point
    if os.path.exists(os.path.join(repo_dir, "package.json")):
        return "Run: npm install && npm start"

    # fallback
    return "Open the repo and run the simplest script found."


# -------------------------------------------------------------
# 4. "FIRST 10 MINUTES" CHECKLIST
# -------------------------------------------------------------
def generate_first_10_min_guide(project_type, first_task):
    if project_type == "python":
        return f"""
1. Create a virtual environment
   python -m venv venv

2. Activate it
   Windows: venv\\Scripts\\activate
   Mac/Linux: source venv/bin/activate

3. Install dependencies
   pip install -r requirements.txt

4. Run a quick smoke test
   python -c "print('Environment OK')"

5. Try the first runnable task
   {first_task}
"""

    if project_type == "node":
        return f"""
1. Install Node.js (LTS version)

2. Install project dependencies
   npm install

3. Run a basic smoke test
   node -e "console.log('Environment OK')"

4. Start project
   {first_task}
"""

    return """
1. Explore the repo structure
2. Identify key components (src, config, entrypoints)
3. Check if dependencies exist
4. Run simplest available script
"""


# -------------------------------------------------------------
# 5. SMOKE TEST EXECUTION (SIMULATED)
# -------------------------------------------------------------
def run_smoke_test(project_type):
    if project_type == "python":
        return "✔ Python environment validated (simulated)."
    if project_type == "node":
        return "✔ Node environment validated (simulated)."
    return "Skipped (unknown project type)"


# -------------------------------------------------------------
# 6. MAIN SETUP FUNCTION
# -------------------------------------------------------------
def setup_repo(repo_url, project_dir):
    repo_dir = os.path.join(project_dir, "repo")
    venv_dir = os.path.join(project_dir, "venv")

    # Clone repo
    git.Repo.clone_from(repo_url, repo_dir)

    # Detect project type
    project_type = detect_project_type(repo_dir)

    # Find dependencies
    req_file = os.path.join(repo_dir, "requirements.txt")
    if os.path.exists(req_file):
        deps = open(req_file).read().splitlines()
    else:
        deps = detect_imports(repo_dir)

    # First runnable task
    first_task = find_first_task(repo_dir)

    # First 10-minute guide
    guide = generate_first_10_min_guide(project_type, first_task)

    # Smoke test
    smoke = run_smoke_test(project_type)

    return {
        "project_path": project_dir,
        "repo_path": repo_dir,
        "venv": venv_dir,
        "project_type": project_type,
        "dependencies": deps,
        "first_task": first_task,
        "first_task_guide": guide,
        "smoke_test": smoke
    }


# -------------------------------------------------------------
# 7. STARTER LOGIC (unchanged)
# -------------------------------------------------------------
def find_start_command(repo_dir):
    if os.path.exists(os.path.join(repo_dir, "app.py")):
        return ["python", "app.py"]
    if os.path.exists(os.path.join(repo_dir, "main.py")):
        return ["python", "main.py"]
    if os.path.exists(os.path.join(repo_dir, "manage.py")):
        return ["python", "manage.py", "runserver"]
    if os.path.exists(os.path.join(repo_dir, "package.json")):
        return ["npm", "start"]
    return None


def start_background_process(project_dir):
    repo_dir = os.path.join(project_dir, "repo")
    venv_dir = os.path.join(project_dir, "venv")
    pid_file = os.path.join(project_dir, "process.json")

    cmd = find_start_command(repo_dir)
    if not cmd:
        raise Exception("No recognized start command")

    env = os.environ.copy()
    env["PATH"] = os.path.join(venv_dir, "bin") + os.pathsep + env["PATH"]

    process = subprocess.Popen(
        cmd,
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env
    )

    json.dump({"pid": process.pid, "command": cmd}, open(pid_file, "w"))

    return {"status": "started", "pid": process.pid, "command": cmd}


def get_status(project_dir):
    pid_file = os.path.join(project_dir, "process.json")
    if not os.path.exists(pid_file):
        return {"status": "no-process"}

    info = json.load(open(pid_file))
    pid = info["pid"]

    try:
        os.kill(pid, 0)
        return {"alive": True, "pid": pid, "command": info["command"]}
    except:
        return {"alive": False, "pid": pid, "command": info["command"]}
