#!/bin/bash
# Ek hi command me: session save -> local test -> GitHub secrets -> push -> GitHub test run.
# Local test pass na ho to kuch bhi push nahi hota.
#
# Use (job-automator folder ke andar):   bash deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\n❌ %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null || die "git nahi mila"
command -v gh  >/dev/null || die "gh nahi mila. Chalao: brew install gh"
git rev-parse --git-dir >/dev/null 2>&1 || die "Ye git repo nahi hai (job-automator folder me chalao)"
[ -f .env ] || die ".env nahi mila"
[ -f main.py ] && [ -f save_session.py ] || die "main.py / save_session.py nahi mile (zip unzip kiya?)"
gh auth status >/dev/null 2>&1 || die "gh login nahi hai. Chalao: gh auth login --with-token  (classic token: repo + workflow)"
python3 -c "import playwright, dotenv" 2>/dev/null || die "Pehle chalao: pip3 install -r requirements.txt && python3 -m playwright install chromium"

say "1/6  Repo saaf karna"
git rm -r -q --cached --ignore-unmatch __pycache__ .DS_Store logs login_error.png .github/workflows/setup_mac.sh
rm -f .github/workflows/setup_mac.sh

say "2/6  Naukri session banana"
rm -f state.json
echo "Pehle normal login (jaise aapka main.py Mac par chalta hai). Ye resume + headline bhi update karega,"
echo "aur chalne ke end me session state.json me save karega."
python3 main.py || true
if [ ! -s state.json ]; then
  echo "Normal run se session nahi bana. Ab browser se login (OTP aaye to email se daalo)..."
  python3 save_session.py
fi
[ -s state.json ] || die "state.json nahi bana. Upar ka error mujhe bhej do."

say "3/6  Local test (saved session se, bina OTP)"
LOG="$(mktemp)"
if ! python3 main.py 2>&1 | tee "$LOG"; then
  die "Local test fail hua. Kuch push nahi kiya. Upar ka log mujhe bhej do."
fi
grep -q "Saved session se login OK" "$LOG" \
  || die "Local test chala, par saved session se login nahi hua (password login hua). Push nahi kar raha."
echo "✅ Local test pass"

say "4/6  GitHub secrets set karna (NAUKRI_STATE_B64, NAUKRI_USER, NAUKRI_PASS)"
# main.py ne session refresh kiya hoga, isliye fresh state.json se secret banta hai
python3 -c "import base64;print(base64.b64encode(open('state.json','rb').read()).decode(),end='')" | gh secret set NAUKRI_STATE_B64
python3 -c "from dotenv import dotenv_values as d;print(d('.env')['NAUKRI_USER'],end='')" | gh secret set NAUKRI_USER
python3 -c "from dotenv import dotenv_values as d;print(d('.env')['NAUKRI_PASS'],end='')" | gh secret set NAUKRI_PASS

say "5/6  Git commit + push"
git add -A
git commit -q -m "Reuse saved Naukri session, repo cleanup" || echo "(commit karne ko kuch naya nahi)"
git push origin HEAD || die "git push fail hua. 'gh auth setup-git' chalao phir 'git push'"

say "6/6  GitHub par test run"
for i in 1 2 3 4 5 6; do
  gh workflow run naukri.yml && break
  [ "$i" = 6 ] && die "Workflow trigger nahi hua (Actions tab se manually Run workflow karo)"
  sleep 5
done
sleep 10
RUN="$(gh run list --workflow naukri.yml --limit 1 --json databaseId -q '.[0].databaseId')"
if gh run watch "$RUN" --exit-status; then
  printf '\n✅ GitHub run PASS. Ab roz 11:59 PM IST par apne aap chalega.\n'
  echo "   Mac wala job ab band kar sakte ho (double run se bachne ke liye):"
  echo "   launchctl unload ~/Library/LaunchAgents/com.saqlain.naukri-daily.plist"
else
  printf '\n❌ GitHub run fail hua. Log dekho:  gh run view %s --log-failed\n' "$RUN"
  echo "   Wo log aur Actions me 'debug' artifact ka screenshot mujhe bhej do."
  exit 2
fi
