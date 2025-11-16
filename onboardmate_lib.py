import hashlib
import json
import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


# -----------------------------------------------------------------------------
# Paths / storage
# -----------------------------------------------------------------------------
BASE_DIR = "offershield_workspace"
WORKSPACE_DIR = Path(BASE_DIR)
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

SCAM_REPORTS_FILE = WORKSPACE_DIR / "scam_reports.json"


# -----------------------------------------------------------------------------
# Static data: HR signatures, salary ranges, risky phrases, etc.
# -----------------------------------------------------------------------------
OFFICIAL_HR_EMAILS: Dict[str, List[str]] = {
    # All lowercase for matching
    "google": ["no-reply@google.com", "careers@google.com"],
    "amazon": ["no-reply@amazon.com", "campus-hire@amazon.com"],
    "microsoft": ["microsoft@e-mail.microsoft.com", "msrecruit@microsoft.com"],
    "accenture": ["careers@accenture.com", "recruitment@accenture.com"],
    "deloitte": ["recruiting@deloitte.com", "campusrecruiting@deloitte.com"],
    "infosys": ["hr@infosys.com", "recruitment@infosys.com"],
    "tcs": ["hr@tcs.com", "recruitment@tcs.com"],
    "jpmorgan": ["recruiting@jpmorgan.com"],
    "meta": ["no-reply@fb.com", "recruiting@fb.com"],
}

FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "rediffmail.com",
    "proton.me",
    "icloud.com",
}

RISKY_LANGUAGE_PATTERNS = [
    r"processing fee",
    r"registration fee",
    r"registration amount",
    r"training fee",
    r"refundable fee",
    r"urgent payment",
    r"pay.*before joining",
    r"no interview required",
    r"without any interview",
    r"confirm within 24 hours",
    r"confirm in 24 hours",
    r"do not share this offer",
    r"don[’']t share this offer",
    r"whatsapp .* joining",
    r"whatsapp only for communication",
    r"certificate fee",
    r"security deposit",
    r"slot will be given to next candidate",
]

WHATSAPP_TELEGRAM_PATTERNS = [
    r"whatsapp",
    r"wa\.me/",
    r"\+?\d{10,13}.*whatsapp",
    r"telegram",
    r"t\.me/",
    r"signal",
]

SUSPICIOUS_LINK_TLDS = {
    ".xyz",
    ".top",
    ".info",
    ".pw",
    ".click",
    ".club",
    ".icu",
}

URL_SHORTENERS = {
    "bit.ly",
    "tinyurl.com",
    "is.gd",
    "t.co",
    "cutt.ly",
    "rb.gy",
}

SALARY_BENCHMARKS = {
    # Very rough, just for hackathon demo
    "india_fresher_software_engineer_month": (20000, 90000),
    "india_fresher_data_analyst_month": (20000, 80000),
    "india_intern_month": (0, 35000),
    "us_fresher_software_engineer_year": (60000, 200000),
    "us_intern_month": (2000, 10000),
}


SECTION_KEYWORDS = {
    "offer_id": ["offer id", "reference no", "ref no"],
    "address": ["registered office", "corporate office", "address"],
    "joining_date": ["date of joining", "doj", "joining date"],
    "manager": ["reporting manager", "reports to", "supervisor"],
    "role": ["designation", "position", "job title", "role"],
    "ctc": ["ctc", "compensation", "salary structure", "remuneration"],
    "tnc": ["terms and conditions", "terms & conditions", "termination", "bond period"],
    "contact": ["hr contact", "contact number", "reach out to", "email us at"],
    "company_ids": ["gst", "pan", "cin"],
}


# -----------------------------------------------------------------------------
# Utility: Offer hash (used for scam aggregation)
# -----------------------------------------------------------------------------
def compute_offer_hash(raw_text: str) -> str:
    normalized = " ".join(raw_text.split()).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# -----------------------------------------------------------------------------
# Scam reports storage (simple JSON file)
# -----------------------------------------------------------------------------
def _load_scam_reports() -> Dict[str, int]:
    if not SCAM_REPORTS_FILE.exists():
        return {}
    try:
        data = json.loads(SCAM_REPORTS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items()}
        return {}
    except Exception:
        return {}


def _save_scam_reports(data: Dict[str, int]) -> None:
    try:
        SCAM_REPORTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        # Safe fail; nothing critical
        pass


def record_scam_report(offer_hash: str) -> Dict[str, Any]:
    data = _load_scam_reports()
    data[offer_hash] = data.get(offer_hash, 0) + 1
    _save_scam_reports(data)
    return get_scam_report_stats(offer_hash)


def get_scam_report_stats(offer_hash: str) -> Dict[str, Any]:
    data = _load_scam_reports()
    count = data.get(offer_hash, 0)
    return {
        "reports_count": count,
        "status": "reported_scam" if count > 0 else "not_reported",
    }


# -----------------------------------------------------------------------------
# OpenAI helper
# -----------------------------------------------------------------------------
def _call_llm(user_prompt: str, max_tokens: int = 400) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or OpenAI is None:
        return ""

    try:
        client = OpenAI(api_key=api_key)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are OfferShield, an AI that evaluates job offer "
                        "letters for scam risk, quality, and plausibility. "
                        "Return clear, concise analysis."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
        )
        return (completion.choices[0].message.content or "").strip()
    except Exception:
        return ""


# -----------------------------------------------------------------------------
# Feature 1: Company Authenticity Scanner
# -----------------------------------------------------------------------------
@dataclass
class CompanyAuthResult:
    score: int
    flags: List[str]
    domain: Optional[str]
    used_free_email: bool
    domain_reachable: bool
    https_ok: bool


def _extract_domain_from_email(email: str) -> Optional[str]:
    email = (email or "").strip().lower()
    if "@" not in email:
        return None
    return email.split("@", 1)[1]


def _check_domain_http(domain: str) -> Tuple[bool, bool]:
    """Returns (http_ok, https_ok)."""
    http_ok = False
    https_ok = False

    try:
        # Just resolving DNS gives some signal of existence
        socket.gethostbyname(domain)
    except Exception:
        return False, False

    try:
        r = requests.get(f"http://{domain}", timeout=3)
        http_ok = r.status_code < 500
    except Exception:
        http_ok = False

    try:
        r = requests.get(f"https://{domain}", timeout=3, verify=True)
        https_ok = r.status_code < 500
    except Exception:
        https_ok = False

    return http_ok, https_ok


def company_authenticity_check(company_name: str, hr_email: str) -> CompanyAuthResult:
    flags: List[str] = []
    score = 100

    company_name_norm = (company_name or "").strip().lower()
    domain = _extract_domain_from_email(hr_email)
    used_free_email = False
    domain_reachable = False
    https_ok = False

    if not domain:
        flags.append("No valid HR email domain found.")
        score -= 25
        return CompanyAuthResult(
            score=max(score, 0),
            flags=flags,
            domain=None,
            used_free_email=False,
            domain_reachable=False,
            https_ok=False,
        )

    if domain in FREE_EMAIL_DOMAINS:
        used_free_email = True
        flags.append(f"HR email uses free provider ({domain}).")
        score -= 35

    official_emails = OFFICIAL_HR_EMAILS.get(company_name_norm, [])
    if official_emails:
        # If company is in list, but email doesn't match any known official address
        if hr_email.strip().lower() not in [e.lower() for e in official_emails]:
            flags.append(
                "HR email does not match known official addresses for this company."
            )
            score -= 25

    # Domain reachable & SSL
    http_ok, https_ok = _check_domain_http(domain)
    domain_reachable = http_ok or https_ok

    if not domain_reachable:
        flags.append("Company email domain does not appear to be reachable.")
        score -= 20
    if http_ok and not https_ok:
        flags.append("Domain only responds over HTTP (no HTTPS).")
        score -= 10

    return CompanyAuthResult(
        score=max(min(score, 100), 0),
        flags=flags,
        domain=domain,
        used_free_email=used_free_email,
        domain_reachable=domain_reachable,
        https_ok=https_ok,
    )


# -----------------------------------------------------------------------------
# Feature 3 & 10: Language risk + WhatsApp/Telegram risk
# -----------------------------------------------------------------------------
@dataclass
class LanguageRiskResult:
    score: int
    risk_phrases: List[str]
    whatsapp_telegram_mentions: List[str]


def language_risk_check(raw_text: str) -> LanguageRiskResult:
    text = raw_text.lower()
    risk_hits: List[str] = []
    for pattern in RISKY_LANGUAGE_PATTERNS:
        if re.search(pattern, text):
            risk_hits.append(pattern)

    wt_hits: List[str] = []
    for pattern in WHATSAPP_TELEGRAM_PATTERNS:
        if re.search(pattern, text):
            wt_hits.append(pattern)

    # Base: 100 (safe) → down with hits
    score = 100
    score -= min(70, 10 * len(risk_hits))
    score -= min(20, 5 * len(wt_hits))

    return LanguageRiskResult(
        score=max(score, 0),
        risk_phrases=risk_hits,
        whatsapp_telegram_mentions=wt_hits,
    )


# -----------------------------------------------------------------------------
# Feature 5: Compensation reality check
# -----------------------------------------------------------------------------
@dataclass
class SalaryCheckResult:
    score: int
    flags: List[str]
    parsed_amount: Optional[float]


def _parse_salary_amount(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw)
    # remove currency symbols and commas
    s = re.sub(r"[₹$,]", "", s)
    m = re.search(r"\d+(\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def salary_plausibility_check(
    salary_amount: Any,
    salary_currency: str,
    salary_period: str,
    job_role: str,
    region_hint: str = "india",
) -> SalaryCheckResult:
    amount = _parse_salary_amount(salary_amount)
    flags: List[str] = []
    score = 100

    if amount is None:
        flags.append("Could not parse salary amount; skipping salary realism checks.")
        return SalaryCheckResult(score=score, flags=flags, parsed_amount=None)

    salary_currency = (salary_currency or "").upper()
    salary_period = (salary_period or "").lower()
    role_lower = (job_role or "").lower()

    # Choose key
    key = None
    if "intern" in role_lower:
        key = f"{region_hint}_intern_{salary_period}"
    elif "data" in role_lower and "analyst" in role_lower:
        key = f"{region_hint}_fresher_data_analyst_{salary_period}"
    else:
        key = f"{region_hint}_fresher_software_engineer_{salary_period}"

    bench = SALARY_BENCHMARKS.get(key)
    if not bench:
        flags.append(
            f"No salary benchmark defined for key '{key}'. Using neutral score."
        )
        return SalaryCheckResult(score=score, flags=flags, parsed_amount=amount)

    low, high = bench

    if amount < low * 0.3:
        flags.append(
            f"Offered salary ({amount}) is extremely low compared to typical range {low}-{high}."
        )
        score -= 30
    elif amount > high * 2:
        flags.append(
            f"Offered salary ({amount}) is extremely high compared to typical range {low}-{high}."
        )
        score -= 40
    elif amount > high * 1.3:
        flags.append(
            f"Offered salary ({amount}) is higher than usual; verify with official HR."
        )
        score -= 15

    return SalaryCheckResult(score=max(score, 0), flags=flags, parsed_amount=amount)


# -----------------------------------------------------------------------------
# Feature 9: Link validation
# -----------------------------------------------------------------------------
@dataclass
class LinkRiskResult:
    score: int
    suspicious_links: List[str]
    short_links: List[str]


def extract_urls(raw_text: str) -> List[str]:
    pattern = r"(https?://[^\s]+)"
    return re.findall(pattern, raw_text)


def link_risk_check(urls: List[str]) -> LinkRiskResult:
    suspicious: List[str] = []
    short: List[str] = []
    score = 100

    for url in urls:
        try:
            host = re.sub(r"^https?://", "", url)
            host = host.split("/", 1)[0].lower()
        except Exception:
            continue

        # Shorteners
        if any(host.startswith(s) for s in URL_SHORTENERS):
            short.append(url)
            score -= 10

        # Suspicious TLDs
        for tld in SUSPICIOUS_LINK_TLDS:
            if host.endswith(tld):
                suspicious.append(url)
                score -= 15
                break

        # Look-alike domains (simple)
        if any(ch in host for ch in ["0", "1", "3", "@"]) and any(
            name in host for name in ["google", "amazon", "microsoft", "accenture"]
        ):
            suspicious.append(url)
            score -= 20

    return LinkRiskResult(
        score=max(score, 0),
        suspicious_links=suspicious,
        short_links=short,
    )


# -----------------------------------------------------------------------------
# Feature 11: Interview plausibility
# -----------------------------------------------------------------------------
@dataclass
class InterviewResult:
    score: int
    flags: List[str]


def interview_plausibility_check(info: Dict[str, Any]) -> InterviewResult:
    score = 100
    flags: List[str] = []

    had_interview = bool(info.get("had_interview"))
    channel = (info.get("channel") or "").lower()
    duration = info.get("duration_minutes")
    asked_technical = info.get("asked_technical")

    if not had_interview:
        flags.append("Candidate reports no interview occurred before offer.")
        score -= 35
    else:
        # Channel heuristics
        if "whatsapp" in channel or "telegram" in channel:
            flags.append("Interview was conducted via WhatsApp/Telegram.")
            score -= 30
        elif "phone" in channel or "call" in channel:
            score -= 10
            flags.append("Interview was only an audio call; verify carefully.")

    if duration is not None:
        try:
            d = int(duration)
            if d < 5:
                flags.append("Interview duration was less than 5 minutes.")
                score -= 20
            elif d < 15:
                flags.append("Interview duration was very short (< 15 minutes).")
                score -= 10
        except Exception:
            pass

    if asked_technical is False:
        flags.append("No technical questions were asked for a technical-looking role.")
        score -= 20

    return InterviewResult(score=max(score, 0), flags=flags)


# -----------------------------------------------------------------------------
# Feature 12: Offer structure validation
# -----------------------------------------------------------------------------
@dataclass
class StructureResult:
    score: int
    sections_found: Dict[str, bool]
    missing_sections: List[str]


def structure_validation_check(raw_text: str) -> StructureResult:
    text = raw_text.lower()
    sections_found: Dict[str, bool] = {}
    for key, keywords in SECTION_KEYWORDS.items():
        present = any(k in text for k in keywords)
        sections_found[key] = present

    required_keys = [
        "offer_id",
        "joining_date",
        "role",
        "ctc",
        "tnc",
        "contact",
    ]
    missing = [k for k in required_keys if not sections_found.get(k)]

    total_required = len(required_keys)
    present_count = total_required - len(missing)
    base_score = int((present_count / max(total_required, 1)) * 100)

    # Slightly penalize if company IDs/address missing
    if not sections_found.get("address"):
        base_score = max(base_score - 10, 0)
    if not sections_found.get("company_ids"):
        base_score = max(base_score - 5, 0)

    return StructureResult(
        score=base_score,
        sections_found=sections_found,
        missing_sections=missing,
    )


# -----------------------------------------------------------------------------
# Feature 2 + 13: Document integrity + explanation via LLM
# -----------------------------------------------------------------------------
@dataclass
class DocumentIntegrityResult:
    score: int
    summary: str


def document_integrity_and_explainability(
    raw_text: str, company_name: str, hr_email: str
) -> DocumentIntegrityResult:
    # Let LLM assign a 0–100 document quality score + explanation
    prompt = f"""
You are scoring a job offer letter for document integrity and professionalism.

Company: {company_name}
HR email: {hr_email}

Offer letter text:
------
{raw_text}
------

1. Give a "Document Quality Score" from 0 to 100 (higher is more polished and professional).
2. Briefly explain 4–8 bullet points about:
   - formatting clarity
   - presence/absence of key sections
   - language quality
   - any red flags about how the letter looks or reads (NOT about salary or domain).
3. At the end, write: "SCORE: <number>".

Keep it concise.
"""
    llm_output = _call_llm(prompt, max_tokens=350) or ""
    score = 60  # default

    m = re.search(r"SCORE:\s*(\d+)", llm_output)
    if m:
        try:
            score_val = int(m.group(1))
            score = max(0, min(100, score_val))
        except Exception:
            pass

    return DocumentIntegrityResult(score=score, summary=llm_output.strip())


# -----------------------------------------------------------------------------
# Feature 8: Role consistency validator (LLM)
# -----------------------------------------------------------------------------
@dataclass
class RoleConsistencyResult:
    score: int
    summary: str


def role_consistency_check(
    job_role: str, raw_text: str, interview_info: Dict[str, Any]
) -> RoleConsistencyResult:
    prompt = f"""
You are checking if a job offer letter and interview process are plausible for the stated role.

Role: {job_role}
Interview info (JSON):
{json.dumps(interview_info, indent=2)}

Offer letter text:
------
{raw_text}
------

1. Give a "Role & Process Plausibility Score" from 0 to 100.
2. Explain briefly why (3–6 bullet points).
3. At the end, write: "SCORE: <number>".

Focus on: whether the described process (interview / lack of it), responsibilities, and tone match typical hiring flows for such roles.
"""
    llm_output = _call_llm(prompt, max_tokens=350) or ""
    score = 60
    m = re.search(r"SCORE:\s*(\d+)", llm_output)
    if m:
        try:
            score_val = int(m.group(1))
            score = max(0, min(100, score_val))
        except Exception:
            pass

    return RoleConsistencyResult(score=score, summary=llm_output.strip())


# -----------------------------------------------------------------------------
# Feature 6: Company existence check (simple)
# -----------------------------------------------------------------------------
@dataclass
class CompanyExistenceResult:
    score: int
    flags: List[str]
    checked_domain: Optional[str]


def company_existence_check(company_name: str, hr_email: str) -> CompanyExistenceResult:
    flags: List[str] = []
    score = 100
    domain = _extract_domain_from_email(hr_email)

    if not domain:
        flags.append("No domain available to check company existence.")
        score -= 20
        return CompanyExistenceResult(
            score=max(score, 0), flags=flags, checked_domain=None
        )

    http_ok, https_ok = _check_domain_http(domain)
    if not (http_ok or https_ok):
        flags.append("Company domain did not respond over HTTP/HTTPS.")
        score -= 30

    return CompanyExistenceResult(
        score=max(score, 0),
        flags=flags,
        checked_domain=domain,
    )


# -----------------------------------------------------------------------------
# Explanation aggregator (Feature 13) + Final trust score (Feature 15)
# -----------------------------------------------------------------------------
def aggregate_final_score(
    company_auth: CompanyAuthResult,
    lang_risk: LanguageRiskResult,
    salary_check: SalaryCheckResult,
    link_risk: LinkRiskResult,
    interview_res: InterviewResult,
    structure_res: StructureResult,
    doc_integrity: DocumentIntegrityResult,
    role_consistency: RoleConsistencyResult,
    company_exist: CompanyExistenceResult,
    scam_reports: Dict[str, Any],
) -> Dict[str, Any]:
    # Convert risk-oriented scores to "trust" scores in 0–100
    trust_company = company_auth.score
    trust_language = lang_risk.score
    trust_salary = salary_check.score
    trust_links = link_risk.score
    trust_interview = interview_res.score
    trust_structure = structure_res.score
    trust_document = doc_integrity.score
    trust_role = role_consistency.score
    trust_company_exist = company_exist.score

    # Past scam reports: each report knocks trust
    reports_count = scam_reports.get("reports_count", 0)
    trust_scam = max(0, 100 - reports_count * 15)

    # Weighted average
    weights = {
        "company": 0.2,
        "language": 0.15,
        "salary": 0.1,
        "links": 0.05,
        "interview": 0.1,
        "structure": 0.1,
        "document": 0.1,
        "role": 0.1,
        "company_exist": 0.05,
        "scam": 0.05,
    }

    final_score = (
        trust_company * weights["company"]
        + trust_language * weights["language"]
        + trust_salary * weights["salary"]
        + trust_links * weights["links"]
        + trust_interview * weights["interview"]
        + trust_structure * weights["structure"]
        + trust_document * weights["document"]
        + trust_role * weights["role"]
        + trust_company_exist * weights["company_exist"]
        + trust_scam * weights["scam"]
    )

    final_score = int(round(final_score))

    if final_score >= 80:
        verdict = "Likely Genuine"
        color = "green"
    elif final_score >= 60:
        verdict = "Needs Verification"
        color = "yellow"
    else:
        verdict = "High Scam Risk"
        color = "red"

    # Explainability: collect reasons
    reasons: List[str] = []

    # Company auth reasons
    reasons.extend(company_auth.flags)
    reasons.extend(company_exist.flags)
    reasons.extend(salary_check.flags)
    reasons.extend(interview_res.flags)

    if lang_risk.risk_phrases:
        reasons.append(
            f"High-risk language patterns detected: {', '.join(lang_risk.risk_phrases)}"
        )
    if lang_risk.whatsapp_telegram_mentions:
        reasons.append(
            "Mentions of WhatsApp/Telegram/Signal in the offer text, which is unusual for formal offers."
        )
    if link_risk.suspicious_links:
        reasons.append(
            f"Suspicious links found: {', '.join(link_risk.suspicious_links)}"
        )
    if reports_count > 0:
        reasons.append(
            f"This letter hash has been reported as scam {reports_count} time(s) by other users."
        )
    if structure_res.missing_sections:
        reasons.append(
            f"Missing important sections: {', '.join(structure_res.missing_sections)}"
        )

    return {
        "score": final_score,
        "verdict": verdict,
        "verdict_color": color,
        "reasons": reasons[:12],  # cap for UI
    }


# -----------------------------------------------------------------------------
# MAIN ENTRY: verify_offer (used by Flask)
# -----------------------------------------------------------------------------
def verify_offer(payload: Dict[str, Any]) -> Dict[str, Any]:
    company_name = payload.get("company_name", "")
    hr_email = payload.get("hr_email", "")
    raw_text = payload.get("raw_text", "") or payload.get("offer_text", "")
    raw_text = str(raw_text)

    salary_amount = payload.get("salary_amount")
    salary_currency = payload.get("salary_currency", "INR")
    salary_period = payload.get("salary_period", "month")
    job_role = payload.get("job_role", "")
    region_hint = payload.get("region_hint", "india")

    interview_info = payload.get("interview") or {}
    urls = payload.get("links") or extract_urls(raw_text)

    # 1) Company authenticity
    company_auth = company_authenticity_check(company_name, hr_email)

    # 2 & 13) Document integrity + explanation
    doc_integrity = document_integrity_and_explainability(
        raw_text, company_name, hr_email
    )

    # 3 & 10) Language risk + WhatsApp / Telegram
    lang_risk = language_risk_check(raw_text)

    # 5) Compensation plausibility
    salary_check = salary_plausibility_check(
        salary_amount,
        salary_currency,
        salary_period,
        job_role,
        region_hint=region_hint,
    )

    # 6) Company existence
    company_exist = company_existence_check(company_name, hr_email)

    # 8) Role consistency
    role_consistency = role_consistency_check(job_role, raw_text, interview_info)

    # 9) Link validation
    link_risk = link_risk_check(urls)

    # 11) Interview plausibility
    interview_res = interview_plausibility_check(interview_info)

    # 12) Offer structure validation
    structure_res = structure_validation_check(raw_text)

    # Past scam reports will be injected by Flask (we only use placeholder here)
    # (Flask passes real stats separately into aggregate_final_score)
    dummy_scam_stats = {"reports_count": 0}

    # Aggregate final trust score (without real scam stats yet)
    final = aggregate_final_score(
        company_auth=company_auth,
        lang_risk=lang_risk,
        salary_check=salary_check,
        link_risk=link_risk,
        interview_res=interview_res,
        structure_res=structure_res,
        doc_integrity=doc_integrity,
        role_consistency=role_consistency,
        company_exist=company_exist,
        scam_reports=dummy_scam_stats,
    )

    # Full analysis payload (frontend can show detailed breakdown)
    return {
        "company_authenticity": {
            "score": company_auth.score,
            "domain": company_auth.domain,
            "used_free_email": company_auth.used_free_email,
            "domain_reachable": company_auth.domain_reachable,
            "https_ok": company_auth.https_ok,
            "flags": company_auth.flags,
        },
        "document_integrity": {
            "score": doc_integrity.score,
            "summary": doc_integrity.summary,
        },
        "language_risk": {
            "score": lang_risk.score,
            "risk_phrases": lang_risk.risk_phrases,
            "whatsapp_telegram_mentions": lang_risk.whatsapp_telegram_mentions,
        },
        "salary_plausibility": {
            "score": salary_check.score,
            "parsed_amount": salary_check.parsed_amount,
            "flags": salary_check.flags,
        },
        "company_existence": {
            "score": company_exist.score,
            "checked_domain": company_exist.checked_domain,
            "flags": company_exist.flags,
        },
        "link_risk": {
            "score": link_risk.score,
            "suspicious_links": link_risk.suspicious_links,
            "short_links": link_risk.short_links,
        },
        "interview_plausibility": {
            "score": interview_res.score,
            "flags": interview_res.flags,
        },
        "offer_structure": {
            "score": structure_res.score,
            "sections_found": structure_res.sections_found,
            "missing_sections": structure_res.missing_sections,
        },
        "role_consistency": {
            "score": role_consistency.score,
            "summary": role_consistency.summary,
        },
        "final_trust": final,
    }
