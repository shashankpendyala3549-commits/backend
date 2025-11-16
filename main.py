import os
from pathlib import Path

from flask import Flask, request, jsonify, make_response
from flask_cors import CORS

from onboardmate_lib import (
    verify_offer,
    compute_offer_hash,
    record_scam_report,
    get_scam_report_stats,
)

# Workspace directory (used for scam-reports store if needed)
BASE_DIR = "offershield_workspace"
WORKSPACE_DIR = Path(BASE_DIR)
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

# Global CORS for hackathon / frontend usage
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
    return "OfferShield backend is running", 200


# ------------------------------------------------------------------
#  /verify  (OPTIONS + POST)
#  Main endpoint: hybrid checks + OpenAI reasoning
# ------------------------------------------------------------------
@app.route("/verify", methods=["POST", "OPTIONS"])
def verify():
    if request.method == "OPTIONS":
        return make_response("", 200)

    data = request.get_json(force=True, silent=True) or {}

    # Expected keys (frontend can send any subset; we handle missing safely)
    # {
    #   "company_name": str,
    #   "hr_email": str,
    #   "raw_text": str,              # full offer letter text
    #   "salary_amount": float|str,
    #   "salary_currency": "INR"/"USD"/...,
    #   "salary_period": "month"/"year",
    #   "job_role": str,
    #   "links": [str],
    #   "contact_numbers": [str],
    #   "interview": {
    #       "had_interview": bool,
    #       "channel": "zoom"/"whatsapp"/"phone"/...,
    #       "duration_minutes": int,
    #       "asked_technical": bool
    #   }
    # }

    raw_text = (data.get("raw_text") or "").strip()
    if not raw_text:
        return jsonify({"error": "raw_text (offer letter text) is required"}), 400

    try:
        analysis = verify_offer(data)

        # Attach hash + scam-report stats for UI
        offer_hash = compute_offer_hash(raw_text)
        report_stats = get_scam_report_stats(offer_hash)

        return jsonify(
            {
                "offer_hash": offer_hash,
                "scam_reports": report_stats,
                "analysis": analysis,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------
#  Legacy alias: /setup -> /verify
#  (So old frontend that still calls /setup will keep working)
# ------------------------------------------------------------------
@app.route("/setup", methods=["POST", "OPTIONS"])
def setup_legacy():
    if request.method == "OPTIONS":
        return make_response("", 200)

    data = request.get_json(force=True, silent=True) or {}

    # Accept both { raw_text: ... } and { offer_text: ... } etc.
    if "raw_text" not in data and "offer_text" in data:
        data["raw_text"] = data["offer_text"]

    return verify()


# ------------------------------------------------------------------
#  /report-scam  (OPTIONS + POST)
#  Users click "Report Fake Offer" in UI → store hash
# ------------------------------------------------------------------
@app.route("/report-scam", methods=["POST", "OPTIONS"])
def report_scam():
    if request.method == "OPTIONS":
        return make_response("", 200)

    data = request.get_json(force=True, silent=True) or {}
    raw_text = (data.get("raw_text") or "").strip()
    offer_hash = data.get("offer_hash")

    if not raw_text and not offer_hash:
        return jsonify({"error": "raw_text or offer_hash is required"}), 400

    if not offer_hash:
        offer_hash = compute_offer_hash(raw_text)

    try:
        stats = record_scam_report(offer_hash)
        return jsonify(
            {
                "offer_hash": offer_hash,
                "message": "Scam report recorded.",
                "scam_reports": stats,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------
#  /status/<offer_hash> (GET)
#  Simple endpoint to fetch scam-report stats for a given hash
# ------------------------------------------------------------------
@app.route("/status/<offer_hash>", methods=["GET", "OPTIONS"])
def status(offer_hash):
    if request.method == "OPTIONS":
        return make_response("", 200)

    try:
        stats = get_scam_report_stats(offer_hash)
        return jsonify(stats)
    except Exception:
        return jsonify({"status": "unknown"}), 404


# Optional: keep /start as a harmless stub so old frontend doesn't break
@app.route("/start", methods=["POST", "OPTIONS"])
def start_stub():
    if request.method == "OPTIONS":
        return make_response("", 200)
    return jsonify(
        {
            "message": (
                "OfferShield does not start background processes. "
                "Use /verify to check offers and /report-scam to flag scams."
            )
        }
    )


if __name__ == "__main__":
    # For local dev; Render will use: gunicorn main:app
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
