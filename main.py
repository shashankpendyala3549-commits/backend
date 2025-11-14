from flask import Flask, request, jsonify
from onboardmate_lib import setup_repo

app = Flask(__name__)

@app.route("/setup", methods=["POST"])
def setup():
    data = request.get_json()
    repo_url = data.get("repo_url")
    if not repo_url:
        return jsonify({"error": "repo_url is required"}), 400
    try:
        result = setup_repo(repo_url)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
