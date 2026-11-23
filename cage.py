"""ZTL as an external verifier for Google's CAGE governance engine — the endpoint side.

WHY (2026-10-10): a ZTL endpoint for CAGE's external-verifier slot. There is no separate specification; the standard is CAGE's own open code
(google/cybernetic-agent-governance-engine, Apache-2.0). Read there on 2026-10-10:

  * The live-HTTP pattern for an independent verifier is `provider_06` (Agent Integrity):
    POST /verify answers a tri-state, POST /receipt a receipt signed Ed25519 over RFC 8785
    JCS with its SHA-256 `receiptDigest`, public keys from a separate JWKS manifest (never
    from the signed document), GET /health/ready and /health/live, everything fail-closed.
  * An attestation in a governance envelope (`seams/attestation.py`) reads VERIFIED /
    DENIED / STALE / UNVERIFIED.

This module follows provider_06's wire pattern with ZTL's own vocabulary — each provider
keeps its own tokens (provider_06: PASS/REVIEW/BLOCKED; provider_01: ALLOW/REFUSE/ESCALATE),
and CAGE's adapter for a provider maps them. One call answers and signs at once (the
receipt travels with the verdict), so there is no second round trip to bind them.

    POST /cage/v1/verify      {"protocolVersion": "ztl-cage-1", "document": <ZFL document>}
    GET  /cage/v1/keys        JWKS: the Ed25519 public key(s)
    GET  /cage/v1/health/live    200 while the process answers
    GET  /cage/v1/health/ready   200 only when a signing key is loaded (else 503)

Status (ZTL's disposition) -> `admitted` / what CAGE's adapter does with it:
    EARNED     -> admitted
    REFUTED    -> not admitted (the claim is false on the declared grounds)
    EARNED_IF_STABLE  -> review: true in every cleaning of the repeated unverified grounds — established once
                         each of them reads the same at every occurrence (judge disposition since 2026-10-29)
    REFUTED_IF_STABLE -> review: false in every cleaning, on the same condition
    ON_CREDIT  -> review: holds only on an unverified ground
    OPEN       -> review: not established, could turn either way
    NO_VERDICT -> review: no claim to judge
The ATTESTATION's status is always UNVERIFIED (corrected 2026-10-10, prose review): in CAGE's
own definition (seams/attestation.py) UNVERIFIED is "resolved and well-formed, but signature not
yet checked" — VERIFIED is for the CONSUMER to set once it has checked our signature, not for a
provider to claim of itself. CAGE's own warrant attestation takes the same line (always
UNVERIFIED, the substance in metadata). ZTL's answer travels in `metadata.ztl_status`.
`settleByChecking` (signed) says whether checking can ever settle it: false for a paradox,
which ZTL reports as OPEN with a permanent refusal — the core's word is kept, not renamed.
An unreadable document is HTTP 400 and no receipt: fail closed, never a verdict.

THE SIGNATURE (provider_06's receipt rule, reproduced, not imported): the receipt minus
`receiptDigest` and `signature` is canonicalized (RFC 8785 JCS); `receiptDigest` is its
SHA-256 hex; `signature.value` is Ed25519 over the same canonical bytes, base64url. The
receipt carries only strings, integers and booleans, so the JCS here is exact (keys sorted
by code point — the same as UTF-16 order for the ASCII keys used — no insignificant
whitespace, no floats; a float would be refused rather than formatted).

WHAT A SIGNATURE ATTESTS (the security review, 2026-10-10). Anyone may send rows marked
`verified` with any ground, and the judge will EARN a claim that follows from them. ZTL attests
the INFERENCE from the grounds the caller declared, not the grounds. So the signed receipt
carries `restsOn` — every row the verdict could stand on, with its status and declared ground —
and `attests`, which says so in words; a consumer that wants the grounds themselves verified
must check `restsOn` against its own sources. (CAGE's own warrant seam takes the same line: a
declared digest proves consistency, not issuance.) An optional `nonce` from the caller (at most
128 characters) is echoed in the signed receipt, so a stale receipt cannot be replayed into a
fresh request.

DETERMINISM: `requestDigest` is the SHA-256 of the exact bytes received; the verdict is a
function of the document and the judge (`judge.studioCommit`), recorded in the receipt.
Only `issuedAt` differs between two calls with the same body.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone

PROTOCOL_VERSION = "ztl-cage-1"
TERMS_URL = "https://api.ztljudge.com/signed/v1/terms"   # Terms of Use and Privacy Note (cage_terms.md)
RECEIPT_VERSION = "ztl-receipt-1"
PROVIDER_NAME = "ztl"
ATTESTATION_TYPE = "ZTL_JUDGMENT"

STATUS_OF = {"EARNED": "EARNED", "REFUTED": "REFUTED", "EARNED IF STABLE": "EARNED_IF_STABLE",
             "REFUTED IF STABLE": "REFUTED_IF_STABLE", "ON CREDIT": "ON_CREDIT", "OPEN": "OPEN",
             None: "NO_VERDICT", "E": "NO_VERDICT"}
# A disposition not in this table is REFUSED (HTTP 500, no receipt), never signed under a guessed status: on
# 2026-10-29 the judge gained EARNED/REFUTED IF STABLE and this endpoint signed them as NO_VERDICT for a day.
ATTESTATION_STATUS = "UNVERIFIED"     # the consumer, not the provider, sets VERIFIED (see above)


# ---------------------------------------------------------------- canonical JSON
def jcs(obj) -> bytes:
    """RFC 8785 for the value kinds a receipt holds: str, int, bool, None, list, dict."""
    def check(x):
        if isinstance(x, float):
            raise ValueError("a float in a signed receipt: refused (JCS number formatting is not reproduced here)")
        if isinstance(x, dict):
            for k, v in x.items():
                if not isinstance(k, str):
                    raise ValueError("a non-string key")
                check(v)
        elif isinstance(x, list):
            for v in x:
                check(v)
        elif not (x is None or isinstance(x, (str, int, bool))):
            raise ValueError(f"{type(x).__name__} in a signed receipt")
    check(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------- the signing key
class Signer:
    """The Ed25519 key, read from a file holding the 32-byte private seed. No key: not ready,
    and /verify answers 503 — a verdict without a signature is not offered."""

    def __init__(self, path: str | None = None):
        self.key = self.kid = self.public = None
        path = path if path is not None else os.environ.get("ZTL_CAGE_KEY_FILE", "")
        if not path:
            return
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        with open(path, "rb") as f:
            seed = f.read()
        if len(seed) != 32:
            raise ValueError("the key file must hold exactly 32 bytes (an Ed25519 seed)")
        self.key = Ed25519PrivateKey.from_private_bytes(seed)
        self.public = self.key.public_key().public_bytes(serialization.Encoding.Raw,
                                                         serialization.PublicFormat.Raw)
        self.kid = "ztl-" + hashlib.sha256(self.public).hexdigest()[:16]

    @property
    def ready(self) -> bool:
        return self.key is not None

    def jwks(self) -> dict:
        if not self.ready:
            return {"keys": []}
        return {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": _b64u(self.public),
                          "kid": self.kid, "use": "sig", "alg": "EdDSA"}]}

    def sign(self, receipt: dict) -> dict:
        body = {k: v for k, v in receipt.items() if k not in ("receiptDigest", "signature")}
        canon = jcs(body)
        return dict(body, receiptDigest=hashlib.sha256(canon).hexdigest(),
                    signature={"alg": "Ed25519", "kid": self.kid, "value": _b64u(self.key.sign(canon))})


ATTESTS = ("the verdict follows, by ZTL, from the grounds the document declares; the grounds "
           "themselves are the caller's statements and were not verified by ZTL")
MAX_RESTS_ON = 64


def rests_on(doc):
    """Every row the verdict could stand on: name, status, declared ground (bounded)."""
    rows = [r for r in (doc.get("rows") or [])
            if isinstance(r, dict) and r.get("status") in ("verified", "refuted", "defined")]
    out = [{"name": str(r.get("name", ""))[:100], "status": r["status"], "ground": str(r.get("ground") or "")[:200]}
           for r in rows[:MAX_RESTS_ON]]
    if len(rows) > MAX_RESTS_ON:     # the marker only when a row was really left out (it was added at exactly 64 too)
        out.append({"name": "...", "status": "truncated", "ground": f"more than {MAX_RESTS_ON} rows"})
    return out


# ---------------------------------------------------------------- the verdict
def verify(raw_body: bytes, judge, signer: Signer, studio_commit: str, now=None):
    """(http status, response dict). `judge` is tools.judge (document -> report)."""
    if not signer.ready:
        return 503, {"protocolVersion": PROTOCOL_VERSION, "error": "no signing key loaded: no verdict is offered unsigned"}
    try:
        payload = json.loads(raw_body or b"{}")
    except (ValueError, RecursionError) as e:
        return 400, {"protocolVersion": PROTOCOL_VERSION, "error": f"the body is not valid JSON: {type(e).__name__}"}
    if not isinstance(payload, dict) or payload.get("protocolVersion") != PROTOCOL_VERSION:
        return 400, {"protocolVersion": PROTOCOL_VERSION,
                     "error": f"protocolVersion must be {PROTOCOL_VERSION!r} (an unknown protocol is refused, not guessed)"}
    try:   # a lone surrogate parses as JSON but cannot be signed as UTF-8: refuse it here, not crash at signing
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return 400, {"protocolVersion": PROTOCOL_VERSION, "error": "the body holds text that is not valid Unicode "
                                                                   "(a lone surrogate); it cannot be signed"}
    doc = payload.get("document")
    nonce = payload.get("nonce")
    if nonce is not None and not (isinstance(nonce, str) and 0 < len(nonce) <= 128):
        return 400, {"protocolVersion": PROTOCOL_VERSION, "error": "`nonce` must be a string of 1..128 characters"}
    if not isinstance(doc, dict):
        return 400, {"protocolVersion": PROTOCOL_VERSION, "error": "`document` must be a ZFL document (an object)"}
    report = judge(doc)
    if not report.get("ok"):
        return 400, {"protocolVersion": PROTOCOL_VERSION, "error": "the document cannot be judged",
                     "issues": [i for i in report.get("issues", []) if i.get("level") == "error"][:20]}
    ans = report.get("answer") or {}
    if ans.get("disposition") not in STATUS_OF:
        return 500, {"protocolVersion": PROTOCOL_VERSION,
                     "error": f"the judge answered with a disposition this endpoint does not know "
                              f"({ans.get('disposition')!r}); refused rather than signed under a guessed status"}
    status = STATUS_OF[ans.get("disposition")]
    findings = []
    if status != "EARNED":
        findings.append({"code": f"ztl.{status.lower()}" if ans.get("settle_by_checking", True) else "ztl.unsettleable",
                         "severity": "review" if status != "REFUTED" else "blocked",
                         "message": str(ans.get("meaning") or ans.get("why_not") or status)[:500]})
    for name in (ans.get("to_check") or [])[:20]:
        findings.append({"code": "ztl.to_check", "severity": "review", "message": f"verify: {name}"})
    receipt = {
        "receiptVersion": RECEIPT_VERSION, "protocolVersion": PROTOCOL_VERSION,
        "provider": PROVIDER_NAME, "judge": {"studioCommit": studio_commit},
        "requestDigest": hashlib.sha256(raw_body or b"").hexdigest(),
        "status": status,
        "verdict": ans.get("verdict"), "disposition": ans.get("disposition"),
        "settleByChecking": bool(ans.get("settle_by_checking", True)),
        "attests": ATTESTS,
        "restsOn": rests_on(doc),
        "nonce": nonce,
        "issuedAt": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    signed = signer.sign(receipt)
    return 200, {
        "protocolVersion": PROTOCOL_VERSION,
        "terms": TERMS_URL,
        "status": status,
        "admitted": status == "EARNED",
        "needsHumanReview": status not in ("EARNED", "REFUTED"),
        "settleByChecking": receipt["settleByChecking"],
        "findings": findings,
        "attestation": {"provider_name": PROVIDER_NAME, "attestation_type": ATTESTATION_TYPE,
                        "status": ATTESTATION_STATUS, "receipt_id": signed["receiptDigest"],
                        "attested_at": receipt["issuedAt"],
                        "metadata": {"ztl_status": status, "disposition": ans.get("disposition"), "verdict": ans.get("verdict"),
                                     "studio_commit": studio_commit, "attests": ATTESTS,
                                     "rests_on": receipt["restsOn"]}},
        "receipt": signed,
    }
