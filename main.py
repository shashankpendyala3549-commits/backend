from flask import Flask, request, jsonify
from flask_cors import CORS
from onboardmate_lib import setup_repo

app = Flask(__name__)
CORS(app)  # Enables CORS for all origins

# Root route – confirms service is running
@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "OnboardMate backend is running!"})

# Setup route – handles repo cloning, dependency installation, smoke test
@app.route("/setup", methods=["POST"])
def setup():
    data = request.get_json()
    repo_url = data.get("repo_url")
    if not repo_url:
        return jsonify({"error": "repo_url is required"}), 400
    try:
        result = setup_repo(repo_url)
        # Add repo name for frontend display
        result["repo_name"] = repo_url.split("/")[-1].replace(".git", "")
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
