"""
Form filling engine (Playwright). Teen jagah kaam aata hai:
  1) Naukri ka chatbot drawer (Apply ke baad sawaal-jawab)       -> run_chatbot()
  2) Normal form / questionnaire page (Naukri ya company site)    -> fill_form()
  3) "Apply on company website" (external site, retries ke saath) -> apply_external()

Jawab candidate.answer() se aate hain. Jo sawaal ka jawab nahi pata uska record
`unknown(question, options, source)` callback se jaata hai (apply.py isko unknown_questions.json me likhta hai).

NOTE: Naukri ka DOM badalta rehta hai. Chatbot selectors best-effort hain; fail hone par
debug/ me screenshot + html dump milta hai (apply.py dump karta hai).
"""
import re
import time
import random
import logging
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import Page, BrowserContext

import candidate as C

log = logging.getLogger("naukri.form")

SUCCESS_RE = re.compile(
    r"(successfully applied|application (has been |was )?(successfully )?(submitted|sent|received)|"
    r"you(?:'| ha)ve (successfully )?applied|applied successfully|thank you for (applying|your application|submitting)|"
    r"we(?:'| ha)ve received your application|application complete)",
    re.I,
)
DAILY_LIMIT_RE = re.compile(r"(daily|today'?s?) (apply )?limit|reached (the |your )?(maximum|max|daily)|limit.*applications? (for|per) (the )?day", re.I)
CAPTCHA_RE = re.compile(r"captcha|are you a robot|verify you are human|unusual (traffic|activity)", re.I)

UnknownCb = Callable[[str, Optional[list], str], None]

# apply.py run() isko set karta hai (epoch seconds). Isse pehle hi external retries band ho jate hain.
DEADLINE: Optional[float] = None

# ---------------------------------------------------------------------------
# 1) Generic form
# ---------------------------------------------------------------------------
_FIELDS_JS = r"""
() => {
  const vis = e => { if(!e) return false; const r=e.getBoundingClientRect(); const s=getComputedStyle(e);
      return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; };
  const clean = t => (t||'').replace(/\s+/g,' ').trim().slice(0,300);
  const labelOf = e => {
    let t = e.getAttribute('aria-label') || '';
    if(!t && e.id){ const l=document.querySelector('label[for="'+CSS.escape(e.id)+'"]'); if(l) t=l.innerText; }
    if(!t){ const l=e.closest('label'); if(l) t=l.innerText; }
    if(!t && e.getAttribute('aria-labelledby')){
      t = e.getAttribute('aria-labelledby').split(' ').map(id=>{const n=document.getElementById(id);return n?n.innerText:''}).join(' ');
    }
    if(!t) t = e.placeholder || '';
    if(!t){ let p=e.parentElement; for(let k=0;k<4&&p&&!t;k++,p=p.parentElement){
        const l=p.querySelector('label,legend,[class*=label],[class*=question],[class*=Label]');
        if(l && l.innerText.trim() && !l.contains(e)) t=l.innerText; } }
    return clean(t || e.name || '');
  };
  const out=[]; let n=0; const groups={};
  document.querySelectorAll('input,textarea,select').forEach(e=>{
    const type=(e.type||e.tagName).toLowerCase();
    if(['hidden','submit','button','image','reset','password'].includes(type)) return;
    if(e.disabled || e.readOnly && type!=='file') return;
    const id = 'ja'+(n++); e.setAttribute('data-ja', id);
    const required = e.required || e.getAttribute('aria-required')==='true' || /\*/.test(labelOf(e));
    if(type==='radio' || type==='checkbox'){
      const lab = e.closest('label') || (e.id && document.querySelector('label[for="'+CSS.escape(e.id)+'"]')) || e.parentElement;
      if(!vis(e) && !vis(lab)) return;
      const text = clean(lab ? lab.innerText : e.value) || clean(e.value);
      const gkey = type+':'+(e.name || ('anon'+(e.closest('fieldset,[role=radiogroup],[class*=group],[class*=question]')?.className||'')));
      const grp = e.closest('fieldset,[role=radiogroup],[class*=question],[class*=Question],[class*=group]') || (e.parentElement && e.parentElement.parentElement);
      let q=''; if(grp){ const ql=grp.querySelector('legend,[class*=question],[class*=Question],[class*=title],h1,h2,h3,h4,h5,p');
          q = clean(ql && !ql.contains(e) ? ql.innerText : grp.innerText.split('\n')[0]); }
      if(!groups[gkey]){ groups[gkey]={kind:type, label:q||clean(e.name), options:[], required}; out.push(groups[gkey]); }
      groups[gkey].options.push({id, text, checked:e.checked});
      return;
    }
    if(type!=='file' && !vis(e)) return;
    const f={id, kind: e.tagName.toLowerCase()==='select' ? 'select' : e.tagName.toLowerCase()==='textarea' ? 'textarea' : type,
             label: labelOf(e), required, value: e.value||'', maxlen: e.maxLength>0 && e.maxLength<100000 ? e.maxLength : null};
    if(f.kind==='select') f.options = Array.from(e.options).map(o=>clean(o.text)).filter(t=>t && !/^(select|choose|--|please)/i.test(t));
    out.push(f);
  });
  return out;
}
"""


def _first_number(s: str) -> Optional[str]:
    m = re.search(r"\d+(?:\.\d+)?", s or "")
    return m.group(0) if m else None


def fill_form(page, ctx: dict, resume: Path, unknown: UnknownCb, source: str = "form") -> dict:
    """Page/frame par jo visible fields hain unhe bharo. Return: {'filled': n, 'unanswered': [labels]}"""
    try:
        fields = page.evaluate(_FIELDS_JS)
    except Exception as e:
        log.warning("fields collect fail: %s", e)
        return {"filled": 0, "unanswered": [], "error": str(e)}

    filled, unanswered = 0, []
    for f in fields:
        label, kind = f.get("label", ""), f["kind"]
        try:
            if kind in ("radio",):
                opts = [o["text"] for o in f["options"]]
                if any(o["checked"] for o in f["options"]):
                    continue
                ans = C.answer(label, opts, ctx=ctx)
                if not ans:
                    unanswered.append(label)
                    unknown(label, opts, source)
                    continue
                oid = next(o["id"] for o in f["options"] if o["text"] == ans)
                page.locator(f"[data-ja='{oid}']").evaluate("e => e.click()")
                filled += 1
            elif kind == "checkbox":
                # sirf consent/terms wale checkbox; baaki (multi-select) skip
                txt = " ".join(o["text"] for o in f["options"]).lower() + " " + label.lower()
                if re.search(r"agree|consent|terms|privacy|acknowledge|confirm|declare|authori[sz]e", txt):
                    for o in f["options"]:
                        if not o["checked"]:
                            page.locator(f"[data-ja='{o['id']}']").evaluate("e => e.click()")
                            filled += 1
                elif len(f["options"]) > 1:
                    ans = C.answer(label, [o["text"] for o in f["options"]], ctx=ctx)
                    if ans:
                        oid = next(o["id"] for o in f["options"] if o["text"] == ans)
                        page.locator(f"[data-ja='{oid}']").evaluate("e => e.click()")
                        filled += 1
            elif kind == "select":
                opts = f.get("options", [])
                ans = C.answer(label, opts, ctx=ctx)
                if not ans:
                    if f["required"]:
                        unanswered.append(label)
                    unknown(label, opts, source)
                    continue
                page.locator(f"[data-ja='{f['id']}']").select_option(label=ans)
                filled += 1
            elif kind == "file":
                if re.search(r"cover|photo|picture|portfolio|certificate|transcript", label, re.I):
                    continue
                page.locator(f"[data-ja='{f['id']}']").set_input_files(str(resume.resolve()))
                filled += 1
            else:  # text, email, tel, number, url, textarea, date, search...
                if f.get("value", "").strip():
                    continue  # pehle se bhara hua
                ans = C.answer(label, None, maxlen=f.get("maxlen"), ctx=ctx)
                if kind in ("number",) and ans is not None:
                    ans = _first_number(ans)
                if kind == "date" and ans and re.fullmatch(r"\d\d/\d\d/\d{4}", ans):
                    d, m, y = ans.split("/")
                    ans = f"{y}-{m}-{d}"
                if not ans:
                    if f["required"]:
                        unanswered.append(label)
                    if label:
                        unknown(label, None, source)
                    continue
                loc = page.locator(f"[data-ja='{f['id']}']")
                loc.fill(ans[: f["maxlen"]] if f.get("maxlen") else ans)
                filled += 1
        except Exception as e:
            log.warning("field '%s' (%s) fill fail: %s", label[:60], kind, str(e).splitlines()[0])
            if f.get("required"):
                unanswered.append(label)
    return {"filled": filled, "unanswered": unanswered}


# ---------------------------------------------------------------------------
# 2) Naukri chatbot drawer
# ---------------------------------------------------------------------------
_ROOT = "[class*='chatbot_Drawer'], [class*='chatbot_DrawerContentWrapper'], [id*='chatbot'], [class*='chatbot']"
_BOT_MSG = "[class*='botMsg'], [class*='botItem'], [class*='BotMsg'], [class*='msg'][class*='bot' i]"
_SEND = ("[class*='sendMsg'], [class*='send' i][class*='btn' i], button:has-text('Save'), button:has-text('Send'), "
         "button:has-text('Submit'), button:has-text('Next'), div:text-is('Save'), div:text-is('Send')")


def _vis(loc) -> bool:
    try:
        return loc.first.is_visible()
    except Exception:
        return False


def run_chatbot(page: Page, ctx: dict, unknown: UnknownCb, max_turns: int = 30) -> str:
    """Return: applied | stuck | unanswered | closed | limit | blocked"""
    root = page.locator(_ROOT).first
    last_q, repeats = "", 0
    for turn in range(max_turns):
        page.wait_for_timeout(1500 + int(random.random() * 800))
        body = ""
        try:
            body = page.inner_text("body", timeout=3000)
        except Exception:
            pass
        if SUCCESS_RE.search(body):
            return "applied"
        if DAILY_LIMIT_RE.search(body):
            return "limit"
        if CAPTCHA_RE.search(body):
            return "blocked"
        if not _vis(root):
            # drawer band: ya to apply ho gaya (button "Applied") ya khul hi nahi paya
            return "applied" if re.search(r"\bapplied\b", body, re.I) and not re.search(r"apply\b.*now", body[:300], re.I) else "closed"

        # --- sawaal nikalo (aakhri bot message)
        q = ""
        try:
            msgs = root.locator(_BOT_MSG)
            if msgs.count():
                q = msgs.nth(msgs.count() - 1).inner_text(timeout=2000).strip()
            if not q:
                lines = [l.strip() for l in root.inner_text(timeout=2000).split("\n") if l.strip()]
                q = lines[-3] if len(lines) >= 3 else (lines[-1] if lines else "")
        except Exception:
            pass
        if not q:
            continue
        if q == last_q:
            repeats += 1
            if repeats >= 3:
                log.warning("chatbot stuck on: %s", q[:120])
                return "stuck"
        else:
            last_q, repeats = q, 0
        log.info("chatbot Q%d: %s", turn + 1, q[:140].replace("\n", " "))

        # --- options (chips / radio / checkbox)
        opts, kind = [], "text"
        chips = root.locator("[class*='chip' i]:visible, [class*='Chip']:visible, [class*='ssrc__label']:visible, "
                             "label:visible, [role='radio']:visible, [role='option']:visible")
        try:
            opts = [t.strip() for t in chips.all_inner_texts() if t.strip()]
        except Exception:
            opts = []
        opts = [o for o in dict.fromkeys(opts) if len(o) < 120]
        if opts:
            kind = "choice"

        ans = C.answer(q, opts or None, maxlen=250, ctx=ctx)
        if not ans:
            unknown(q, opts or None, "chatbot")
            skip = root.locator("text=/^(skip|not applicable|na)$/i").first
            if _vis(skip):
                skip.click()
                continue
            return "unanswered"
        log.info("   -> %s", ans[:100])

        try:
            if kind == "choice":
                target = chips.filter(has_text=re.compile("^\\s*" + re.escape(ans) + "\\s*$", re.I)).first
                target.click(timeout=5000)
                # kuch chatbots me choice ke baad Save dabana padta hai
                send = root.locator(_SEND).first
                page.wait_for_timeout(600)
                if _vis(send):
                    send.click()
            else:
                box = root.locator("[contenteditable='true'], textarea, input[type='text'], input[type='number'], input:not([type])").first
                box.click(timeout=5000)
                try:
                    box.fill(ans)
                except Exception:
                    page.keyboard.type(ans, delay=25)
                page.wait_for_timeout(500)
                send = root.locator(_SEND).first
                if _vis(send):
                    send.click()
                else:
                    page.keyboard.press("Enter")
        except Exception as e:
            log.warning("chatbot action fail: %s", str(e).splitlines()[0])
            return "stuck"
    return "stuck"


# ---------------------------------------------------------------------------
# 3) External company website
# ---------------------------------------------------------------------------
_BTN_JS = r"""
(patterns) => {
  const vis = e => { const r=e.getBoundingClientRect(); const s=getComputedStyle(e);
    return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'&&!e.disabled; };
  const bad = /sign ?in|log ?in|register|create (an )?account|sign ?up|search|subscribe|cancel|back|share|save for later|linkedin|google|facebook|apple/i;
  const els = Array.from(document.querySelectorAll('button,input[type=submit],a[role=button],a.btn,a[class*=button],a[class*=apply]'));
  for(const p of patterns){
    const re = new RegExp(p,'i');
    for(const e of els){
      const t=(e.innerText||e.value||e.getAttribute('aria-label')||'').trim();
      if(t && re.test(t) && !bad.test(t) && vis(e)){ e.setAttribute('data-ja-btn','1'); return t; }
    }
  }
  return null;
}
"""
FINAL_BTNS = ["^submit application$", "^submit( now)?$", "^apply( now| for this job| to this job)?$", "^send application$", "^finish$"]
STEP_BTNS = ["^next( step)?$", "^continue$", "^review$", "^save (and|&) continue$"]


def run_form_pages(pg: Page, ctx: dict, resume: Path, unknown: UnknownCb, source: str = "questionnaire",
                   max_steps: int = 8) -> str:
    """Multi-step form (Naukri questionnaire ya company site) - jo page abhi khula hai us par.
    Return: submitted | unconfirmed | needs_info | needs_account | blocked | failed"""
    pg.wait_for_timeout(1500)
    finals = FINAL_BTNS + (["^save$", "^submit and apply$"] if source != "external" else [])
    submitted_click = False
    for step in range(max_steps):
        body = ""
        try:
            body = pg.inner_text("body", timeout=4000)
        except Exception:
            pass
        if SUCCESS_RE.search(body):
            return "submitted"
        if CAPTCHA_RE.search(body) or pg.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .g-recaptcha, .h-captcha").count():
            return "blocked"
        if pg.locator("input[type='password']:visible").count() > 0:
            return "needs_account"

        res = fill_form(pg, ctx, resume, unknown, source=source)
        if res["unanswered"]:
            log.info("%s: required fields unanswered: %s", source, res["unanswered"][:5])
            return "needs_info"

        btn_text = pg.evaluate(_BTN_JS, finals + STEP_BTNS)
        if not btn_text:
            break
        log.info("%s: click '%s'", source, btn_text)
        is_final = any(re.search(p, btn_text, re.I) for p in finals)
        try:
            pg.locator("[data-ja-btn='1']").first.click(timeout=8000)
        except Exception as e:
            log.warning("%s click fail: %s", source, str(e).splitlines()[0])
            break
        finally:
            try:
                pg.evaluate("document.querySelectorAll('[data-ja-btn]').forEach(e=>e.removeAttribute('data-ja-btn'))")
            except Exception:
                pass
        # sirf tab "submitted-ish" maano jab form bhara gaya ho (res['filled']>0) ya step 1 ke baad
        if is_final and (res["filled"] > 0 or step > 0):
            submitted_click = True
        pg.wait_for_timeout(3500)
    return "unconfirmed" if submitted_click else "failed"


def _external_once(pg: Page, url: str, ctx: dict, resume: Path, unknown: UnknownCb) -> str:
    pg.goto(url, wait_until="domcontentloaded", timeout=45_000)
    pg.wait_for_timeout(2000)
    return run_form_pages(pg, ctx, resume, unknown, source="external")


def apply_external(context: BrowserContext, url: str, ctx: dict, resume: Path, unknown: UnknownCb,
                   retries: int = 5) -> str:
    """Company site par apply. Network/load fail par `retries` baar try; account/captcha/info missing par turant chhod."""
    status = "failed"
    for attempt in range(1, retries + 1):
        if DEADLINE and time.time() > DEADLINE:
            log.warning("external: time budget khatam, retries band")
            break
        pg = context.new_page()
        pg.set_default_timeout(20_000)
        try:
            status = _external_once(pg, url, ctx, resume, unknown)
            log.info("external attempt %d/%d -> %s", attempt, retries, status)
            if status in ("submitted", "unconfirmed", "needs_account", "needs_info", "blocked"):
                return status
        except Exception as e:
            status = "failed"
            log.warning("external attempt %d/%d error: %s", attempt, retries, str(e).splitlines()[0])
        finally:
            try:
                pg.close()
            except Exception:
                pass
        time.sleep(3 * attempt + random.random() * 2)
    return status
