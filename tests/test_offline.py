"""Offline tests (Naukri ki zaroorat nahi):  python tests/test_offline.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("EXP_MIN", "4"); os.environ.setdefault("EXP_MAX", "6")
import apply as A

bad = 0
def chk(name, got, exp):
    global bad
    ok = got == exp; bad += not ok
    print("OK  " if ok else "FAIL", name, "->", got, "" if ok else f"(expected {exp})")

chk("exp 3-5 ok", A.exp_ok("3-5 Yrs"), True)
chk("exp 4-8 ok", A.exp_ok("4-8 Yrs"), True)
chk("exp 6-9 ok (overlap)", A.exp_ok("6-9 Yrs"), True)
chk("exp 7-10 no", A.exp_ok("7-10 Yrs"), False)
chk("exp 0-2 no", A.exp_ok("0-2 Yrs"), False)
chk("exp 8+ no", A.exp_ok("8+ Yrs"), False)
chk("exp empty allow", A.exp_ok(""), True)
for t, e in [("React Native Developer", True), ("Senior Full Stack Developer (MERN)", True), ("Mobile App Developer", True),
             ("Java Developer", False), ("Flutter Developer", False), ("React Native Intern", False),
             ("Python Full Stack Developer", False), ("JavaScript Developer", True), ("Sales Manager", False)]:
    chk("title " + t, A.title_ok(t), e)
chk("job_id", A.job_id("https://www.naukri.com/job-listings-react-native-developer-acme-mumbai-4-to-6-years-251025012345?src=x"), "251025012345")
chk("url remote", "wfhType=2" in A.search_url("react native developer", "remote", 1), True)
chk("url page2", "-jobs-in-thane-2?" in A.search_url("web developer", "thane", 2), True)
print("\nFAILED:", bad); sys.exit(1 if bad else 0)
