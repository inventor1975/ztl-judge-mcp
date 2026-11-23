"""The contact form (contact.py + its routes in server.py), tested without the network.

Run: python3 test_contact.py"""
import hashlib
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
key = os.path.join(tmp, "contact.secret")
open(key, "w").write(os.urandom(32).hex())
os.environ["ZTL_CONTACT_SECRET_FILE"] = key
os.environ["ZTL_CONTACT_LOG"] = os.path.join(tmp, "contact.jsonl")
os.environ["ZTL_API_LOG"] = os.path.join(tmp, "requests.jsonl")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import contact  # noqa: E402

ok = bad = 0


def check(name, cond, info=""):
    global ok, bad
    if cond:
        ok += 1
        print("  OK  ", name)
    else:
        bad += 1
        print("  FAIL", name, info)


def solve(ch):
    n = 0
    while contact.leading_zero_bits(hashlib.sha256(f"{ch}:{n}".encode()).digest()) < contact.BITS:
        n += 1
    return str(n)


T0 = 1_800_000_000
form = lambda ch, **kw: {"name": "Ada", "email": "ada@example.org", "message": "Hello, a question about ZTL.",
                         "website": "", "challenge": ch, "nonce": solve(ch), **kw}

ch = contact.new_challenge(T0)
code, out = contact.submit(form(ch), "1.1.1.1", T0 + 10)
check("a proper message is accepted", code == 200 and out["ok"], out)
lines = open(os.environ["ZTL_CONTACT_LOG"]).read().splitlines()
rec = json.loads(lines[-1])
check("it is logged with id, time, name, email, message — and no address",
      set(rec) == {"id", "time", "name", "email", "message"} and "1.1.1.1" not in lines[-1], rec)
code, out = contact.submit(form(ch), "1.1.1.1", T0 + 11)
check("the same challenge twice: refused", code == 400 and "already used" in out["error"], out)

ch = contact.new_challenge(T0)
code, out = contact.submit(form(ch), "2.2.2.2", T0 + 1)
check("sent faster than the minimum: refused", code == 400 and "too fast" in out["error"], out)
ch = contact.new_challenge(T0)
code, out = contact.submit(form(ch), "2.2.2.2", T0 + contact.MAX_SECONDS + 5)
check("an expired challenge: refused", code == 400 and "expired" in out["error"], out)
ch = contact.new_challenge(T0)
code, out = contact.submit({**form(ch), "nonce": "0"}, "2.2.2.2", T0 + 10)
check("unsolved proof of work: refused", code == 400 and "proof of work" in out["error"], out)
forged = ch[:-4] + "0000"
code, out = contact.submit(form(forged), "2.2.2.2", T0 + 10)
check("a challenge not issued here: refused", code == 400 and "not issued" in out["error"], out)

n_before = len(open(os.environ["ZTL_CONTACT_LOG"]).read().splitlines())
ch = contact.new_challenge(T0)
code, out = contact.submit(form(ch, website="http://spam"), "3.3.3.3", T0 + 10)
check("honeypot filled: looks like success, nothing kept",
      code == 200 and len(open(os.environ["ZTL_CONTACT_LOG"]).read().splitlines()) == n_before, out)

ch = contact.new_challenge(T0)
code, out = contact.submit(form(ch, email="not-an-address"), "4.4.4.4", T0 + 10)
check("a bad email address: refused", code == 400 and "email" in out["error"], out)
ch = contact.new_challenge(T0)
code, out = contact.submit(form(ch, message="x" * 6000), "4.4.4.4", T0 + 10)
check("a message over 5000 characters: refused", code == 400 and "longer" in out["error"], out)

codes = []
for i in range(contact.PER_IP_10MIN + 1):
    ch = contact.new_challenge(T0 + 100)
    codes.append(contact.submit(form(ch), "5.5.5.5", T0 + 110 + i)[0])
check(f"rate limit: {contact.PER_IP_10MIN} in ten minutes pass, the next is 429",
      codes[:-1] == [200] * contact.PER_IP_10MIN and codes[-1] == 429, codes)

code, out = contact.inbox("Bearer wrong", T0 + 200)
check("inbox without the token: 401", code == 401, out)
code, out = contact.inbox("Bearer " + contact.inbox_token(), T0 + 200)
check("inbox with the token: the messages, oldest first",
      code == 200 and len(out["messages"]) >= 1 and out["messages"][0]["name"] == "Ada", out)

old = {"id": "old", "time": "2020-01-01T00:00:00Z", "name": "", "email": "a@b.co", "message": "old one"}
contact.append_log(old)
contact.inbox("Bearer " + contact.inbox_token(), T0 + 300)
check("retention: a line older than 365 days is deleted",
      "old one" not in open(os.environ["ZTL_CONTACT_LOG"]).read())

# the routes, through the app: and the API's request log must not mention /contact/
import server  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
client = TestClient(server.build_app(request_log=os.environ["ZTL_API_LOG"]))
r = client.get("/contact/challenge")
check("GET /contact/challenge: 200 with a challenge", r.status_code == 200 and r.json()["bits"] == contact.BITS, r.text)
r = client.get("/contact/inbox")
check("GET /contact/inbox without the token: 401", r.status_code == 401, r.text)
client.get("/health")
log = open(os.environ["ZTL_API_LOG"]).read() if os.path.exists(os.environ["ZTL_API_LOG"]) else ""
check("the API's request log has /health but nothing under /contact/",
      "/health" in log and "/contact/" not in log, log[:300])

print(f"contact form: {ok} ok, {bad} failed")
print("CONTACT GREEN" if not bad else "CONTACT RED")
sys.exit(1 if bad else 0)
