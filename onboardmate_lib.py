import os
import subprocess
import git
from pathlib import Path
import signal
import json

COMMON_PACKAGE_EXCEPTIONS = {"cv2": "opencv-python", "PIL": "Pillow"}


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


def setup_repo(repo_url, project_dir):
    repo_dir = os.path.join(project_dir, "repo")
    venv_dir = os.path.join(project_dir, "venv")

    git.Repo.clone_from(repo_url, repo_dir)

    subprocess.run(["python", "-m", "venv", venv_dir])
    pip_path = os.path.join(venv_dir, "Scripts", "pip.exe") if os.name == "nt" else os.path.join(venv_dir, "bin", "pip")

    req_file = os.path.join(repo_dir, "requirements.txt")

    if os.path.exists(req_file):
        subprocess.run([pip_path, "install", "-r", req_file])
        installed = "Installed from requirements.txt"
    else:
        pkgs = detect_imports(repo_dir)
        if pkgs:
            subprocess.run([pip_path, "install"] + pkgs)
            installed = f"Auto-installed: {pkgs}"
        else:
            installed = "No dependencies found"

    return {
        "project_path": project_dir,
        "repo_path": repo_dir,
        "venv": venv_dir,
        "dependencies": installed
    }


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
        raise Exception("No recognized way to start this project.")

    env = os.environ.copy()
    env["PATH"] = os.path.join(venv_dir, "bin") + os.pathsep + env["PATH"]

    process = subprocess.Popen(
        cmd,
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env
    )

    with open(pid_file, "w") as f:
        json.dump({"pid": process.pid, "command": cmd}, f)

    return {"status": "started", "pid": process.pid, "command": cmd}


def get_status(project_dir):
    pid_file = os.path.join(project_dir, "process.json")
    if not os.path.exists(pid_file):
        return {"status": "no-process"}

    info = json.load(open(pid_file))
    pid = info["pid"]

    try:
        os.kill(pid, 0)
        alive = True
    except OSError:
        alive = False

    return {"alive": alive, "pid": pid, "command": info["command"]}
