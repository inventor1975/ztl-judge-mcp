"""Stand for the Space's own layer (tools.py), run locally without Gradio.

    python3 -B test_tools.py
"""
import json
import py_compile
import re
from pathlib import Path

import tools
import zfl

HERE = Path(__file__).resolve().parent
CYR = re.compile("[А-Яа-яЁё]")
ok = fail = 0


def check(name, got, want):
    global ok, fail
    if got == want:
        ok += 1; print(f"  OK   {name}")
    else:
        fail += 1; print(f"  FAIL {name}: got {got!r}, want {want!r}")


print("the judge is the vendored studio, not a copy found elsewhere on the path")
check("zfl comes from this folder", Path(zfl.__file__).resolve().parent, HERE)

print("\nlanguage")
lang = tools.language()
check("13 row columns", len(lang["row_columns"]), 13)
check("7 document fields", [f["key"] for f in lang["document_fields"]], ["grounds", "claim", "bounds", "sets", "need", "independent", "ask"])
check("the status options", next(c for c in lang["row_columns"] if c["key"] == "status")["options"],
      ["verified", "refuted", "unverified", "defined"])
check("the limit on names", (lang["max_names"], lang["max_repeated_names"]), (64, 10))
check("all of it in English", bool(CYR.search(json.dumps(lang, ensure_ascii=False))), False)

print("\nexamples")
ex = tools.examples()
check("all 56 of the studio's examples, in English", len(ex), 56)
by_title = {e["en"]: e["doc"] for e in __import__("zflexamples").EXAMPLES}
same_verdict = sum(json.dumps(zfl.run(e["document"]).get("report"), sort_keys=True, default=str)
                   == json.dumps(zfl.run(by_title[e["title"]]).get("report"), sort_keys=True, default=str)
                   for e in ex)
check("the English descriptions change no report: 56 of 56 byte-identical to the studio's own", same_verdict, 56)
check("all of them in English", bool(CYR.search(json.dumps(ex, ensure_ascii=False))), False)
same = sum(json.dumps(tools.judge(json.dumps(e["document"])).get("report"), sort_keys=True, default=str)
           == json.dumps(zfl.run(e["document"]).get("report"), sort_keys=True, default=str) for e in ex)
check("every example: judge() reports exactly what the studio's zfl.run reports", same, len(ex))
answers = {((tools.judge(json.dumps(e["document"])).get("report") or {}).get("judge") or {}).get("verdict")
           for e in ex} - {None}
check("every verdict is T or F", answers <= {"T", "F"}, True)

print("\njudge")
liar = {"claim": "L", "rows": [{"name": "L", "means": "this sentence is false",
                                "status": "defined", "ground": "~Tr(L)"}]}
r = tools.judge(json.dumps(liar))
check("the liar carries UNREDEEMABLE (the studio's own smoke check)",
      r["report"]["receipt"]["verdict"]["credit"], "UNREDEEMABLE")
check("a dict and its JSON text are judged alike",
      json.dumps(tools.judge(liar)["report"], sort_keys=True), json.dumps(r["report"], sort_keys=True))
bad = tools.judge("{not json")
check("broken JSON is refused as an issue, not raised", (bad["ok"], bad["issues"][0]["code"]), (False, "E_JSON"))
check("the back-reading facts carry English keys only",
      [k for k in r["back_reading_facts"] if CYR.search(k)], [])
check("the report names the studio commit it ran", r["studio_commit"], "02f908c")
# degrees (2026-11-05) reach the service: both sides — no degree, no section; degrees -> the Fréchet interval + threshold
_A = {"name": "a", "status": "verified", "ground": "m", "degree": "0.3", "means": "a"}
_B = {"name": "b", "status": "verified", "ground": "m", "degree": "0.7", "means": "b"}
check("no degree -> no degree section", "degree" in tools.judge({"rows": [dict(_A, degree="")], "claim": "a"})["report"],
      False)
_d = tools.judge({"rows": [_A, _B], "claim": "a | b", "need": "0.6"})["report"]["degree"]
check("degrees through the service: a|b in [7/10, 1], EARNED at 0.6", (_d["interval"], _d["verdict"]),
      (["7/10", "1"], "EARNED"))
stab = tools.judge({"claim": "p & ~p", "rows": [{"name": "p", "means": "it rains", "status": "unverified"}]})
check("REFUTED IF STABLE carries its meaning and names what to check (29.10)",
      (stab["answer"]["disposition"], stab["answer"]["meaning"].startswith("false in every completion"), stab["answer"]["to_check"]),
      ("REFUTED IF STABLE", True, ["p"]))

print("\nthe answer field (blind cloud test, 29.09)")
L = tools.language()
check("language documents | and the precedence", ("|" in L["operators"], "left-associative" in L["operators"]["precedence"]), (True, True))
check("language says ? is solved for, not 'unknown'", "SOLVES" in L["values"] and "[0,inf]" in L["values"], True)
check("language tells refuted from unverified", "known NOT done" in L["status_guide"], True)
defd = {"rows": [{"name": "lawyer", "status": "verified", "ground": "memo"}, {"name": "payment", "status": "unverified"},
                 {"name": "may_sign", "status": "defined", "ground": "Tr(lawyer) & Tr(payment)"}], "claim": "may_sign"}
a = tools.judge(defd)["answer"]
check("to_check names the INPUT, not the defined row", a["to_check"], ["payment"])
a = tools.judge(liar)["answer"]
check("a paradox: checking cannot settle it, and nothing is sent to be checked",
      (a["settle_by_checking"], a["to_check"], a["meaning"].startswith("no truth value")), (False, [], True))
a = tools.judge({"rows": [{"name": "tests", "status": "verified", "ground": "ci"}, {"name": "qa", "status": "unverified"}],
                 "claim": "tests | qa"})["answer"]
check("settled for good: the unverified are listed as irrelevant, not to check",
      (a["disposition"], a["to_check"], a.get("unverified_but_irrelevant")), ("EARNED", [], ["qa"]))
num = {"rows": [{"name": "line", "status": "unverified", "value": "[0,inf]", "unit": "EUR"},
                {"name": "budget", "status": "verified", "ground": "order", "value": "2000", "unit": "EUR"}], "claim": "line <= budget"}
a = tools.judge(num)["answer"]
check("numeric: one place for the verdict, the internal atom named by its comparison, the input to check",
      (a["source"], a["disposition"], a["comparisons"], a["to_check"]), ("numeric", "OPEN", {"nc1": "line <= budget"}, ["line"]))
big = {"rows": [{"name": f"c{i}", "status": "verified", "ground": "log"} for i in range(11)] + [{"name": "load", "status": "unverified"}],
       "claim": " & ".join([f"c{i}" for i in range(11)] + ["load"])}
r = tools.judge(big)
qn = {"rows": [{"name": "line", "status": "unverified", "value": "?", "unit": "EUR"},
               {"name": "budget", "status": "verified", "ground": "order", "value": "2000", "unit": "EUR"}], "claim": "line <= budget"}
r = tools.judge(qn)
check("? solved: the core warns W_SOLVED and the answer says which values, not the actual one",
      ("W_SOLVED" in [i["code"] for i in r["issues"]], r["answer"]["meaning"].startswith("the ANSWER to 'which values of line")), (True, True))
check("the value column no longer says '? for unknown'",
      "? for unknown" in [c for c in tools.language()["row_columns"] if c["key"] == "value"][0]["description"], False)
r = tools.judge(big)
# 2026-09-29: the cap counts REPEATED atoms (10) and all atoms (64); twelve atoms named
# once are no longer over it, so the conjunction is judged whole, with no split.
check("12 atoms named once: judged whole, no split needed",
      (r["ok"], r["answer"].get("parts"), r["answer"]["disposition"], r["answer"]["to_check"]), (True, None, "OPEN", ["load"]))
twice = {"rows": big["rows"], "claim": " & ".join([f"(c{i} & c{i})" for i in range(11)] + ["(load & load)"])}
r = tools.judge(twice)
check("12 REPEATED atoms in a conjunction: judged by the split, not refused",
      (r["ok"], r["answer"]["parts"], r["answer"]["disposition"], r["answer"]["to_check"]), (True, 12, "OPEN", ["load"]))
shared = {"rows": [{"name": f"s{i}", "status": "unverified"} for i in range(12)],
          "claim": " & ".join(f"(s{i} | s{(i + 1) % 12})" for i in range(12))}
check("a connected group over the cap is still refused", tools.judge(shared)["ok"], False)
eqd = {"rows": [{"name": f"e{i}", "status": "unverified"} for i in range(12)],
       "claim": "e0 = " + " & ".join(f"e{i}" for i in range(1, 12))}
r = tools.judge(eqd)
check("a top-level = over 12 atoms named once: judged whole (under the cap since 29.09)",
      (r["ok"], r.get("answer", {}).get("parts")), (True, None))
eqd2 = {"rows": eqd["rows"], "claim": "e0 = " + " & ".join(f"(e{i} & e{i})" for i in range(1, 12))}
check("a top-level = is not split (it binds weaker than &): 11 repeated atoms refused",
      tools.judge(eqd2)["ok"], False)

print("\nthe service file compiles (its MCP SDK is tested by test_api.py)")
py_compile.compile(str(HERE / "server.py"), doraise=True)
check("server.py compiles", True, True)

print(f"\nSPACE TOOLS {'GREEN' if not fail else 'RED'}: {ok} OK, {fail} FAIL")
raise SystemExit(1 if fail else 0)
