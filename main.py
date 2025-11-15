import os
import uuid
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from onboardmate_lib import setup_repo, start_background_process, get_status

BASE_DIR = "onboardmate_workspace"
PROJECTS_DIR = os.path.join(BASE_DIR, "projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)

app = Flask(__name__)

# Global CORS
CORS(
    app,
    resources={r"/*": {"origins": "*"}},
    supports_credentials=True
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
# HANDLE /setup WITH OPTIONS + POST
# -------------------------------
@app.route("/setup", methods=["POST", "OPTIONS"])
def setup():
    # Preflight request
    if request.method == "OPTIONS":
        return make_response("", 200)

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


# -------------------------------
# HANDLE /start WITH OPTIONS + POST
# -------------------------------
@app.route("/start", methods=["POST", "OPTIONS"])
def start():
    if request.method == "OPTIONS":
        return make_response("", 200)

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


# -------------------------------
# HANDLE /status WITH OPTIONS + GET
# -------------------------------
@app.route("/status/<project_id>", methods=["GET", "OPTIONS"])
def status(project_id):
    if request.method == "OPTIONS":
        return make_response("", 200)

    project_dir = os.path.join(PROJECTS_DIR, project_id)

    try:
        info = get_status(project_dir)
        return jsonify(info)
    except Exception:
        return jsonify({"status": "unknown"}), 404


if __name__ == "__main__":
    app.run(debug=True)
