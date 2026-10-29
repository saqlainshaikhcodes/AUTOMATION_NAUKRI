"""
Saqlain ka profile + "question -> answer" engine.

Pure Python (browser nahi chahiye), isliye alag se test ho sakta hai:
    python candidate.py      # self-test chalata hai

Naukri chatbot / questionnaire / company-site form me jo bhi sawaal aaye,
answer(question, options=..., maxlen=..., ctx=...) usko profile ke hisaab se jawab deta hai.
None return hua = answer nahi pata (caller use unknown_questions.json me log karta hai).
"""
import os
import re
from typing import Optional

# ---------------------------------------------------------------------------
# PROFILE DATA  (private repo me hi rakho - isme phone/email hai)
# ---------------------------------------------------------------------------
FIRST, MIDDLE, LAST = "Saqlain", "Mohammad", "Shaikh"
EMAIL = os.getenv("PROFILE_EMAIL", "saqlainshaikh578@gmail.com")
PHONE = "8669390062"
LINKEDIN = "https://www.linkedin.com/in/shaikh-saqlain-1b01a0248/"
DOB = "03/02/2002"  # dd/mm/yyyy
CITY, STATE = "Thane", "Maharashtra"
GENDER = os.getenv("GENDER", "").strip() or None  # env me set karoge tabhi use hoga (guess nahi karta)

TOTAL_EXP = 5
CURRENT_CTC_LPA = 6.6
EXPECTED_CTC_LPA = float(os.getenv("EXPECTED_CTC_LPA", "9.5"))  # tumne 9-10 bola tha
LWD = "31/08/2026"

# Skill -> years (sirf wahi jo resume/details me hai; baaki unknown_skill_years se)
SKILL_YEARS = {
    "react native": 5, "android": 5, "ios": 5, "mobile": 5, "mobile app": 5,
    "expo": 3, "react": 3.5, "next": 2, "redux": 4, "javascript": 5, "typescript": 4,
    "node": 3.5, "express": 3.5, "mongodb": 3, "mysql": 3, "postgresql": 2, "sql": 3,
    "rest api": 5, "api": 5, "firebase": 4, "aws": 2, "git": 5, "html": 5, "css": 5,
    "stripe": 2, "razorpay": 2, "paypal": 2, "payment": 2, "socket": 2, "websocket": 2,
    "webrtc": 1, "ci/cd": 2, "fastlane": 2, "github actions": 2, "jest": 2,
    "full stack": 4, "fullstack": 4, "web": 3.5, "frontend": 4, "front end": 4,
    "backend": 3.5, "back end": 3.5, "agile": 5, "scrum": 5,
}
# Resume me jo skill nahi hai uske liye jawab (honest default 0). Env se badal sakte ho.
UNKNOWN_SKILL_YEARS = os.getenv("UNKNOWN_SKILL_YEARS", "0")

SHORT_BIO = (
    "Senior Full Stack Developer, 5+ years in React Native (Android, iOS, Expo) and Node.js. "
    "Shipped 10+ production apps with payments, KYC, real-time chat and CI/CD."
)
REASON_CHANGE = (
    "My previous engagement ended as the company faced financial constraints and reduced project "
    "scope. I'm looking for a stable role with bigger technical challenges and growth."
)
WHY_HIRE = (
    "5+ years in React Native with Node.js backend experience. I owned a live app (Oreya) end to end, "
    "from architecture to store release, with payments, KYC and real-time chat. Available immediately."
)
WHY_JOIN = (
    "I'm looking to apply my 5+ years of React Native and Node.js experience to high-impact products at "
    "scale. I'm drawn to {company} for its product and engineering challenges, and I'd bring an "
    "ownership mindset, release management experience and full-stack skills."
)
STRENGTHS = (
    "Product ownership, quick debugging and root cause analysis, payment and KYC integrations, "
    "release management, and working independently while coordinating well with backend teams."
)
CHALLENGE = (
    "Navigation state loss in Expo Router during app background/kill. I debugged the root cause and "
    "restructured state management so user data persisted correctly."
)
ACHIEVEMENT = (
    "Took Oreya from scratch to a live production app on both stores as the only mobile engineer, with "
    "3 payment gateways, 2 IAP platforms and 3 KYC providers, at 99.5% crash-free sessions."
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
_NORMALIZE = [
    (r"node\s*\.?\s*js", "node"), (r"react\s*\.?\s*js", "react"), (r"reactjs", "react"),
    (r"next\s*\.?\s*js", "next"), (r"react[\s-]*native", "react native"), (r"\bmern\b", "react node mongodb"),
    (r"post?gres(ql)?", "postgresql"), (r"my\s*sql", "mysql"), (r"\bci\s*/\s*cd\b", "ci/cd"),
    (r"rest\s*(ful)?\s*apis?", "rest api"), (r"full[\s-]*stack", "full stack"),
    (r"front[\s-]*end", "frontend"), (r"back[\s-]*end", "backend"), (r"\bapple ios\b", "ios"),
]


def norm(q: str) -> str:
    q = (q or "").lower().replace("’", "'")
    q = re.sub(r"\s+", " ", q).strip()
    for pat, rep in _NORMALIZE:
        q = re.sub(pat, rep, q)
    return q


def _fmt_years(y: float) -> str:
    return str(int(y)) if float(y).is_integer() else str(y)


def skill_years(q: str) -> Optional[str]:
    """Question me jo skill mention hai uske years. Skill nahi mili -> None."""
    for key in sorted(SKILL_YEARS, key=len, reverse=True):
        if re.search(r"(?<![a-z])" + re.escape(key) + r"(?![a-z])", q):
            return _fmt_years(SKILL_YEARS[key])
    return None


def _is_experience_q(q: str) -> bool:
    return bool(re.search(r"\b(years?|yrs?|experience|exp)\b", q))


def _money(q: str, lpa: float) -> str:
    if re.search(r"\b(lakhs?|lpa|lacs?|lac)\b", q):
        return _fmt_years(round(lpa, 2))
    if re.search(r"month", q):
        return str(int(round(lpa * 100000 / 12, -2)))
    return str(int(round(lpa * 100000)))


# Option text me ye aliases bhi try hote hain (answer -> possible option texts)
ALIASES = {
    "immediate": ["immediate", "15 days or less", "less than 15", "0-15", "0 to 15", "within 15", "1 week",
                  "7 days", "15 days", "serving notice", "0 days"],
    "yes": ["yes", "y", "true", "i agree", "agree"],
    "no": ["no", "n", "false"],
    "master": ["master", "post graduate", "postgraduate", "m.sc", "msc", "pg"],
    "thane": ["thane", "mumbai", "navi mumbai", "mumbai (all areas)"],
    "mumbai": ["mumbai", "mumbai (all areas)", "navi mumbai", "thane"],
}


def _range_hit(value: float, text: str) -> bool:
    t = text.lower().replace(",", "")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)]
    if not nums:
        return False
    if re.search(r"(less than|below|under|<|upto|up to|max)", t) and len(nums) == 1:
        return value < nums[0] or value <= nums[0] and "up" in t
    if re.search(r"(\+|more than|above|over|>|plus|and above|or more|min)", t) and len(nums) == 1:
        return value >= nums[0]
    if len(nums) >= 2:
        return nums[0] <= value <= nums[1]
    return abs(value - nums[0]) < 1e-9


def pick_option(ans: str, options: list) -> Optional[str]:
    """Options me se wo option do jo ans se sabse achha match kare. Na mile to None."""
    if not options:
        return None
    opts = [(o, re.sub(r"\s+", " ", o).strip().lower()) for o in options if o and o.strip()]
    a = ans.strip().lower()
    for o, ol in opts:  # exact
        if ol == a:
            return o
    try:  # numeric -> range wale options (3-5 years, 5+ ...)
        val = float(a)
        hits = [o for o, ol in opts if _range_hit(val, ol)]
        if hits:
            # sabse tight (chhoti range) pehle
            def width(o):
                n = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", o.replace(",", ""))]
                return (n[1] - n[0]) if len(n) >= 2 else 999
            return sorted(hits, key=width)[0]
        for o, ol in opts:
            if ol in (a, a + ".0") or re.fullmatch(rf"{re.escape(a)}\s*(years?|yrs?|lpa)?", ol):
                return o
    except ValueError:
        pass
    for alias in ALIASES.get(a, []):  # alias (exact phir contains)
        for o, ol in opts:
            if ol == alias:
                return o
        for o, ol in opts:
            if alias in ol and len(alias) > 2:
                return o
    for o, ol in opts:  # contains
        if a in ol or (len(ol) > 2 and ol in a):
            return o
    return None


# ---------------------------------------------------------------------------
# RULES  (order matters: specific pehle)
# ---------------------------------------------------------------------------
NEGATIVE = re.compile(
    r"serving (your )?notice|currently serving|offer in hand|other offers?|holding (any )?offers?|"
    r"already applied|applied (before|previously)|previously (worked|employed|interviewed)|"
    r"worked (with|for) us|former employee|relative|terminated|convicted|criminal|"
    r"sponsorship|require.*visa|non[- ]?compete|currently employed|are you employed|gap in"
)
AUX_YESNO = re.compile(
    r"^(are|do|did|can|could|have|has|will|would|is|may|should)\b|willing|comfortable|open to|ready to|"
    r"ok with|okay with|agree|confirm|consent|acknowledge"
)


def _text(q: str, ctx: dict, maxlen: Optional[int], full: str, short: str) -> str:
    company = (ctx or {}).get("company") or "your company"
    full, short = full.format(company=company), short.format(company=company)
    if maxlen and len(full) > maxlen:
        if len(short) <= maxlen:
            return short
        cut = short[:maxlen]
        stop = max(cut.rfind(". "), cut.rfind("."))
        if stop > maxlen * 0.5:
            return cut[: stop + 1]
        return cut[: maxlen - 1].rsplit(" ", 1)[0].rstrip(",;:") + "."
    return full


def answer(question: str, options: Optional[list] = None, maxlen: Optional[int] = None,
           ctx: Optional[dict] = None) -> Optional[str]:
    """Question ka jawab (string). options di ho to jawab options me se hi aata hai (exact option text).
    None => pata nahi."""
    q = norm(question)
    if not q:
        return None
    ctx = ctx or {}
    raw = _rules(q, ctx, maxlen)
    if raw is None:
        raw = _fallback(q, options)
    if raw is None:
        return None
    if options:
        return pick_option(raw, options)  # match nahi mila to None (caller log karega)
    return raw


def _fallback(q: str, options: Optional[list]) -> Optional[str]:
    low = {re.sub(r"\s+", " ", o).strip().lower() for o in (options or [])}
    yn = {"yes", "no"} <= low
    if yn or (not options and AUX_YESNO.search(q)):
        return "No" if NEGATIVE.search(q) else "Yes"
    return None


def _rules(q: str, ctx: dict, maxlen: Optional[int]) -> Optional[str]:  # noqa: C901 (rules ki list hai)
    has = lambda p: re.search(p, q) is not None  # noqa: E731

    # --- notice / joining ---
    if has(r"notice period.*negotiable|negotiable.*notice"):
        return "Yes"
    if has(r"serving (your )?notice|currently serving"):
        return "No"
    if has(r"last working day|\blwd\b|last date of (work|employment)"):
        return LWD
    if has(r"notice period|how soon|when can you (join|start)|joining (time|period|date)|time to join|"
           r"availability to join|earliest.*join|join.*within|immediate(ly)? (join|available)|available to join"):
        if has(r"(can|will|able to|willing).*(join|start)") and has(r"within|immediate") and not has(r"how soon|when"):
            return "Yes"
        return "Immediate"

    # --- salary ---
    if has(r"negotiable") and has(r"ctc|salary|compensation|package"):
        return "Yes"
    if has(r"expect\w*.*(ctc|salary|compensation|package|pay)|(ctc|salary|package|compensation).*expect"):
        return _money(q, EXPECTED_CTC_LPA)
    if has(r"(current|present|last|existing|drawn|fixed).*(ctc|salary|compensation|package|pay)|"
           r"(ctc|salary).*(current|present)"):
        return _money(q, CURRENT_CTC_LPA)
    if has(r"\b(ctc|salary|package|compensation)\b"):
        return _money(q, EXPECTED_CTC_LPA)

    # --- experience ---
    if _is_experience_q(q) and has(r"month") and has(r"experience"):
        sy = skill_years(q)
        return str(int(float(sy or TOTAL_EXP) * 12))
    if _is_experience_q(q) and has(r"\b(experience|exp)\b|how many years|years? of"):
        if has(r"total|overall|relevant|professional|work experience|industry") and not skill_years(q):
            return str(TOTAL_EXP)
        sy = skill_years(q)
        if sy is not None:
            return sy
        if has(r"how many years|years? of experience (do you have|in)|experience in|experience with|worked (with|on)"):
            # koi aur skill (Java, PHP, ...) jo resume me nahi hai
            if has(r"total|overall|relevant|professional|work experience|industry|your experience$|^experience$"):
                return str(TOTAL_EXP)
            return UNKNOWN_SKILL_YEARS
        return str(TOTAL_EXP)

    # --- location / work mode ---
    if has(r"relocat"):
        return "Yes"
    if has(r"current(ly)? (city|location|residing|based|located)|present (city|location)|"
           r"where (are you|do you) (currently )?(located|based|live|reside)|city of residence|your (city|location)$"):
        return CITY
    if has(r"preferred (work )?(city|location)|(city|location) preference|prefer.*location|location.*prefer"):
        return "Mumbai"
    if has(r"\bstate\b") and not has(r"statement"):
        return STATE
    if has(r"\bcountry\b"):
        return "India"
    if has(r"work from (office|home)|\bwfo\b|\bwfh\b|on-?site|hybrid|remote|commute|travel|shift|work (5|six|6) days"):
        return "Yes"

    # --- identity / contact ---
    if has(r"first name|given name"):
        return FIRST
    if has(r"middle name"):
        return MIDDLE
    if has(r"last name|sur ?name|family name"):
        return LAST
    if has(r"^(your |candidate |applicant )?(full )?name\b|\bfull name\b"):
        return f"{FIRST} {LAST}"
    if has(r"e-?mail"):
        return EMAIL
    if has(r"phone|mobile|contact (no|number)|whats ?app|telephone"):
        return PHONE
    if has(r"linkedin"):
        return LINKEDIN
    if has(r"portfolio|github|website|personal (site|url)|profile (url|link)"):
        return LINKEDIN
    if has(r"date of birth|\bdob\b|birth ?date"):
        return DOB
    if has(r"\bgender\b|\bsex\b"):
        return GENDER

    # --- work authorisation ---
    if has(r"sponsor|require.*visa|visa (status|required)"):
        return "No"
    if has(r"authori[sz]ed to work|legally (eligible|authori[sz]ed)|work permit|eligible to work|right to work"):
        return "Yes"

    # --- education ---
    if has(r"highest (qualification|education|degree)|education(al)? (level|qualification)|qualification"):
        return "master"
    if has(r"(year|yr) of (passing|graduation|completion)|passing year|graduation year"):
        return "2025"
    if has(r"university|college|institute"):
        return "University of Mumbai"
    if has(r"\b(specialization|specialisation|branch|stream|course|field of study)\b"):
        return "Information Technology"
    if has(r"\bdegree\b"):
        return "M.Sc. IT"

    # --- employment status ---
    if has(r"current(ly)? (company|employer|organi[sz]ation)|present (company|employer)|last (company|employer)"):
        return "Oudience LLC"
    if has(r"current(ly)? (designation|role|job title)|last (designation|role)|job title|designation"):
        return "Senior Full Stack Developer"
    if has(r"currently (employed|working)|are you employed"):
        return "No"

    # --- long text ---
    if has(r"reason (for|of) (job )?(change|leaving|switch)|why (are you|do you want to) (leav|chang|switch)|why.*job change"):
        return _text(q, ctx, maxlen, REASON_CHANGE, REASON_CHANGE)
    if has(r"why (should|do) we hire|why.*hire you|what (can|will) you (bring|contribute)|why.*suitable|why.*fit"):
        return _text(q, ctx, maxlen, WHY_HIRE, WHY_HIRE)
    if has(r"why (do you want to )?(join|work|apply|interested)|interested in (this|the|our)|why us|why this (role|company)"):
        return _text(q, ctx, maxlen, WHY_JOIN, WHY_JOIN)
    if has(r"strength"):
        return _text(q, ctx, maxlen, STRENGTHS, STRENGTHS)
    if has(r"challenge|difficult|problem you (solved|faced)"):
        return _text(q, ctx, maxlen, CHALLENGE, CHALLENGE)
    if has(r"achievement|accomplishment|proud"):
        return _text(q, ctx, maxlen, ACHIEVEMENT, ACHIEVEMENT)
    if has(r"about (yourself|you)|introduce|summary|cover letter|bio\b|describe yourself|additional (info|comments?)|anything else"):
        return _text(q, ctx, maxlen, SHORT_BIO, SHORT_BIO)

    # --- misc ---
    if has(r"how did you (hear|find|come)|\bsource\b|referr?ed"):
        return "Naukri"
    if has(r"immediate(ly)? (join|joiner|available)|join immediately|can you join"):
        return "Yes"
    return None


# ---------------------------------------------------------------------------
if __name__ == "__main__":  # self-test
    T = [
        ("What is your notice period?", ["Immediate", "15 Days or less", "30 Days", "60 Days"], "Immediate"),
        ("Notice period", ["15 Days or less", "1 Month", "2 Months", "3 Months"], "15 Days or less"),
        ("What is your current CTC (in lakhs)?", None, "6.6"),
        ("Expected CTC in INR annual", None, "950000"),
        ("Expected salary (LPA)", None, "9.5"),
        ("Total experience in years", None, "5"),
        ("How many years of experience do you have in React Native?", None, "5"),
        ("Experience in Node.js", ["0-1", "1-3", "3-5", "5+"], "3-5"),
        ("Years of experience with Expo?", None, "3"),
        ("How many years of experience in Java?", None, "0"),
        ("Are you willing to relocate to Mumbai?", ["Yes", "No"], "Yes"),
        ("Are you currently serving notice period?", ["Yes", "No"], "No"),
        ("Do you have any offer in hand?", ["Yes", "No"], "No"),
        ("Do you require visa sponsorship?", ["Yes", "No"], "No"),
        ("Are you comfortable working from office (Mumbai)?", None, "Yes"),
        ("Current location", None, "Thane"),
        ("Highest qualification", ["Bachelor's", "Master's / Post graduation", "Doctorate"], "Master's / Post graduation"),
        ("Why should we hire you?", None, WHY_HIRE),
        ("Why do you want to join Acme?", None, None),  # company ctx test below
        ("What is your date of birth?", None, "03/02/2002"),
        ("Gender", None, None),
        ("Can you join immediately?", ["Yes", "No"], "Yes"),
        ("Experience in ReactJS", None, "3.5"),
        ("Total experience in months", None, "60"),
    ]
    bad = 0
    for q, opts, exp in T:
        got = answer(q, opts, ctx={"company": "Acme"})
        if exp is None:
            ok = True
        else:
            ok = got == exp
        bad += not ok
        print(("OK  " if ok else "FAIL"), q, "->", (got or "")[:70], "" if ok else f"(expected {exp})")
    short = answer("Why should we hire you?", maxlen=250)
    print("maxlen test:", len(short), "<= 250" if len(short) <= 250 else "TOO LONG")
    bad += len(short) > 250
    print("\nFAILED:", bad)
    raise SystemExit(1 if bad else 0)
