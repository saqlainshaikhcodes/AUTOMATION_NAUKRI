"""
Naukri auto-apply orchestrator.

main.py login + resume/headline refresh ke BAAD (RUN_APPLY=true par) run(page, context, resume) call karta hai.

Kya karta hai:
  * roles x locations ke search URLs (pehle Remote/Mumbai/Navi Mumbai/Thane/Kalyan-Dombivli, phir baaki cities random)
  * 4-6 yrs experience + role-title filter, duplicate/already-applied skip (applied.json)
  * 3 tarah ke apply handle: Easy Apply (chatbot), Apply+questionnaire, Apply on company site (5 retries)
  * limits: MAX_APPLIES, TIME_BUDGET_MIN, daily-limit/captcha par turant stop, lagatar fail par stop

Env (sab optional):
  MAX_APPLIES=40  TIME_BUDGET_MIN=22  MAX_FAILS_IN_ROW=8  APPLY_DELAY_MIN=6  APPLY_DELAY_MAX=14
  EXP_MIN=4  EXP_MAX=6  JOB_AGE_DAYS=7  PAGES_PER_QUERY=2  MAX_PER_QUERY=12
  ALLOW_EXTERNAL=true  EXTERNAL_RETRIES=5  DRY_RUN=false  COMPANY_BLACKLIST=comma,separated
  APPLIED_FILE=applied.json  UNKNOWN_FILE=unknown_questions.json  REPORT_FILE=report.md
"""
import os
import re
import json
import time
import random
import logging
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote

from playwright.sync_api import Page, BrowserContext

import form_filler as F

log = logging.getLogger("naukri.apply")


def _flag(name, default):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_APPLIES = _int("MAX_APPLIES", 40)
TIME_BUDGET = _int("TIME_BUDGET_MIN", 22) * 60
MAX_FAILS_IN_ROW = _int("MAX_FAILS_IN_ROW", 8)
DELAY = (_int("APPLY_DELAY_MIN", 6), _int("APPLY_DELAY_MAX", 14))
EXP_MIN, EXP_MAX = _int("EXP_MIN", 4), _int("EXP_MAX", 6)
JOB_AGE = _int("JOB_AGE_DAYS", 7)
PAGES = _int("PAGES_PER_QUERY", 2)
MAX_PER_QUERY = _int("MAX_PER_QUERY", 12)
ALLOW_EXTERNAL = _flag("ALLOW_EXTERNAL", "true")
EXT_RETRIES = _int("EXTERNAL_RETRIES", 5)
DRY_RUN = _flag("DRY_RUN", "false")
BLACKLIST = [c.strip().lower() for c in os.getenv("COMPANY_BLACKLIST", "").split(",") if c.strip()]
APPLIED_FILE = Path(os.getenv("APPLIED_FILE", "applied.json"))
UNKNOWN_FILE = Path(os.getenv("UNKNOWN_FILE", "unknown_questions.json"))
REPORT_FILE = Path(os.getenv("REPORT_FILE", "report.md"))
DEBUG_DIR = Path("debug")

ROLES = [
    "react native developer", "full stack developer", "mobile application developer",
    "react js developer", "node js developer", "mern stack developer", "web developer",
]
TIER1 = ["remote", "mumbai", "navi-mumbai", "thane", "kalyan", "dombivli", "mulund", "airoli", "panvel"]
TIER2 = ["bengaluru", "pune", "hyderabad", "chennai", "gurugram", "noida", "ahmedabad", "kolkata"]

TITLE_OK = re.compile(
    r"react|native|mobile|android|ios|full[\s-]*stack|mern|web developer|front[\s-]*end|node|javascript|"
    r"typescript|software (engineer|developer)|app developer|application developer|ui developer", re.I)
TITLE_BAD = re.compile(
    r"intern|trainee|fresher|manager|director|head\b|principal|architect|sales|marketing|tester|\bqa\b|"
    r"testing|devops|data (engineer|scientist|analyst)|designer|\.net|php|laravel|java\b(?!script)|"
    r"angular|vue|flutter|kotlin|swift(?!ui)|salesforce|sap\b|support|recruit|hr\b|python|django|golang|"
    r"unity|game|embedded|wordpress|shopify|drupal|magento", re.I)

FINAL_STATUS = {"applied", "applied_external", "external_unconfirmed", "already", "expired",
                "skipped_external", "unanswered", "filtered"}
COUNTS = {"applied", "applied_external", "external_unconfirmed"}
FAIL_STATUS = {"stuck", "no_button", "error"}


class Stop(Exception):
    """Daily limit / captcha / block - aaj ke liye aur apply nahi."""


# ---------------------------------------------------------------------------
# state files
# ---------------------------------------------------------------------------
def _load(path: Path, default):
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except Exception:
        return default


def _save(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(path)


class Unknowns:
    def __init__(self):
        self.data = _load(UNKNOWN_FILE, {})

    def __call__(self, question, options, source):
        key = re.sub(r"\s+", " ", (question or "")).strip()[:200]
        if not key:
            return
        e = self.data.setdefault(key, {"count": 0, "options": options, "source": source})
        e["count"] += 1
        e["last"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _save(UNKNOWN_FILE, self.data)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def pause(lo=1.0, hi=3.0):
    time.sleep(random.uniform(lo, hi))


def dump(page: Page, name: str) -> None:
    """Debug: screenshot + html (selectors theek karne ke kaam aata hai)."""
    try:
        DEBUG_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        page.screenshot(path=str(DEBUG_DIR / f"{stamp}_{name}.png"), full_page=True)
        (DEBUG_DIR / f"{stamp}_{name}.html").write_text(page.content()[:600_000])
    except Exception as e:
        log.warning("dump fail: %s", e)


def job_id(url: str) -> str:
    m = re.search(r"-(\d{9,14})(?:\?|$|#)", url or "")
    return m.group(1) if m else re.sub(r"\W+", "", (url or ""))[-40:]


def exp_ok(text: str) -> bool:
    """'4-8 Yrs' -> range [4,8] ka [EXP_MIN, EXP_MAX] se overlap. Text na mile to allow."""
    m = re.search(r"(\d+)\s*-\s*(\d+)", text or "")
    if not m:
        m1 = re.search(r"(\d+)\s*\+?\s*y", text or "", re.I)
        return True if not m1 else int(m1.group(1)) <= EXP_MAX
    lo, hi = int(m.group(1)), int(m.group(2))
    return lo <= EXP_MAX and hi >= EXP_MIN


def title_ok(t: str) -> bool:
    return bool(TITLE_OK.search(t or "")) and not TITLE_BAD.search(t or "")


def search_url(role: str, loc: str, page_no: int) -> str:
    slug = role.replace(" ", "-")
    suffix = f"-{page_no}" if page_no > 1 else ""
    q = f"k={quote(role)}&experience=5&jobAge={JOB_AGE}"
    if loc == "remote":
        return f"https://www.naukri.com/{slug}-jobs{suffix}?{q}&wfhType=2"
    return f"https://www.naukri.com/{slug}-jobs-in-{loc}{suffix}?{q}&l={quote(loc.replace('-', ' '))}"


_CARDS_JS = r"""
() => Array.from(document.querySelectorAll('.srp-jobtuple-wrapper, article.jobTuple, .jobTuple, div[class*="cust-job-tuple"]')).map(c => {
  const a = c.querySelector('a.title, a[class*="title"]');
  const q = s => { const e = c.querySelector(s); return e ? e.innerText.trim() : ''; };
  return { url: a ? a.href : '', title: a ? a.innerText.trim() : '',
           company: q('a.comp-name, [class*="comp-name"]'),
           exp: q('.expwdth, [class*="expwdth"], [class*="exp-wrap"]'),
           loc: q('.locWdth, [class*="locWdth"], [class*="loc-wrap"]') };
}).filter(j => j.url)
"""


def search(page: Page, role: str, loc: str, seen: set) -> list:
    out = []
    for pn in range(1, PAGES + 1):
        url = search_url(role, loc, pn)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(3500)
            _guard(page)
            cards = page.evaluate(_CARDS_JS)
        except Stop:
            raise
        except Exception as e:
            log.warning("search fail %s: %s", url, str(e).splitlines()[0])
            break
        if not cards:
            log.info("0 cards: %s | %s p%d", role, loc, pn)
            if pn == 1 and _flag("DUMP_EMPTY_SEARCH", "true") and not getattr(search, "_dumped", False):
                dump(page, "search_empty")  # selectors badle hain to yahan se dikhega
                search._dumped = True
            break
        for c in cards:
            jid = job_id(c["url"])
            if jid in seen:
                continue
            seen.add(jid)
            c["id"] = jid
            out.append(c)
        pause(1.5, 3)
    return out


def _guard(page: Page) -> None:
    try:
        body = page.inner_text("body", timeout=3000)
    except Exception:
        return
    if F.CAPTCHA_RE.search(body):
        dump(page, "captcha")
        raise Stop("captcha / security check")
    if "nlogin" in page.url:
        raise Stop("session expire (login page)")
    if F.DAILY_LIMIT_RE.search(body):
        raise Stop("daily apply limit")


# ---------------------------------------------------------------------------
# apply one job
# ---------------------------------------------------------------------------
_APPLY_BTN = "#apply-button, button:text-is('Apply'), button:has-text('Apply now')"
_COMPANY_BTN = "#company-site-button, button:has-text('Apply on company site'), a:has-text('Apply on company site')"
_ALREADY = "#already-applied, button:text-is('Applied'), .already-applied, :text-is('Already Applied')"
_LOGIN_TO_APPLY = "button:has-text('Login to apply'), a:has-text('Login to apply'), button:has-text('Register to apply')"
_EXPIRED = re.compile(r"no longer (accepting|available)|job (is )?(expired|closed)|position (has been )?filled|"
                      r"this job has expired|not accepting applications", re.I)


def _is_naukri(url: str) -> bool:
    return "naukri.com" in (url or "")


def _new_tab_url(context: BrowserContext, before: set, timeout=9.0):
    """Click ke baad naya tab khula? (page, url) return."""
    end = time.time() + timeout
    while time.time() < end:
        fresh = [p for p in context.pages if p not in before]
        if fresh:
            np = fresh[-1]
            try:
                np.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass
            for _ in range(8):  # about:blank se real URL tak
                if np.url and np.url != "about:blank":
                    break
                time.sleep(1)
            return np, np.url
        time.sleep(0.5)
    return None, None


def _external(context, url, ctx, resume, unknown) -> str:
    if not ALLOW_EXTERNAL:
        return "skipped_external"
    st = F.apply_external(context, url, ctx, resume, unknown, retries=EXT_RETRIES)
    return {"submitted": "applied_external", "unconfirmed": "external_unconfirmed"}.get(st, "skipped_external")


def apply_job(page: Page, context: BrowserContext, job: dict, resume: Path, unknown) -> str:
    ctx = {"company": job.get("company"), "role": job.get("title")}
    page.goto(job["url"], wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2500)
    _guard(page)
    body = ""
    try:
        body = page.inner_text("body", timeout=4000)
    except Exception:
        pass
    if _EXPIRED.search(body):
        return "expired"
    if page.locator(_LOGIN_TO_APPLY).first.is_visible():
        raise Stop("session logged out (Login to apply dikha)")
    if page.locator(_ALREADY).first.is_visible():
        return "already"

    company_btn = page.locator(_COMPANY_BTN).first
    apply_btn = page.locator(_APPLY_BTN).first
    if company_btn.is_visible() and not apply_btn.is_visible():
        before = set(context.pages)
        company_btn.click()
        np, url = _new_tab_url(context, before)
        if np is not None:
            try:
                np.close()
            except Exception:
                pass
        elif not _is_naukri(page.url):
            url = page.url
        if not url or _is_naukri(url):
            return "no_button"
        return _external(context, url, ctx, resume, unknown)

    if not apply_btn.is_visible():
        return "no_button"

    before_pages = set(context.pages)
    start_url = page.url
    apply_btn.click()

    end = time.time() + 12
    while time.time() < end:
        page.wait_for_timeout(800)
        # (a) naya tab: company site ya naukri questionnaire
        fresh = [p for p in context.pages if p not in before_pages]
        if fresh:
            np = fresh[-1]
            try:
                np.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass
            if _is_naukri(np.url):
                st = F.run_form_pages(np, ctx, resume, unknown, source="questionnaire")
                np.close()
                return _map_form(st)
            url = np.url
            np.close()
            return _external(context, url, ctx, resume, unknown)
        # (b) same tab redirect
        if page.url != start_url:
            if not _is_naukri(page.url):
                return _external(context, page.url, ctx, resume, unknown)
            if re.search(r"apply|questionnaire|myapply", page.url, re.I):
                return _map_form(F.run_form_pages(page, ctx, resume, unknown, source="questionnaire"))
        # (c) chatbot / success / limit
        try:
            txt = page.inner_text("body", timeout=2000)
        except Exception:
            txt = ""
        if F.DAILY_LIMIT_RE.search(txt):
            raise Stop("daily apply limit")
        if F.SUCCESS_RE.search(txt):
            return "applied"
        if page.locator(F._ROOT).first.is_visible():
            st = F.run_chatbot(page, ctx, unknown)
            if st == "applied":
                return "applied"
            if st == "limit":
                raise Stop("daily apply limit")
            if st == "blocked":
                raise Stop("captcha in chatbot")
            return {"unanswered": "unanswered", "stuck": "stuck"}.get(st, "no_button")
        # (d) inline questionnaire (modal) - form fields dikh rahe hain
        if page.locator("[role='dialog'] input:visible, [role='dialog'] select:visible, [class*='modal'] input:visible").count():
            return _map_form(F.run_form_pages(page, ctx, resume, unknown, source="questionnaire"))
    # kuch nahi hua: shayad button click seedha apply kar gaya (Applied badge)
    if page.locator(_ALREADY).first.is_visible():
        return "applied"
    return "no_button"


def _map_form(st: str) -> str:
    return {"submitted": "applied", "unconfirmed": "applied", "needs_info": "unanswered",
            "blocked": "stuck", "needs_account": "skipped_external"}.get(st, "stuck")


# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------
def queries():
    t1 = [(r, l) for l in TIER1 for r in ROLES]
    t2l = TIER2[:]
    random.shuffle(t2l)
    t2 = [(r, l) for l in t2l for r in ROLES[:4]]
    return t1 + t2


def run(page: Page, context: BrowserContext, resume: Path) -> dict:
    t0 = time.time()
    F.DEADLINE = t0 + TIME_BUDGET + 120  # ek slow company site poore budget ko na khaye
    applied = _load(APPLIED_FILE, {})
    unknown = Unknowns()
    seen = set()
    stats, rows = {}, []
    done_count, fails_in_row, dumps = 0, 0, 0
    abort = None
    log.info("APPLY start | max=%d budget=%dmin dry_run=%s external=%s", MAX_APPLIES, TIME_BUDGET // 60, DRY_RUN, ALLOW_EXTERNAL)

    try:
        for role, loc in queries():
            if done_count >= MAX_APPLIES or time.time() - t0 > TIME_BUDGET:
                break
            jobs = search(page, role, loc, seen)
            per_query = 0
            log.info("search '%s' @ %s -> %d naye cards", role, loc, len(jobs))
            for job in jobs:
                if done_count >= MAX_APPLIES or per_query >= MAX_PER_QUERY or time.time() - t0 > TIME_BUDGET:
                    break
                prev = applied.get(job["id"])
                if prev and (prev["status"] in FINAL_STATUS or prev.get("attempts", 1) >= 2):
                    continue
                reason = None
                if not title_ok(job["title"]):
                    reason = "title"
                elif not exp_ok(job["exp"]):
                    reason = "experience"
                elif any(b in (job["company"] or "").lower() for b in BLACKLIST):
                    reason = "blacklist"
                if reason:
                    stats["filtered"] = stats.get("filtered", 0) + 1
                    continue
                if DRY_RUN:
                    rows.append((job, "dry_run"))
                    done_count += 1
                    per_query += 1
                    log.info("DRY  %s | %s | %s | %s", job["title"], job["company"], job["exp"], job["loc"])
                    continue

                log.info("APPLY [%d/%d] %s | %s | %s", done_count + 1, MAX_APPLIES, job["title"], job["company"], job["exp"])
                try:
                    st = apply_job(page, context, job, resume, unknown)
                except Stop:
                    raise
                except Exception as e:
                    log.error("job error: %s", str(e).splitlines()[0])
                    st = "error"
                log.info("   => %s", st)

                if st in FAIL_STATUS and dumps < 5:
                    dump(page, f"{st}_{job['id']}")
                    dumps += 1
                applied[job["id"]] = {
                    "title": job["title"], "company": job["company"], "url": job["url"], "status": st,
                    "attempts": (prev or {}).get("attempts", 0) + 1,
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                _save(APPLIED_FILE, applied)
                stats[st] = stats.get(st, 0) + 1
                rows.append((job, st))
                if st in COUNTS:
                    done_count += 1
                    per_query += 1
                fails_in_row = fails_in_row + 1 if st in FAIL_STATUS else 0
                if fails_in_row >= MAX_FAILS_IN_ROW:
                    raise Stop(f"{fails_in_row} lagatar fail - shayad Naukri ka layout badla ya block (debug/ dekho)")
                # kuch jobs ke baad page reset (extra tabs band)
                for p in context.pages:
                    if p is not page:
                        try:
                            p.close()
                        except Exception:
                            pass
                pause(*DELAY)
    except Stop as s:
        abort = str(s)
        log.error("STOP: %s", abort)

    summary = {"counted": done_count, "stats": stats, "abort": abort, "minutes": round((time.time() - t0) / 60, 1)}
    _report(rows, summary)
    log.info("APPLY done: %s", summary)
    return summary


def _report(rows, summary) -> None:
    lines = [f"## Naukri auto-apply  ({datetime.now().strftime('%d %b %Y %H:%M')})", "",
             f"- Applied (counted): **{summary['counted']}** / limit {MAX_APPLIES}",
             f"- Time: {summary['minutes']} min", f"- Status: `{json.dumps(summary['stats'])}`"]
    if summary["abort"]:
        lines.append(f"- ⚠️ Stopped early: **{summary['abort']}**")
    if DRY_RUN:
        lines.append("- DRY RUN: kuch apply nahi hua")
    lines += ["", "| Status | Role | Company | Exp |", "|---|---|---|---|"]
    for job, st in rows:
        lines.append(f"| {st} | [{job['title'][:60]}]({job['url']}) | {job['company'][:40]} | {job['exp']} |")
    text = "\n".join(lines) + "\n"
    try:
        REPORT_FILE.write_text(text)
        gh = os.getenv("GITHUB_STEP_SUMMARY")
        if gh:
            with open(gh, "a") as f:
                f.write(text)
    except Exception as e:
        log.warning("report write fail: %s", e)
