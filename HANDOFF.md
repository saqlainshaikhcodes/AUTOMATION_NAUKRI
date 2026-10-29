# HANDOFF - Naukri auto-apply (Saqlain)

## Goal
Roz 11:59 PM IST: login -> resume + headline refresh (main.py, pehle se chal raha) -> uske baad 15-30 min Naukri par
jobs apply (4-6 yrs, React Native / Full Stack / Web / Mobile; Remote, Mumbai, Navi Mumbai, Thane, Kalyan... phir baaki cities).
GitHub Actions par chalta hai. Profile data: candidate.py (resume + details se).

## File map (repo root = job-automator/)
| File | Kaam | Status |
|---|---|---|
| main.py | login + resume/headline; RUN_APPLY=true par apply.run() call | DONE (hook added) |
| apply.py | search, filters, 3 apply types, limits, applied.json, report.md | DONE |
| form_filler.py | fill_form, run_chatbot, run_form_pages, apply_external (5 retries) | DONE |
| candidate.py | profile + answer(question, options, maxlen, ctx) | DONE (24 self-tests pass) |
| save_session.py | OTP login se state.json | hardcoded password hataya |
| .github/workflows/naukri.yml | cron 18:29 UTC, timeout 45m, apply history cache | UPDATED |
| tests/test_offline.py | apply.py ke filters ke tests | DONE |

## Tested (offline, real Chromium, mock pages)
fill_form (number/select/radio/textarea maxlen/file/consent), run_form_pages (2-step questionnaire),
run_chatbot (chips + text + success), candidate.py, apply.py filters.

## NOT tested (live Naukri chahiye) - pehla run yahi check karo
Naukri ke real selectors (search cards, Apply button, chatbot drawer). Fail par debug/ artifact me screenshot+html aata hai.
1. Actions -> Run workflow -> dry_run=true  (sirf jobs list; report.md me dikhega). Cards 0 aaye to debug/*search_empty.html se _CARDS_JS selectors theek karo.
2. Phir dry_run=false, max_applies=3. Status dekho (applied / no_button / stuck).
3. unknown_questions.json (artifact) dekho: jo sawaal pata nahi the, unke jawab candidate.py ke _rules me jodo.
4. Sab theek ho to max_applies=40 (default) par daily cron.

## Env knobs
MAX_APPLIES, TIME_BUDGET_MIN, MAX_FAILS_IN_ROW, EXP_MIN/EXP_MAX, JOB_AGE_DAYS, PAGES_PER_QUERY,
ALLOW_EXTERNAL, EXTERNAL_RETRIES, DRY_RUN, COMPANY_BLACKLIST, EXPECTED_CTC_LPA (default 9.5), GENDER (optional).

## Known limits / TODO
- Status "external_unconfirmed" = Submit click hua par success text nahi dikha (count hota hai, verify karna).
- Company-site forms me account/captcha/unknown required field -> skip (applied.json me skipped_external/unanswered).
- Naukri ToS automation par pabandi laga sakta hai; MAX_APPLIES chhota rakho, captcha aaye to run khud ruk jata hai.
- GENDER set nahi hai to gender wale sawaal skip hote hain.
- Rotate Naukri password (purani git history me tha) aur GitHub secrets me hi rakho.
