"""The contact form behind https://ztljudge.com/about/ — inside the API process, no service of its own
(the curator, 2026-10-30: "hang it on what we have, do not multiply processes").

    GET  /contact/challenge   {"challenge", "bits"}       a fresh proof-of-work task, HMAC-signed, single use
    POST /contact/submit      {"name", "email", "message", "website", "challenge", "nonce"}
    GET  /contact/inbox       the stored messages, for the curator's Telegram bridge only (Bearer token)

Apache serves these at https://ztljudge.com/contact/ (same origin as the page, no CORS) and keeps them out of its access
log; the API's RequestLog skips /contact/ as well — the privacy note (Logic3, 2026-10-30) promises that the address is
held in memory for 24 hours for the rate limit only and written to no log.

BOT PROTECTION, all on our own server (no Google, no Cloudflare, no cookies):
  1. honeypot — the field `website` is hidden from people; anything in it is a bot. Answered like a success, kept nowhere.
  2. proof of work — the browser finds `nonce` with SHA-256(challenge + ":" + nonce) starting with BITS zero bits
     (~65 000 tries at 16 bits: a second or two in a browser, a cost to a mass sender).
  3. minimum fill time — a challenge younger than MIN_SECONDS is refused; older than MAX_SECONDS is expired.
  4. single use — a challenge is accepted once.
  5. rate limit per address — PER_IP_10MIN and PER_IP_DAY, plus GLOBAL_DAY for everyone together (memory only).

A MESSAGE is one JSON line in LOG: id, time, name, email, message. No email is sent (the curator: "what matters is
that we do not miss a message and read it"): the bridge polls /contact/inbox once a minute, files each new message as a
request for the assistant and posts it to the curator's Telegram room. RETENTION: lines older than KEEP_DAYS are
deleted (checked on every submit and every inbox read).

Secrets: ZTL_CONTACT_SECRET_FILE (systemd LoadCredential) holds a hex key; challenges are signed with it and the inbox
token is derived from it (HMAC of "inbox"), so the server keeps one secret and the bridge holds only the token.
"""

import collections
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time

BITS = 16
MIN_SECONDS, MAX_SECONDS = 3, 2 * 3600
PER_IP_10MIN, PER_IP_DAY, GLOBAL_DAY = 3, 10, 200
MAX_BODY = 16 * 1024
LIMITS = {"name": 100, "email": 200, "message": 5000}
EMAIL_RE = re.compile(r"^[^@\s<>\"']{1,64}@[^@\s<>\"']{1,190}\.[A-Za-z]{2,}$")

def _secret():
    p = os.environ.get("ZTL_CONTACT_SECRET_FILE")
    try:
        return bytes.fromhex(open(p).read().strip()) if p else b""
    except (OSError, ValueError):
        return b""


SECRET = _secret()
LOG = os.environ.get("ZTL_CONTACT_LOG", "/var/lib/ztl-api/contact.jsonl")
KEEP_DAYS = 365

_lock = threading.Lock()
_used = {}                                   # challenge -> expiry (single use)
_hits = collections.defaultdict(list)        # ip -> [times]   (memory only)
_global = []                                 # [times]


def now():
    return time.time()


def sign(payload):
    return hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32]


def new_challenge(t=None):
    t = int(t if t is not None else now())
    payload = f"{t}.{secrets.token_hex(12)}"
    return f"{payload}.{sign(payload)}"


def leading_zero_bits(digest):
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        return bits + (8 - byte.bit_length())
    return bits


def check_challenge(challenge, nonce, t=None):
    """None if the challenge is valid, fresh, solved and unused (and marks it used); else the reason."""
    t = t if t is not None else now()
    parts = str(challenge).split(".")
    if len(parts) != 3 or not parts[0].isdigit():
        return "malformed challenge"
    payload = f"{parts[0]}.{parts[1]}"
    if not hmac.compare_digest(sign(payload), parts[2]):
        return "challenge not issued here"
    age = t - int(parts[0])
    if age < MIN_SECONDS:
        return "sent too fast"
    if age > MAX_SECONDS:
        return "challenge expired; reload the page"
    if not isinstance(nonce, str) or not (0 < len(nonce) <= 32):
        return "malformed nonce"
    if leading_zero_bits(hashlib.sha256(f"{challenge}:{nonce}".encode()).digest()) < BITS:
        return "proof of work not solved"
    with _lock:
        for c in [c for c, exp in _used.items() if exp < t]:
            del _used[c]
        if challenge in _used:
            return "challenge already used"
        _used[challenge] = int(parts[0]) + MAX_SECONDS
    return None


def rate_ok(ip, t=None):
    t = t if t is not None else now()
    with _lock:
        hits = [h for h in _hits[ip] if h > t - 86400]
        _hits[ip] = hits
        _global[:] = [h for h in _global if h > t - 86400]
        if len(_global) >= GLOBAL_DAY or len(hits) >= PER_IP_DAY or len([h for h in hits if h > t - 600]) >= PER_IP_10MIN:
            return False
        hits.append(t)
        _global.append(t)
        for k in [k for k, v in _hits.items() if not v]:
            del _hits[k]
        return True


def validate(form):
    out = {}
    for k, lim in LIMITS.items():
        v = form.get(k, "")
        if not isinstance(v, str):
            return None, f"{k} must be text"
        v = v.replace("\r\n", "\n").strip()
        if len(v) > lim:
            return None, f"{k} is longer than {lim} characters"
        out[k] = v
    if not EMAIL_RE.match(out["email"]):
        return None, "a valid email address is needed so that we can answer"
    if len(out["message"]) < 5:
        return None, "the message is empty"
    if any(c in out["name"] + out["email"] for c in "\n\r"):
        return None, "name and email must be one line"
    return out, None


def append_log(entry):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with _lock, open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def prune(t=None):
    """Delete log lines older than KEEP_DAYS (the privacy note promises 12 months)."""
    t = t if t is not None else now()
    if not os.path.exists(LOG):
        return 0
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t - KEEP_DAYS * 86400))
    with _lock:
        lines = open(LOG, encoding="utf-8").read().splitlines()
        keep = [l for l in lines if l and json.loads(l).get("time", "") >= cutoff]
        if len(keep) != len(lines):
            open(LOG, "w", encoding="utf-8").write("".join(l + "\n" for l in keep))
    return len(lines) - len(keep)


def submit(form, ip, t=None):
    """(http status, answer). The whole decision, testable without a socket."""
    t = t if t is not None else now()
    if not isinstance(form, dict):
        return 400, {"ok": False, "error": "bad request"}
    if form.get("website"):                     # honeypot: look like success, keep nothing
        print("honeypot: dropped one submission", file=sys.stderr)
        return 200, {"ok": True}
    reason = check_challenge(form.get("challenge", ""), form.get("nonce"), t)
    if reason:
        return 400, {"ok": False, "error": reason}
    if not rate_ok(ip, t):
        return 429, {"ok": False, "error": "too many messages from here; please try again later"}
    clean, err = validate(form)
    if err:
        return 400, {"ok": False, "error": err}
    entry = {"id": secrets.token_hex(6), "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)), **clean}
    append_log(entry)
    prune(t)
    return 200, {"ok": True}


def ready():
    return bool(SECRET)


def inbox_token():
    return hmac.new(SECRET, b"inbox", hashlib.sha256).hexdigest() if SECRET else ""


def inbox(auth_header, t=None):
    """(http status, answer) for the bridge: every stored message (at most the newest 500), oldest first."""
    tok = inbox_token()
    given = (auth_header or "").removeprefix("Bearer ").strip()
    if not tok or not hmac.compare_digest(given, tok):
        return 401, {"ok": False, "error": "unauthorized"}
    prune(t)
    if not os.path.exists(LOG):
        return 200, {"ok": True, "messages": []}
    with _lock:
        lines = open(LOG, encoding="utf-8").read().splitlines()
    return 200, {"ok": True, "messages": [json.loads(l) for l in lines[-500:] if l]}



