"""Stand for the judge service (server.py), end to end over HTTP.

    ~/venvs/torch/bin/python -B test_api.py      (needs the mcp SDK, as the server does)

The service is started for real (uvicorn, a free port); REST is spoken with
urllib, MCP with the official client AND with a raw older-protocol handshake,
because agents in the wild speak both.
"""
import asyncio
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import uvicorn

import server
import tools

ok = fail = 0


def check(name, got, want):
    global ok, fail
    if got == want:
        ok += 1; print(f"  OK   {name}")
    else:
        fail += 1; print(f"  FAIL {name}: got {got!r}, want {want!r}")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


RATE = 40
PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
import os
import tempfile
LOG = os.path.join(tempfile.mkdtemp(prefix="ztl-api-log-"), "requests.jsonl")
cfg = uvicorn.Config(server.build_app(rate_per_minute=RATE, request_log=LOG, rate_total_per_minute=10000),
                     host="127.0.0.1", port=PORT,
                     proxy_headers=True, forwarded_allow_ips="127.0.0.1", log_level="warning")
srv = uvicorn.Server(cfg)
threading.Thread(target=srv.run, daemon=True).start()
for _ in range(100):
    if srv.started:
        break
    time.sleep(0.05)


def call(method, path, body=None, headers=None, ip=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(BASE + path, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None and "Content-Type" not in (headers or {}):
        req.add_header("Content-Type", "application/json")
    if ip:
        req.add_header("X-Forwarded-For", ip)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, *_parsed(r.headers, r.read())
    except urllib.error.HTTPError as e:
        return e.code, *_parsed(e.headers, e.read())


def _parsed(headers, raw):
    """Header names lower-cased; the body parsed only when it says it is JSON."""
    h = {k.lower(): v for k, v in headers.items()}
    if raw and h.get("content-type", "").startswith("application/json"):
        return h, json.loads(raw)
    return h, (raw.decode("utf-8", "replace") if raw else None)


LIAR = {"claim": "L", "rows": [{"name": "L", "means": "this sentence is false",
                                "status": "defined", "ground": "~Tr(L)"}]}

print("REST, for the page")
code, _, body = call("GET", "/health", ip="10.0.0.1")
check("GET /health", (code, body["ok"], body["studio_commit"]), (200, True, "28ed15c"))
code, headers, _ = call("GET", "/mcp", ip="10.0.0.4")
check("GET /mcp answers 405 at once (stateless, no SSE stream)", (code, headers.get("allow") or headers.get("Allow")), (405, "POST, OPTIONS"))
code, _, body = call("GET", "/language", ip="10.0.0.1")
check("GET /language: 13 columns", (code, len(body["row_columns"])), (200, 13))
code, _, body = call("GET", "/examples", ip="10.0.0.1")
check("GET /examples: all 56, in English", (code, len(body["examples"])), (200, 56))
code, _, body = call("POST", "/judge", LIAR, ip="10.0.0.1")
check("POST /judge: the liar is UNREDEEMABLE", (code, body["report"]["receipt"]["verdict"]["credit"]), (200, "UNREDEEMABLE"))
code, _, wrapped = call("POST", "/judge", {"document": LIAR}, ip="10.0.0.1")
check("the document may come bare or under 'document'", wrapped["report"], body["report"])
check("REST answers what tools.judge answers", body["report"], tools.judge(LIAR)["report"])
code, _, body = call("POST", "/judge", b"{not json", ip="10.0.0.1")
check("broken JSON: 400 with an issue, not a crash", (code, body["issues"][0]["code"]), (400, "E_JSON"))
code, _, _ = call("POST", "/judge", b" " * (server.MAX_BODY + 1), ip="10.0.0.1")
check("a document over 256 KB: 413", code, 413)

print("\nCORS, so the Hugging Face page may call it")
origin = "https://inventor1975-ztl-studio.static.hf.space"
code, headers, _ = call("GET", "/language", headers={"Origin": origin}, ip="10.0.0.2")
check("a GET carries Access-Control-Allow-Origin", headers.get("access-control-allow-origin"), "*")
code, headers, _ = call("OPTIONS", "/judge", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                     "Access-Control-Request-Headers": "content-type"}, ip="10.0.0.2")
check("the preflight for POST /judge is allowed", (code, "POST" in headers.get("access-control-allow-methods", "")), (200, True))

print("\nthe rate limit, per client address as Apache saw it")
codes = [call("GET", "/language", ip="10.9.9.9")[0] for _ in range(RATE + 1)]
check(f"{RATE} requests a minute pass, the next is refused", (codes[:RATE].count(200), codes[RATE]), (RATE, 429))
check("another address is not held back by the first", call("GET", "/language", ip="10.9.9.8")[0], 200)
check("health is never rate-limited", call("GET", "/health", ip="10.9.9.9")[0], 200)

print("\nMCP host check (DNS rebinding protection)")
init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "raw-stand", "version": "0"}}}
mcp_headers = {"Accept": "application/json, text/event-stream"}
code, _, body = call("POST", "/mcp", init, headers={**mcp_headers, "Host": "api.vitalyreznik.com"}, ip="10.0.0.3")
check("Host api.vitalyreznik.com is served", code, 200)
code, _, _ = call("POST", "/mcp", init, headers={**mcp_headers, "Host": "api.ztljudge.com"}, ip="10.0.0.3")
check("Host api.ztljudge.com is served (the new name, 2026-10-30)", code, 200)
check("an older client's handshake (2025-06-18) is answered in its version",
      (body if isinstance(body, dict) else {}).get("result", {}).get("protocolVersion"), "2025-06-18")
code, _, _ = call("POST", "/mcp", init, headers={**mcp_headers, "Host": "evil.example"}, ip="10.0.0.3")
check("any other Host is refused", code in (400, 403, 421), True)

print("\nthe two checkers (2026-11-05)")
code, _, body = call("POST", "/steps", {"equations": ["(x^2-1)/(x-1) = 2", "x^2-1 = 2*(x-1)"]}, ip="10.0.5.1")
check("POST /steps: clearing the denominator gains x = 1", (code, body["verdict"], body["steps"][0]["gained"][0]["x"]),
      (200, "REFUTED", "1"))
code, _, body = call("POST", "/steps", {"equations": ["1^100000000 = 1", "x = 1"]}, ip="10.0.5.1")
check("POST /steps: a power above the limit is refused at once, by form", (code, body["ok"], bool(body["refused"])),
      (200, False, True))
code, _, body = call("POST", "/naive", tools.checker_examples()["naive"]["a web of trust (keys that sign each other)"], ip="10.0.5.1")
check("POST /naive: the web of trust — 'every key trusted or not' EARNED IF STABLE",
      (code, body["claims"][1]["disposition"], body["claims"][1]["residue"]),
      (200, "EARNED IF STABLE", ["mallory1", "mallory2"]))
code, _, body = call("POST", "/naive", {"sets": {"A": {"abs": ["in", "x", "ghost"]}}}, ip="10.0.5.1")
check("POST /naive: an unknown name is refused (no subject)", (code, body["ok"]), (200, False))
code, _, body = call("GET", "/checker-examples", ip="10.0.5.1")
check("GET /checker-examples: the kernels' own", (code, sorted(body)), (200, ["naive", "steps"]))

print("\nMCP with the official client")


async def official():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    async with streamable_http_client(BASE + "/mcp") as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            names = sorted(t.name for t in (await s.list_tools()).tools)
            r = await s.call_tool("judge", {"document": LIAR})
            lang = await s.call_tool("language", {})
            st = await s.call_tool("check_steps", {"request": {"equations": ["x^2 = x", "x = 1"]}})
            nv = await s.call_tool("judge_naive", {"universe": tools.checker_examples()["naive"][
                "Russell and the truth-teller set"]})
            return names, r, lang, st, nv


names, r, lang, st, nv = asyncio.run(official())
check("six tools", names, ["check_steps", "checker_examples", "examples", "judge", "judge_naive", "language"])
_sc = lambda x: getattr(x, "structured_content", None) or getattr(x, "structuredContent", None)
check("check_steps over MCP: cancelling x loses x = 0", (_sc(st)["verdict"], _sc(st)["steps"][0]["lost"][0]["x"]),
      ("REFUTED", "0"))
check("judge_naive over MCP: R∈R is a PARADOX", [c["disposition"] for c in _sc(nv)["claims"]], ["EARNED", "OPEN", "OPEN"])
structured = getattr(r, "structured_content", None) or getattr(r, "structuredContent", None)
check("judge over MCP: the liar is UNREDEEMABLE", structured["report"]["receipt"]["verdict"]["credit"], "UNREDEEMABLE")
check("MCP answers what REST answers", structured["report"], tools.judge(LIAR)["report"])
lang_s = getattr(lang, "structured_content", None) or getattr(lang, "structuredContent", None)
check("language over MCP", len(lang_s["row_columns"]), 13)

print("\nthe service-wide cap, across different addresses")


async def _asgi(app, ip):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        sent.append(m)
    scope = {"type": "http", "method": "GET", "path": "/language", "client": (ip, 1), "headers": []}
    await app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


async def _ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})

limited = server.RateLimit(_ok, per_minute=100, total_per_minute=3)
codes = [asyncio.run(_asgi(limited, f"10.1.0.{i}")) for i in range(4)]
check("three pass from three addresses, the fourth address is refused", codes, [200, 200, 200, 429])

print("\nthe request log: when, who, what — not the answer")
lines = [json.loads(x) for x in open(LOG, encoding="utf-8") if x.strip()]
raw = open(LOG, encoding="utf-8").read()
import hashlib                                                            # noqa: E402
liar_fp = hashlib.sha256(json.dumps(LIAR, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
jl = [x for x in lines if x["path"] == "/judge" and ((x.get("what") or {}).get("document") or {}).get("sha256") == liar_fp]
check("a /judge request is logged with the client's address and the document's FINGERPRINT (SHA-256, size)",
      bool(jl) and jl[0]["ip"] == "10.0.0.1" and "ua" in jl[0] and "ts" in jl[0] and jl[0]["what"]["document"]["bytes"] > 0, True)
calls = [c for x in lines if x["path"].startswith("/mcp") for c in (x.get("what") or {}).get("mcp", [])]
check("an MCP tool call is logged with the tool and the fingerprint of its arguments",
      any(c.get("tool") == "judge" and "sha256" in (c.get("arguments") or {}) for c in calls), True)
liar_text = json.dumps(LIAR, ensure_ascii=False)
check("NO document text in the log: not the liar's rows, not its claim (data minimisation, 2026-10-10)",
      any(r.get("name", "") and f'"{r["name"]}"' in raw and r.get("means", "") in raw for r in LIAR.get("rows", []))
      or (LIAR.get("claim") and f'"claim": "{LIAR["claim"]}"' in raw), False)
check("an MCP handshake is logged with the client's own name",
      any(c.get("rpc") == "initialize" and (c.get("client") or {}).get("name") == "raw-stand" for c in calls), True)
check("the answer is not logged: no report, no receipt, no verdict",
      ('"report"' in raw, '"receipt"' in raw, "UNREDEEMABLE" in raw), (False, False, False))
check("an oversize request is logged by its size, not its content",
      any((x.get("what") or {}).get("too_large_bytes", 0) > server.MAX_BODY for x in lines), True)
check("a rate-limited request is logged with its 429", any(x["status"] == 429 for x in lines), True)
check("preflight requests are not logged", any(x["method"] == "OPTIONS" for x in lines), False)

d = tempfile.mkdtemp(prefix="ztl-api-rot-")
rot = server.request_logger(os.path.join(d, "r.jsonl"), days=2, when="S")    # a 'day' of one second
for i in range(5):
    rot.info(json.dumps({"i": i}))
    time.sleep(1.1)
rot.info(json.dumps({"i": "last"}))
files = sorted(os.listdir(d))
check("the log is bounded by AGE: rotated files older than `days` intervals are deleted (6 'days' written)",
      len(files) <= 3, True)
stale = os.path.join(d, "r.jsonl.2026-01-01_00-00-00")
open(stale, "w").write("{}\n")
server.request_logger(os.path.join(d, "r.jsonl"), days=2, when="S")
check("a stale rotated file left from before is swept at start (a quiet service still keeps the promise)",
      os.path.exists(stale), False)
h = server.request_logger(LOG + ".probe").handlers[0]
check("in service: one file a day (UTC midnight), deleted after 30 days by age",
      (type(h).__name__, h.when, h.days, h.utc), ("AgedRotatingHandler", "MIDNIGHT", 30, True))

srv.should_exit = True
print(f"\nJUDGE SERVICE {'GREEN' if not fail else 'RED'}: {ok} OK, {fail} FAIL")
raise SystemExit(1 if fail else 0)
