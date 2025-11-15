import os
import uuid
from pathlib import Path

from flask import Flask, request, jsonify, make_response
from flask_cors import CORS

from onboardmate_lib import (
    setup_repo,
    start_background_process,
    get_status,
    analyze_project,
)

BASE_DIR = "onboardmate_workspace"
PROJECTS_DIR = Path(BASE_DIR) / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

# Global CORS
CORS(
    app,
    resources={r"/*": {"origins": "*"}},
    supports_credentials=True,
)


@app.after_request
def add_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.route("/", methods=["GET"])
def root():
    return "Backend is running", 200


# -------------------------------
# /setup  (OPTIONS + POST)
# -------------------------------
@app.route("/setup", methods=["POST", "OPTIONS"])
def setup():
    if request.method == "OPTIONS":
        return make_response("", 200)

    data = request.get_json(force=True, silent=True) or {}
    repo_url = data.get("repo_url")

    if not repo_url:
        return jsonify({"error": "repo_url is required"}), 400

    project_id = str(uuid.uuid4())
    project_dir = PROJECTS_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = setup_repo(repo_url, str(project_dir))
        return jsonify(
            {
                "project_id": project_id,
                "setup": result,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -------------------------------
# /start  (OPTIONS + POST)
# -------------------------------
@app.route("/start", methods=["POST", "OPTIONS"])
def start():
    if request.method == "OPTIONS":
        return make_response("", 200)

    data = request.get_json(force=True, silent=True) or {}
    project_id = data.get("project_id")

    if not project_id:
        return jsonify({"error": "project_id is required"}), 400

    project_dir = PROJECTS_DIR / project_id

    try:
        info = start_background_process(str(project_dir))
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -------------------------------
# /status  (OPTIONS + GET)
# -------------------------------
@app.route("/status/<project_id>", methods=["GET", "OPTIONS"])
def status(project_id):
    if request.method == "OPTIONS":
        return make_response("", 200)

    project_dir = PROJECTS_DIR / project_id

    try:
        info = get_status(str(project_dir))
        return jsonify(info)
    except Exception:
        return jsonify({"status": "unknown"}), 404


if __name__ == "__main__":
    # For local dev; Render will use: gunicorn main:app
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
