import os
from flask import Flask, request, jsonify
from onboardmate_lib import setup_repo

app = Flask(__name__)

@app.route("/setup", methods=["POST"])
def setup():
    data = request.get_json(silent=True) or {}
    repo_url = data.get("repo_url")
    if not repo_url:
        return jsonify({"error": "repo_url is required"}), 400
    try:
        result = setup_repo(repo_url)
        return jsonify(result)
    except Exception as e:
        # Return error safely
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Render/Heroku provide PORT env var
    port = int(os.environ.get("PORT", 5000))
    # Do not use debug in production
    app.run(host="0.0.0.0", port=port)
