"""Stand for cage.py — ZTL as an external verifier for CAGE (provider_06's wire pattern).

The receipt is verified HERE independently of the signer: the public key is taken from the
published JWKS (the trust anchor is never the signed document), the digest and the Ed25519
signature recomputed over this file's own canonical JSON. Then over HTTP, through the app.

Run:  python3 test_cage.py   -> CAGE DOOR GREEN
"""
import base64
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import cage
import tools

ok = fail = 0


def check(name, cond, why=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  OK   {name}")
    else:
        fail += 1; print(f"  FAIL {name} — {why}")


def canon(obj):                     # this stand's own canonical form (sorted keys, no spaces)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def receipt_ok(receipt, jwks):
    """Digest and signature, checked against the JWKS key named by the receipt's kid."""
    body = {k: v for k, v in receipt.items() if k not in ("receiptDigest", "signature")}
    if hashlib.sha256(canon(body)).hexdigest() != receipt.get("receiptDigest"):
        return False
    sig = receipt.get("signature") or {}
    key = next((k for k in jwks["keys"] if k["kid"] == sig.get("kid")), None)
    if key is None:
        return False
    pub = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(key["x"] + "=="))
    try:
        pub.verify(base64.urlsafe_b64decode(sig["value"] + "=="), canon(body))
        return True
    except Exception:
        return False


def new_key():
    fd, path = tempfile.mkstemp()
    os.write(fd, os.urandom(32)); os.close(fd)
    return path


R = lambda n, s, g=None, **kw: dict(name=n, means=n, status=s, **({"ground": g} if g else {}), **kw)
DOCS = {
    "EARNED": {"rows": [R("p", "verified", "d1"), R("q", "verified", "d2")], "claim": "p & q"},
    "REFUTED": {"rows": [R("p", "verified", "d1"), R("q", "refuted", "d2")], "claim": "p & q"},
    "OPEN": {"rows": [R("p", "unverified"), R("q", "verified", "d2")], "claim": "p & q"},
    "ON_CREDIT": {"rows": [R("x", "unverified", value="5")], "claim": "x <= 10"},
    "NO_VERDICT": {"rows": [{"name": "L", "means": "this sentence is false", "status": "defined", "ground": "~L"}],
                   "claim": ""},
    "EARNED_IF_STABLE": {"rows": [R("p", "unverified")], "claim": "~(p & ~p)"},
    "REFUTED_IF_STABLE": {"rows": [R("p", "unverified")], "claim": "p & ~p"},
}
# always UNVERIFIED: in CAGE's own definition VERIFIED is the consumer's word after checking our
# signature, not a provider's word about itself (corrected 2026-10-10); ZTL's answer is in metadata
WANT_ATT = {k: "UNVERIFIED" for k in ("EARNED", "REFUTED", "OPEN", "ON_CREDIT", "NO_VERDICT", "EARNED_IF_STABLE",
                                      "REFUTED_IF_STABLE")}


def body(doc):
    return json.dumps({"protocolVersion": cage.PROTOCOL_VERSION, "document": doc}).encode()


key_path = new_key()
signer = cage.Signer(key_path)
jwks = signer.jwks()

print("1. the five dispositions, signed")
for want, doc in DOCS.items():
    code, out = cage.verify(body(doc), tools.judge, signer, "test")
    check(f"{want}: HTTP 200, status {want}, attestation {WANT_ATT[want]}, admitted only when EARNED, "
          f"receipt verifies against the JWKS key",
          code == 200 and out["status"] == want and out["attestation"]["status"] == WANT_ATT[want]
          and out["admitted"] == (want == "EARNED") and receipt_ok(out["receipt"], jwks)
          and out["attestation"]["receipt_id"] == out["receipt"]["receiptDigest"]
          and out["attestation"]["metadata"]["ztl_status"] == want,
          json.dumps(out)[:300])

def future_judge(doc):
    r = tools.judge(doc)
    return {**r, "answer": {**(r.get("answer") or {}), "disposition": "SOMETHING NEW"}}


code, out = cage.verify(body(DOCS["EARNED"]), future_judge, signer, "test")
check("a disposition the endpoint does not know: refused (500), no receipt — never signed under a guessed status",
      code == 500 and "receipt" not in out, json.dumps(out)[:300])

code, out = cage.verify(b'{"protocolVersion":"ztl-cage-1","document":{"rows":[{"name":"p","means":"x",'
                        b'"status":"verified","ground":"a\\ud800b"}],"claim":"p"}}', tools.judge, signer, "test")
check("a lone surrogate in the text: 400 and no receipt, not a crash at signing", code == 400 and "receipt" not in out,
      json.dumps(out)[:300])
rows64 = [R(f"p{i}", "verified", f"g{i}") for i in range(64)]
check("exactly 64 grounded rows: all listed, no 'truncated' marker; 65: 64 listed and the marker",
      len(cage.rests_on({"rows": rows64})) == 64
      and cage.rests_on({"rows": rows64 + [R("p64", "verified", "g64")]})[-1]["status"] == "truncated"
      and len(cage.rests_on({"rows": rows64 + [R("p64", "verified", "g64")]})) == 65)

liar = {"rows": [{"name": "L", "means": "this sentence is false", "status": "defined", "ground": "~L"}], "claim": "L"}
code, out = cage.verify(body(liar), tools.judge, signer, "test")
check("the liar as a claim: ZTL's own OPEN kept, settleByChecking false (signed), finding ztl.unsettleable",
      out["status"] == "OPEN" and out["receipt"]["settleByChecking"] is False
      and out["findings"][0]["code"] == "ztl.unsettleable" and receipt_ok(out["receipt"], jwks), json.dumps(out)[:300])
code, out = cage.verify(body(DOCS["OPEN"]), tools.judge, signer, "test")
check("an ordinary OPEN: settleByChecking true, and what to check is named",
      out["receipt"]["settleByChecking"] is True and any(f["code"] == "ztl.to_check" for f in out["findings"]),
      json.dumps(out)[:300])

print("2. fail closed")
code, out = cage.verify(b"{not json", tools.judge, signer, "test")
check("a body that is not JSON: 400, no receipt", code == 400 and "receipt" not in out, str(out))
code, out = cage.verify(json.dumps({"protocolVersion": "1-alpha", "document": DOCS["EARNED"]}).encode(),
                        tools.judge, signer, "test")
check("another protocol's version: 400, refused not guessed", code == 400 and "receipt" not in out, str(out))
code, out = cage.verify(body({"rows": [{"name": "T", "means": "x", "status": "verified"}], "claim": "T"}),
                        tools.judge, signer, "test")
check("a document the judge cannot read: 400 with its issues, no verdict, no receipt",
      code == 400 and out.get("issues") and "receipt" not in out and "status" not in out, str(out)[:300])
code, out = cage.verify(body(DOCS["EARNED"]), tools.judge, cage.Signer(""), "test")
check("no signing key: 503 — no verdict is offered unsigned", code == 503 and "status" not in out, str(out))

print("3. a forged or altered receipt does not verify")
code, out = cage.verify(body(DOCS["REFUTED"]), tools.judge, signer, "test")
forged = dict(out["receipt"], status="EARNED")
check("REFUTED rewritten to EARNED: the digest no longer matches", not receipt_ok(forged, jwks))
other = cage.Signer(new_key())
code2, out2 = cage.verify(body(DOCS["EARNED"]), tools.judge, other, "test")
check("signed by another key under our kid: refused by our JWKS",
      not receipt_ok(dict(out2["receipt"], signature=dict(out2["receipt"]["signature"], kid=signer.kid)), jwks))
check("its own kid is not in our JWKS: refused (keys come from the manifest, never from the receipt)",
      not receipt_ok(out2["receipt"], jwks))

print("4. deterministic")
t = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
a = cage.verify(body(DOCS["OPEN"]), tools.judge, signer, "test", now=t)[1]["receipt"]
b = cage.verify(body(DOCS["OPEN"]), tools.judge, signer, "test", now=t)[1]["receipt"]
check("the same body at the same time: the same receipt, signature included (Ed25519 is deterministic)", a == b)
check("requestDigest is SHA-256 of the exact bytes received",
      a["requestDigest"] == hashlib.sha256(body(DOCS["OPEN"])).hexdigest())
try:
    cage.jcs({"x": 0.5}); refused = False
except ValueError:
    refused = True
check("a float in a signed receipt is refused, not formatted", refused)

print("4b. what the signature attests; replay")
code, out = cage.verify(body(DOCS["EARNED"]), tools.judge, signer, "test")
rec = out["receipt"]
check("the signed receipt says what it attests (inference from DECLARED grounds) and lists them: p d1, q d2",
      "not verified by ZTL" in rec["attests"] and [(x["name"], x["ground"]) for x in rec["restsOn"]] == [("p", "d1"), ("q", "d2")]
      and receipt_ok(rec, jwks), json.dumps(rec)[:300])
check("rewriting a declared ground in the receipt breaks it", not receipt_ok(dict(rec, restsOn=[dict(rec["restsOn"][0], ground="court-ruling"), rec["restsOn"][1]]), jwks))
nb = json.dumps({"protocolVersion": cage.PROTOCOL_VERSION, "document": DOCS["EARNED"], "nonce": "n-123"}).encode()
code, out = cage.verify(nb, tools.judge, signer, "test")
check("a nonce is echoed inside the signature", code == 200 and out["receipt"]["nonce"] == "n-123" and receipt_ok(out["receipt"], jwks))
check("a receipt for one nonce does not pass as another's", not receipt_ok(dict(out["receipt"], nonce="n-999"), jwks))
code, out = cage.verify(json.dumps({"protocolVersion": cage.PROTOCOL_VERSION, "document": DOCS["EARNED"],
                                    "nonce": "x" * 200}).encode(), tools.judge, signer, "test")
check("a nonce over 128 characters: 400", code == 400, str(out))
many = {"rows": [R(f"r{i}", "verified", f"g{i}") for i in range(100)], "claim": "r0 & r1"}
code, out = cage.verify(body(many), tools.judge, signer, "test")
check("restsOn is bounded (100 declared grounds -> 64 and a truncation mark; the request itself answered 200)",
      code == 200 and len(out["receipt"]["restsOn"]) == 65 and out["receipt"]["restsOn"][-1]["status"] == "truncated",
      str(code) + json.dumps(out)[:200])

print("5. over HTTP, through the app")
os.environ["ZTL_CAGE_KEY_FILE"] = key_path
import importlib                                    # noqa: E402
import server                                       # noqa: E402
importlib.reload(server)
from starlette.testclient import TestClient         # noqa: E402
client = TestClient(server.build_app(rate_per_minute=1000, request_log=None, rate_total_per_minute=1000),
                    base_url="http://127.0.0.1:8230")
r = client.get("/cage/v1/health/ready")
check("GET /cage/v1/health/ready: 200 with the key's kid", r.status_code == 200 and r.json()["kid"] == signer.kid, r.text)
check("GET /cage/v1/health/live: 200", client.get("/cage/v1/health/live").status_code == 200)
keys = client.get("/cage/v1/keys").json()
check("GET /cage/v1/keys: one Ed25519 key (OKP, EdDSA)", len(keys["keys"]) == 1 and keys["keys"][0]["crv"] == "Ed25519", str(keys))
r = client.post("/cage/v1/verify", content=body(DOCS["EARNED"]), headers={"content-type": "application/json"})
check("POST /cage/v1/verify: EARNED, the receipt verifies against GET /cage/v1/keys",
      r.status_code == 200 and r.json()["status"] == "EARNED" and receipt_ok(r.json()["receipt"], keys), r.text[:300])
for path in ("health/ready", "keys", "terms"):
    a, b = client.get(f"/cage/v1/{path}"), client.get(f"/signed/v1/{path}")
    check(f"GET /signed/v1/{path} answers exactly as /cage/v1/{path}", a.status_code == b.status_code == 200
          and a.content == b.content, b.text[:200])
r = client.post("/signed/v1/verify", content=body(DOCS["EARNED"]), headers={"content-type": "application/json"})
check("POST /signed/v1/verify: EARNED, the receipt verifies against the same key",
      r.status_code == 200 and r.json()["status"] == "EARNED" and receipt_ok(r.json()["receipt"], keys), r.text[:300])
r = client.get("/cage/v1/terms")
check("GET /cage/v1/terms: the Terms of Use and Privacy Note (markdown), and every verdict links to it",
      r.status_code == 200 and "AS IS" in r.text and "is not stored" in r.text
      and client.post("/cage/v1/verify", content=body(DOCS["OPEN"])).json()["terms"] == cage.TERMS_URL, r.text[:200])
r = client.post("/cage/v1/verify", content=b"x" * (300 * 1024))
check("a body over 256 KB: 413", r.status_code == 413, str(r.status_code))
os.environ.pop("ZTL_CAGE_KEY_FILE")
importlib.reload(server)
client = TestClient(server.build_app(rate_per_minute=1000, request_log=None, rate_total_per_minute=1000),
                    base_url="http://127.0.0.1:8230")
check("without a key: ready 503, verify 503",
      client.get("/cage/v1/health/ready").status_code == 503
      and client.post("/cage/v1/verify", content=body(DOCS["EARNED"])).status_code == 503)

os.unlink(key_path)
print(f"cage door: {ok} ok, {fail} failed")
print("CAGE DOOR GREEN" if fail == 0 else "CAGE DOOR RED")
