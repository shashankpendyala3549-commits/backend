import os
import uuid
from flask import Flask, request, jsonify
from flask_cors import CORS
from onboardmate_lib import setup_repo, start_background_process, get_status

BASE_DIR = "onboardmate_workspace"
PROJECTS_DIR = os.path.join(BASE_DIR, "projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)

app = Flask(__name__)
CORS(app)


@app.route("/", methods=["GET"])
def root():
    return "Backend is running", 200


@app.route("/setup", methods=["POST"])
def setup():
    data = request.get_json()
    repo_url = data.get("repo_url")

    if not repo_url:
        return jsonify({"error": "repo_url is required"}), 400

    project_id = str(uuid.uuid4())
    project_dir = os.path.join(PROJECTS_DIR, project_id)
    os.makedirs(project_dir, exist_ok=True)

    try:
        result = setup_repo(repo_url, project_dir)
        return jsonify({
            "project_id": project_id,
            "setup": result
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/start", methods=["POST"])
def start():
    data = request.get_json()
    project_id = data.get("project_id")

    if not project_id:
        return jsonify({"error": "project_id is required"}), 400

    project_dir = os.path.join(PROJECTS_DIR, project_id)

    try:
        info = start_background_process(project_dir)
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/status/<project_id>", methods=["GET"])
def status(project_id):
    project_dir = os.path.join(PROJECTS_DIR, project_id)

    try:
        info = get_status(project_dir)
        return jsonify(info)
    except Exception:
        return jsonify({"status": "unknown"}), 404


if __name__ == "__main__":
    app.run(debug=True)
