"""ZTL judge service — the studio's judge without the AI, for api.ztljudge.com (formerly api.vitalyreznik.com).

Two doors onto one judge, both answered by the same three functions in tools.py:
  REST, for the Hugging Face page:  GET /language, GET /examples, POST /judge, GET /health
                                    POST /steps, POST /naive, GET /checker-examples (2026-11-05)
  MCP,  for agents:                 POST /mcp   (tools: language, examples, judge, check_steps, judge_naive,
                                                 checker_examples)
Nothing here calls a model. Requests are logged — when, who, what — to a
bounded file (ZTL_API_LOG); the answer is not, the judge recomputes it.

The MCP side is the official SDK, not a hand-rolled one: the protocol moved
under us (2026-07-28 is a self-contained POST with no handshake; older
clients still shake hands), and the SDK negotiates both.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

import cage
import contact
import tools

PUBLIC_HOST = os.environ.get("ZTL_API_HOST", "api.ztljudge.com")
# 2026-10-30: the service moved to api.ztljudge.com; the old name keeps working, NOT redirected — many clients turn a
# redirected POST into a GET, and an MCP client would silently break. Both names are always allowed, whatever the
# unit file's ZTL_API_HOST says; ZTL_API_HOSTS may add more (comma-separated).
PUBLIC_HOSTS = sorted({PUBLIC_HOST, "api.ztljudge.com", "api.vitalyreznik.com"}
                      | {h.strip() for h in os.environ.get("ZTL_API_HOSTS", "").split(",") if h.strip()})
PORT = int(os.environ.get("PORT", "8230"))
MAX_BODY = 256 * 1024                    # the studio's own limit per request
RATE_PER_MINUTE = int(os.environ.get("ZTL_API_RATE", "30"))            # per client address
RATE_TOTAL_PER_MINUTE = int(os.environ.get("ZTL_API_RATE_TOTAL", "300"))  # for everyone together
# ONE LIMITER FOR BOTH DOORS. Judgments run in threads, at most four at once,
# over REST and over MCP alike: the SDK runs a sync tool in its own pool of 40,
# so until 2026-09-24 an agent could hold ten times the judgments a page could.
# One process, so the threads share one core (the GIL) — which is also what
# keeps the blog and the studio on this VPS responsive.
WORKERS = anyio.CapacityLimiter(4)
# THE REQUEST LOG (the curator's word, 2026-09-24): when, who and what — not the
# answer, which the judge recomputes from the document. BOUNDED, so it cannot grow
# into a problem: files of LOG_MAX_BYTES, LOG_BACKUPS old ones kept, oldest dropped.
REQUEST_LOG = os.environ.get("ZTL_API_LOG")               # unset: no request log
# TIME, NOT SIZE (2026-10-10, Logik3's terms review, the curator's word): one file a day (UTC),
# LOG_DAYS of them kept, older ones deleted by the handler — a retention a privacy note can name.
LOG_DAYS = int(os.environ.get("ZTL_API_LOG_DAYS", "30"))

mcp = MCPServer(
    name="ztl-judge", version=tools.STUDIO_COMMIT,
    instructions=("ZTL judges a claim written as a ZFL table. Call `language` for the columns "
                  "and rules, see `examples` for complete documents, then call `judge`. "
                  "A compound claim is judged T or F; a claim that is a single name answers with "
                  "that name's own value, which is Z while it is unverified or rests on "
                  "something unverified. Put the formula itself in the claim for a T/F verdict. "
                  "A row whose value is `?` is an unknown; equalities linear in the unknowns are "
                  "solved, also when an interval [lo,hi] (a tolerance) multiplies an unknown. The "
                  "unknown's exact range is reported under `solved` when every interval enters to "
                  "the first power with rank at most one in the coefficients and right-hand side "
                  "(as one resistor enters Ohm's law), the system is square with one determinant "
                  "sign at every corner, and there are at most 10 intervals; otherwise the log "
                  "names the condition. The system's equalities then hold at every reading and a "
                  "comparison over an unknown is read at the corners where exact. A measured "
                  "quantity (value or interval with a ground) is judged as given, at every reading. "
                  "A document may also carry `sets` (naive sets: a list, cells, or a property that may "
                  "mention any set, itself included); each cell a ∈ S is then a row a_in_S the claim can "
                  "name — see `language`."))


@mcp.tool(structured_output=True)
async def language() -> dict[str, Any]:
    """The ZFL language: the columns of a row, the document fields, their meaning and rules."""
    return tools.language()


@mcp.tool(structured_output=True)
async def examples() -> dict[str, Any]:
    """Worked examples: questions already written as ZFL documents, ready to judge."""
    return {"examples": tools.examples()}


@mcp.tool(structured_output=True)
async def judge(document: dict[str, Any] | str) -> dict[str, Any]:
    """Judge a ZFL document with ZTL, a zero-trust logic: three values, two-valued connectives.

    Args:
        document: The ZFL document, as an object or as JSON text:
            {"rows": [{"name": ..., "means": ..., "status": ..., "ground": ...}], "claim": ...}.

    Returns:
        The verdict with its disposition and grade, the receipt, the
        instruments that applied, the issues found, and what the core read.
        Read the verdict WITH its disposition: T EARNED = established; F REFUTED = false; F OPEN or Z OPEN = NOT ESTABLISHED, it could still turn either way (do not report it as false) — `why` and `unverified` say what to check; ON CREDIT = holds only on an unverified ground. A compound claim gets T or F; a claim that is a single name gets that name's own value, Z while unverified.
        The full report is returned whatever `ask` says.
    """
    return await anyio.to_thread.run_sync(tools.judge, document, limiter=WORKERS)


@mcp.tool(structured_output=True)
async def check_steps(request: dict[str, Any] | str) -> dict[str, Any]:
    """Check an algebra derivation step by step: does each step keep exactly the same solutions?

    Args:
        request: {"equations": ["x^2 = x", "x = 1"], "var": "x", "domain": [lo, hi] or null,
            "domain_verified": true}. Each equation is the next step; one unknown; + - * / ^ with whole powers.

    Returns:
        The chain's verdict (its weakest step) and each step's: EARNED = same solutions on the domain;
        ON CREDIT = the same, on an unverified domain; REFUTED = a solution is lost (e.g. cancelling a factor that
        can be 0) or gained (e.g. clearing a denominator where the original was undefined), with the witness and
        the cause; E = the step divides by an expression that is 0 for every value. A request above the public
        limits is refused with the reason (ok: false, refused).
    """
    return await anyio.to_thread.run_sync(tools.check_steps, request, limiter=WORKERS)


@mcp.tool(structured_output=True)
async def judge_naive(universe: dict[str, Any] | str) -> dict[str, Any]:
    """Judge sets defined by a property, self-reference allowed (Russell's set, a web of trust, a policy rule).

    Args:
        universe: {"sets": {name: {"list": [...]} | {"data": {name: "T"|"F"|"Z"}} | {"abs": formula}},
            "claims": [formula, ...]}. Formulas: ["in", a, S], ["not", f], ["and"|"or"|"imp"|"iff", f, g],
            ["all"|"ex", "y", f], ["eq", S, T]; an abs set's own variable is "x". Every name is also an object.

    Returns:
        Per set: the members and non-members the definitions ground, and a passport for each membership they
        leave undecided — PARADOX (no stipulation can repair it), UNDERDETERMINED (consistent; a decision is
        owed), INTRINSIC, INPUT (verify the given cell), DOWNSTREAM (inherits a quarantine). Per claim: EARNED /
        REFUTED / EARNED IF STABLE / REFUTED IF STABLE / OPEN, the cells it rests on with their passports, and for
        a universal claim the instances left undecided. Oversized requests are refused with the reason.
    """
    return await anyio.to_thread.run_sync(tools.judge_naive, universe, limiter=WORKERS)


@mcp.tool(structured_output=True)
async def checker_examples() -> dict[str, Any]:
    """Ready requests for check_steps and judge_naive (the kernels' own examples)."""
    return tools.checker_examples()


def _json(obj, status=200):
    return JSONResponse(obj, status_code=status)


async def _rest_body(request: Request):
    body = await request.body()
    if len(body) > MAX_BODY:
        return None, _json({"ok": False, "refused": f"the request is larger than {MAX_BODY} bytes"}, 413)
    try:
        return json.loads(body or b"{}"), None
    except (ValueError, RecursionError):
        return None, _json({"ok": False, "refused": "the request is not valid JSON"}, 400)


@mcp.custom_route("/steps", methods=["POST"])
async def rest_steps(request: Request):
    payload, bad = await _rest_body(request)
    if bad is not None:
        return bad
    return _json(await anyio.to_thread.run_sync(tools.check_steps, payload, limiter=WORKERS))


@mcp.custom_route("/naive", methods=["POST"])
async def rest_naive(request: Request):
    payload, bad = await _rest_body(request)
    if bad is not None:
        return bad
    return _json(await anyio.to_thread.run_sync(tools.judge_naive, payload, limiter=WORKERS))


@mcp.custom_route("/checker-examples", methods=["GET"])
async def rest_checker_examples(request: Request):
    return _json(tools.checker_examples())


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return _json({"ok": True, "studio_commit": tools.STUDIO_COMMIT})


@mcp.custom_route("/language", methods=["GET"])
async def rest_language(request: Request):
    return _json(tools.language())


@mcp.custom_route("/examples", methods=["GET"])
async def rest_examples(request: Request):
    return _json({"examples": tools.examples()})


@mcp.custom_route("/judge", methods=["POST"])
async def rest_judge(request: Request):
    body = await request.body()
    if len(body) > MAX_BODY:
        return _json({"ok": False, "error": f"the document is larger than {MAX_BODY} bytes"}, 413)
    try:
        payload = json.loads(body or b"{}")
    except (ValueError, RecursionError) as e:
        # not only JSONDecodeError (2026-09-26, the studio's robustness red team): a number
        # past 4300 digits raises ValueError, deep nesting RecursionError — both a bad body
        hint = str(e) if isinstance(e, json.JSONDecodeError) else type(e).__name__
        return _json({"ok": False, "issues": [{"level": "error", "code": "E_JSON", "where": "document",
                                               "hint": f"the document is not valid JSON: {hint}"}]}, 400)
    doc = payload.get("document", payload) if isinstance(payload, dict) else payload
    result = await anyio.to_thread.run_sync(tools.judge, doc, limiter=WORKERS)
    return _json(result)


# THE CAGE DOOR (2026-10-10, cage.py): ZTL as an external verifier for Google's CAGE governance
# engine, on the wire pattern of its provider_06 — a signed tri-state, keys published apart.
# The same door under its own name since 2026-10-30 (the curator: the path should say what it is, not whose engine
# it fits): /signed/v1/* answers exactly as /cage/v1/*, same code, same key; /cage/v1 stays for those wired to it.
SIGNER = cage.Signer()


@mcp.custom_route("/signed/v1/verify", methods=["POST"])
@mcp.custom_route("/cage/v1/verify", methods=["POST"])
async def cage_verify(request: Request):
    body = await request.body()
    if len(body) > MAX_BODY:
        return _json({"protocolVersion": cage.PROTOCOL_VERSION,
                      "error": f"the body is larger than {MAX_BODY} bytes"}, 413)
    code, out = await anyio.to_thread.run_sync(
        lambda: cage.verify(body, tools.judge, SIGNER, tools.STUDIO_COMMIT), limiter=WORKERS)
    return _json(out, code)


_CAGE_TERMS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cage_terms.md"), encoding="utf-8").read()


@mcp.custom_route("/signed/v1/terms", methods=["GET"])
@mcp.custom_route("/cage/v1/terms", methods=["GET"])
async def cage_terms(request: Request):
    from starlette.responses import PlainTextResponse
    return PlainTextResponse(_CAGE_TERMS, media_type="text/markdown; charset=utf-8")


@mcp.custom_route("/signed/v1/keys", methods=["GET"])
@mcp.custom_route("/cage/v1/keys", methods=["GET"])
async def cage_keys(request: Request):
    return _json(SIGNER.jwks())


@mcp.custom_route("/signed/v1/health/live", methods=["GET"])
@mcp.custom_route("/cage/v1/health/live", methods=["GET"])
async def cage_live(request: Request):
    return _json({"live": True})


@mcp.custom_route("/signed/v1/health/ready", methods=["GET"])
@mcp.custom_route("/cage/v1/health/ready", methods=["GET"])
async def cage_ready(request: Request):
    return _json({"ready": SIGNER.ready, "kid": SIGNER.kid}, 200 if SIGNER.ready else 503)


# THE CONTACT FORM (2026-10-30, contact.py): https://ztljudge.com/contact/ is proxied here by Apache. No process of its
# own; the bridge on the curator's machine polls /contact/inbox.
@mcp.custom_route("/contact/challenge", methods=["GET"])
async def contact_challenge(request: Request):
    if not contact.ready():
        return _json({"ok": False, "error": "the form is not configured"}, 503)
    return _json({"challenge": contact.new_challenge(), "bits": contact.BITS})


@mcp.custom_route("/contact/submit", methods=["POST"])
async def contact_submit(request: Request):
    if not contact.ready():
        return _json({"ok": False, "error": "the form is not configured"}, 503)
    body = await request.body()
    if len(body) > contact.MAX_BODY:
        return _json({"ok": False, "error": "message too large"}, 413)
    try:
        form = json.loads(body or b"{}")
    except ValueError:
        return _json({"ok": False, "error": "bad request"}, 400)
    ip = request.client.host if request.client else "?"
    code, out = await anyio.to_thread.run_sync(lambda: contact.submit(form, ip))
    return _json(out, code)


@mcp.custom_route("/contact/inbox", methods=["GET"])
async def contact_inbox(request: Request):
    code, out = await anyio.to_thread.run_sync(lambda: contact.inbox(request.headers.get("authorization")))
    return _json(out, code)


class RateLimit:
    """At most RATE_PER_MINUTE requests a minute per client address, and
    RATE_TOTAL_PER_MINUTE for everyone together.

    The address is the one Apache saw: uvicorn trusts X-Forwarded-For from
    127.0.0.1 only, so a visitor cannot choose their own bucket. Preflight
    requests and /health are free — they judge nothing."""

    def __init__(self, app, per_minute: int, total_per_minute: int = RATE_TOTAL_PER_MINUTE):
        self.app, self.rate, self.total = app, per_minute, total_per_minute
        self.seen: dict[str, list[float]] = defaultdict(list)
        self.everyone: list[float] = []

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS" or scope["path"] == "/health":
            return await self.app(scope, receive, send)
        who = (scope.get("client") or ("?", 0))[0]
        now = time.monotonic()
        # FOR EVERYONE TOGETHER, TOO: a hundred addresses under the per-address
        # limit would still be a hundred times the load (the curator's word).
        self.everyone = [t for t in self.everyone if now - t < 60.0]
        if len(self.everyone) >= self.total:
            wait = int(60.0 - (now - self.everyone[0])) + 1
            response = JSONResponse({"ok": False, "error": f"the judge is busy; try again in {wait} s"},
                                    status_code=429, headers={"Retry-After": str(wait)})
            return await response(scope, receive, send)
        recent = [t for t in self.seen[who] if now - t < 60.0]
        if len(recent) >= self.rate:
            wait = int(60.0 - (now - recent[0])) + 1
            self.seen[who] = recent
            response = JSONResponse({"ok": False, "error": f"too many requests; try again in {wait} s"},
                                    status_code=429, headers={"Retry-After": str(wait)})
            return await response(scope, receive, send)
        recent.append(now)
        self.seen[who] = recent
        self.everyone.append(now)
        if len(self.seen) > 10000:                       # forget the quiet ones
            self.seen = defaultdict(list, {k: v for k, v in self.seen.items() if now - v[-1] < 60.0})
        return await self.app(scope, receive, send)


def _fingerprint(b: bytes) -> dict:
    """What the log keeps of a body: its SHA-256 and its size — enough to match a complaint
    ("I sent this") against the log, not enough to read it."""
    import hashlib
    return {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}


class AgedRotatingHandler(logging.handlers.TimedRotatingFileHandler):
    """Deletes rotated files by AGE, not by count. The stock handler keeps the newest
    `backupCount` files — on quiet days with no rollover those span more than `days`, and the
    privacy note promises at most 30 days. Old files are swept at every rollover and at start."""

    def __init__(self, path, when, days):
        super().__init__(path, when=when, backupCount=days, utc=True, encoding="utf-8")
        self.days = days
        for f in self.getFilesToDelete():
            os.remove(f)

    def getFilesToDelete(self):
        d, base = os.path.split(self.baseFilename)
        now, out = datetime.now(timezone.utc), []
        for name in os.listdir(d or "."):
            if not name.startswith(base + "."):
                continue
            suffix = name[len(base) + 1:]
            if not self.extMatch.fullmatch(suffix):
                continue
            try:
                at = datetime.strptime(suffix, self.suffix).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if (now - at).total_seconds() > self.days * self.interval:
                out.append(os.path.join(d, name))
        return out


def request_logger(path: str, days: int = LOG_DAYS, when: str = "midnight"):
    lg = logging.getLogger(f"ztl-api.requests.{path}")
    lg.propagate = False
    lg.setLevel(logging.INFO)
    h = AgedRotatingHandler(path, when, days)
    h.setFormatter(logging.Formatter("%(message)s"))
    lg.handlers[:] = [h]
    return lg


class RequestLog:
    """One JSON line per request: when, who, what.

    who  — the client's address as Apache saw it, its User-Agent and Origin, and
           the name an MCP client gives in its handshake;
    what — the endpoint and a FINGERPRINT of what was sent: SHA-256 and size of the
           document (for MCP: the tool, and the fingerprint of its arguments). Not the
           document itself (2026-10-10, data minimisation: people send their own texts,
           personal data among them; until then the whole document was logged, by the
           curator's word of 2026-09-24, reversed by his word of 2026-10-10). Not the
           answer. Only the HTTP status and the time taken are kept of the response.
           Preflight requests are not logged."""

    def __init__(self, app, logger):
        self.app, self.log = app, logger

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        if scope.get("path", "").startswith("/contact/"):   # the form's privacy note: no address in any log
            return await self.app(scope, receive, send)
        t0, chunks, more = time.monotonic(), [], True
        while more:                                   # buffer the body, then replay it
            msg = await receive()
            if msg["type"] == "http.request":
                chunks.append(msg.get("body", b""))
                more = msg.get("more_body", False)
            else:
                more = False
        body, replayed, status = b"".join(chunks), [False], [None]

        async def replay():
            if not replayed[0]:
                replayed[0] = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        async def watch(msg):
            if msg["type"] == "http.response.start":
                status[0] = msg["status"]
            await send(msg)

        try:
            await self.app(scope, replay, watch)
        finally:
            try:
                self._write(scope, body, status[0], time.monotonic() - t0)
            except Exception:                         # the log must never break a request
                pass

    def _write(self, scope, body, status, dt):
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers") or []}
        rec = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "ip": (scope.get("client") or ("?",))[0], "method": scope["method"],
               "path": scope["path"], "status": status, "ms": round(dt * 1000),
               "ua": headers.get("user-agent", "")[:300], "origin": headers.get("origin", "")[:200]}
        what = self._what(scope["path"], body)
        if what is not None:
            rec["what"] = what
        self.log.info(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))

    @staticmethod
    def _what(path, body):
        if not body:
            return None
        if len(body) > MAX_BODY:
            return {"too_large_bytes": len(body)}
        try:
            payload = json.loads(body)
        except Exception:                # RecursionError too (deep nesting), 2026-09-26
            return {"unparsed": _fingerprint(body)}
        if path in ("/steps", "/naive"):
            return {"request": _fingerprint(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())}
        if path == "/judge":
            doc = payload.get("document", payload) if isinstance(payload, dict) else payload
            return {"document": _fingerprint(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode())}
        if path.startswith("/mcp"):
            calls = []
            for m in (payload if isinstance(payload, list) else [payload]):
                if not isinstance(m, dict):
                    continue
                params, e = m.get("params") or {}, {"rpc": m.get("method")}
                if m.get("method") == "tools/call":
                    e["tool"] = params.get("name")
                    e["arguments"] = _fingerprint(json.dumps(params.get("arguments"), sort_keys=True,
                                                             ensure_ascii=False).encode())
                elif m.get("method") == "initialize":
                    e["client"], e["protocol"] = params.get("clientInfo"), params.get("protocolVersion")
                calls.append(e)
            return {"mcp": calls}
        return None


def build_app(rate_per_minute: int = RATE_PER_MINUTE, request_log: str | None = REQUEST_LOG,
              rate_total_per_minute: int = RATE_TOTAL_PER_MINUTE):
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp", json_response=True, stateless_http=True,
        max_request_body_size=MAX_BODY,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[*PUBLIC_HOSTS, "127.0.0.1:*", "localhost:*"]))
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "OPTIONS"],
                       allow_headers=["content-type", "accept", "mcp-protocol-version", "mcp-session-id"],
                       expose_headers=["mcp-session-id"])
    app.add_middleware(RateLimit, per_minute=rate_per_minute, total_per_minute=rate_total_per_minute)
    if request_log:                                   # outermost: refused requests are logged too
        app.add_middleware(RequestLog, logger=request_logger(request_log))
    return app


if __name__ == "__main__":
    import uvicorn
    # access_log=False (2026-10-10): the request log above is the one record of requests, with
    # its 30 days; uvicorn's access lines would copy IPs and paths into the system journal,
    # whose retention is the system's, not ours. Errors still go to the journal.
    uvicorn.run(build_app(), host="127.0.0.1", port=PORT, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", log_level="info", access_log=False)
