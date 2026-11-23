"""What an agent needs from the ZTL Studio, as three plain functions.

No model runs here. The caller's own AI turns a question into a ZFL table;
these functions give it the language, the worked examples and the verdict.
The verdict is the studio's own `zfl.run`: the files next to this one are
github.com/inventor1975/ztlstudio at the commit named in STUDIO_COMMIT,
copied unchanged.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import backread
import zfl
import zflexamples
import znaive          # noqa: E402  (ztlcore/ is on the path through zfl)
import zsteps          # noqa: E402

STUDIO_COMMIT = "28ed15c"
_CYRILLIC = re.compile("[А-Яа-яЁё]")


def _english(obj) -> bool:
    return not _CYRILLIC.search(json.dumps(obj, ensure_ascii=False))


def _field(spec: dict) -> dict:
    label, description = spec["en"]
    out = {"key": spec["key"], "label": label, "description": description,
           "required": bool(spec.get("required"))}
    if spec.get("options"):
        out["options"] = list(spec["options"])
    examples = [e for e in (spec.get("eg") or []) if _english(e)]
    if examples:
        out["examples"] = examples
    return out


# From the studio's zfldoc.PRECEDENCE (en) and SPEC.md "Reading the text: precedence".
_PRECEDENCE = ("Binding, strongest first: ~, then & > | > ^ > -> > = (also <->). Every connective is "
               "left-associative: a & b | c is (a & b) | c, and a -> b -> c is (a -> b) -> c, NOT "
               "a -> (b -> c). Write the brackets when you mean the other reading.")


def language() -> dict:
    """The ZFL language: how to write a question as a table ZTL can judge.

    A ZFL document is JSON with "rows" (one per named statement: its name,
    what it means, its status and the ground it stands on) and a "claim"
    (a formula over the row names, e.g. "a & (b -> c)"). ZTL has three
    values and two-valued connectives: a compound claim is always judged
    T or F; a claim that is a single name answers with that name's own
    value, which is Z while it stays unverified or rests on something
    unverified. Put the formula itself in the claim for a T/F verdict.

    Returns:
        The row columns and document fields with their meaning, allowed
        options and examples, and the limit on names per document.
    """
    return {"row_columns": [_field(c) for c in zfl.COLUMNS],
            "document_fields": [_field(f) for f in zfl.DOC_FIELDS],
            "max_names": zfl.MAX_DISTINCT_ATOMS,
            "max_repeated_names": zfl.MAX_ATOMS,
            "answer": "A compound claim: T or F. A claim that is a single name: that name's own value, Z while it is unverified or rests on something unverified.",
            # BLIND TEST 29.09: a stranger's AI read "F" as "false" and could not
            # tell what what_to_check meant; `ask` promised a narrowing the API
            # never does. Said here, where the stranger reads.
            "reading": "Read the verdict WITH its disposition: T EARNED = established; F REFUTED = false; F OPEN or Z OPEN = NOT ESTABLISHED, it could still turn either way (do not report it as false) — `why` and `unverified` say what to check; ON CREDIT = holds only on an unverified ground. A compound claim gets T or F; a claim that is a single name gets that name's own value, Z while unverified.",
            "what_to_check": "Minimal sets of unverified names to check, per goal: EARNED (make it established), REFUTED (make it false), SETTLED (decide it either way). 'guaranteed': once the set is checked the goal is reached whatever the checks show; 'possible': checking the set can reach the goal, depending on what they show; 'already': the goal holds now. An empty family is explained by *_none: true = no such set exists, null = the search stopped early (not searched to the end).",
            "ask_note": "The API always returns the full report; `ask` does not narrow it here.",
            # BLIND TEST, CLOUD, 20 QUESTIONS (29.09): a fresh model guessed `|`,
            # read `?` as "unknown" and got a confident wrong yes, could not tell
            # refuted from unverified, and did not know the limit counts atoms.
            "answer_field": "Every judge result carries `answer`: verdict, disposition, what it means in words, what to check (the unverified INPUTS, not defined rows), and whether checking can settle it at all. Read `answer` first.",
            "operators": {"~": "not", "&": "and", "|": "or", "^": "exactly one of",
                          "->": "if ... then (material: false only when the left holds and the right fails; 'A only if B' is A -> B, 'A if B' is B -> A)",
                          "= or <->": "same truth value (between rows)", "Tr(x)": "the value of row x, used inside a defined row's ground",
                          "<= >= < > ==": "numeric comparisons, for rows with a value",
                          "+ - * /": "arithmetic inside a comparison",
                          "precedence": _PRECEDENCE},
            # FUNCTIONS (2026-10-20, studio 6ae9ba8): the list is the language's own (zfl.FUNCTION_WORDS).
            "functions": {"names": list(zfl.FUNCTION_WORDS) + ["sqrt", "sum", "min", "max", "abs"],
                          "note": "Inside a numeric comparison. exp, ln, log10, sin, cos, tan, atan (radians), pow, "
                                  "sqrt: each value a PROVED bracket of two rationals, never a rounding. pi: the "
                                  "constant as a proved bracket — an equality with pi is never forced. "
                                  "integral(f, x, a, b): the definite integral of f over x from a to b; x is the "
                                  "variable of integration, NOT a row (and must not be a row's name); a and b may be "
                                  "rows with ranges; the value is a kernel-checked enclosure valid for EVERY value of "
                                  "the rows it reads; no certificate found = OPEN, never a guess. To get a number, "
                                  "give a row value ? and claim  E == integral(...): the judge solves E's range."},
            "status_guide": "verified: a document shows it TRUE (name the document in ground). refuted: a document shows it FALSE, including a thing known NOT done or known absent (the log shows the test was not run; the scan shows no third signature). unverified: nobody has checked. defined: a formula over other rows; read row x as Tr(x).",
            "values": "value is a number or an interval [lo,hi] (inf allowed: [0,inf]). `?` does NOT mean 'unknown fact': it marks the quantity the question ASKS FOR, and the judge SOLVES for it (reports the range that makes the claim true). For a quantity that exists but is not known yet, give the range it can lie in, e.g. [0,inf]; the verdict then stays open until it is measured.",
            "limits": f"At most {zfl.MAX_ATOMS} plain atoms that occur MORE THAN ONCE (counted over the claim and the grounds of defined rows; each Tr(x) read counts), at most {zfl.MAX_DISTINCT_ATOMS} plain atoms in all, and {zfl.MAX_COMPARISONS} comparisons. An atom named once costs almost nothing; only repeated atoms make the exact guarantee branch. A claim that is a conjunction (a & b & ...) is split by this API into independent groups automatically, so the limit applies per group of conjuncts that share names. Rows themselves are not limited.",
            "claims_note": "Without a claim only the passport of self-reference is computed. For a verdict, put a formula in claim (for a paradox, e.g. the name of the paradoxical row).",
            "studio_commit": STUDIO_COMMIT}


# THE STUDIO WRITES 26 OF ITS 48 EXAMPLE DOCUMENTS IN RUSSIAN — only the
# `means` of their rows (58 strings; names, grounds and claims are Latin).
# `means` is a description: the judge never reads it and the receipt never
# carries it, so an English `means` changes no verdict. test_tools.py proves
# that per example, report against report, to the byte. An example with any
# string missing from the table is left out rather than shown half-translated.
_MEANS_EN = json.loads((Path(__file__).resolve().parent / "examples_en.json").read_text(encoding="utf-8"))


def english_document(doc: dict):
    """The document with its row descriptions in English, or None if one has no translation."""
    out = copy.deepcopy(doc)
    for row in out.get("rows", []):
        means = row.get("means")
        if isinstance(means, str) and _CYRILLIC.search(means):
            if means not in _MEANS_EN:
                return None
            row["means"] = _MEANS_EN[means]
    return out if _english(out) else None


def examples() -> list:
    """Worked examples: questions already written as ZFL documents.

    Returns:
        A list of examples, each with its title, kind (paradox, everyday,
        audit, numbers, clock), the question in words, and the ZFL document.
    """
    out = []
    for e in zflexamples.EXAMPLES:
        doc = english_document(e["doc"])
        if doc is not None:
            out.append({"title": e["en"], "kind": e["kind"], "question": e.get("ask_en", ""),
                        "document": doc})
    return out


def judge(document: str) -> dict:
    """Judge a ZFL document with ZTL and return the studio's report.

    Args:
        document: The ZFL document as JSON text, e.g.
            {"rows": [{"name": "p", "means": "...", "status": "verified",
            "ground": "doc-1"}], "claim": "p"}. Call `language` for the
            columns and `examples` for complete documents.

    Returns:
        The report: the verdict with its disposition and grade, the
        receipt, which instruments applied, the issues found in the table,
        and what the core read from it. Read the verdict WITH its disposition: T EARNED = established; F REFUTED = false; F OPEN or Z OPEN = NOT ESTABLISHED, it could still turn either way (do not report it as false) — `why` and `unverified` say what to check; ON CREDIT = holds only on an unverified ground. A compound claim gets T or F; a claim that is a single name gets that name's own value, Z while unverified.
    """
    try:
        doc = json.loads(document) if isinstance(document, str) else document
    except json.JSONDecodeError as e:
        return {"ok": False, "issues": [{"level": "error", "code": "E_JSON", "where": "document",
                                         "hint": f"the document is not valid JSON: {e}"}]}
    result = zfl.run(doc)
    base = result                                  # the judgement, reused by the back-reading below (2026-11-08)
    if isinstance(doc, dict) and not result.get("ok"):
        split = _judge_split(doc, result)
        if split is not None:
            result = split
    try:
        result["answer"] = _answer(doc, result)
    except Exception as e:                        # the summary never takes the report down
        result["answer"] = {"error": f"{type(e).__name__}: {e}"}
    facts = backread.факты(doc, base if result is base else None)   # the studio's own back-reading, as facts
    if isinstance(facts, dict) and "связь_мимо_приборов" in facts:
        facts["relations_no_instrument_reads"] = facts.pop("связь_мимо_приборов")
    result["back_reading_facts"] = facts
    result["studio_commit"] = STUDIO_COMMIT
    return result


# --------------------------------------------------------------- for an AI
# THE ANSWER FIELD (29.09, blind cloud test of 20 questions). A fresh model
# read `F` as false, listed a DEFINED row as the thing to verify (it cannot be
# verified), was told to "verify" a paradox (refusal is permanent), found the
# numeric verdict in a different place, and saw an internal name `nc1`. The
# core stays as it is; this is one place where an AI can read what it means.
_MEANING = {
    "EARNED": "established",
    "REFUTED": "false",
    "OPEN": "NOT established: it could still turn either way; do not report it as false",
    "ON CREDIT": "holds only on an unverified ground",
    # 2026-10-29: the judge's two new dispositions. Without these the answer said "no verdict: see issues"
    # beside a disposition the judge had given (measured on the live API, p & ~p).
    "EARNED IF STABLE": ("true in every completion: established if each name in `to_check` reads the same at every occurrence (no "
                         "change between check and use; the same meaning in premise and conclusion); read each "
                         "occurrence on its own and it is NOT established — check them to settle it"),
    "REFUTED IF STABLE": ("false in every completion: refuted if each name in `to_check` reads the same at every occurrence; read each "
                          "occurrence on its own and it is NOT refuted — check them to settle it"),
    "E": "cannot be judged: the document has no admissible reading (see issues)",
}
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _answer(doc, result):
    rep = (result or {}).get("report") or {}
    j, src = rep.get("judge") or {}, "judge"
    if not j and rep.get("numeric"):
        j, src = rep["numeric"], "numeric"
    rows = {r.get("name"): r for r in (doc.get("rows") or []) if isinstance(r, dict)}
    try:
        from ztlcore import znumjudge
        _, nat = znumjudge.extract_comparisons(doc.get("claim") or "", {}, parse=False)
        comparisons = {k: v[3].strip() for k, v in nat.items()}
    except Exception:
        comparisons = {}

    to_check, seen = [], set()

    def add(n):
        if n not in seen:
            seen.add(n)
            to_check.append(n)

    def inputs(n, walked):
        if n in walked:
            return
        walked.add(n)
        r = rows.get(n)
        if r is None:
            if n in comparisons:                     # nc<i>: say the comparison, check its inputs
                for m in _NAME.findall(comparisons[n]):
                    if (rows.get(m) or {}).get("status") == "unverified":
                        add(m)
            return
        if r.get("status") == "defined":
            for m in zfl._reading_atoms(r.get("ground") or "")[0]:
                inputs(m, walked)
        elif r.get("status") == "unverified":
            add(n)

    for n in j.get("unverified") or []:
        inputs(n, set())
    kinds = {k: (v or {}).get("kind") for k, v in (rep.get("passport_rows") or {}).items()}
    settle, why_not = True, None
    if "PARADOX" in kinds.values():
        settle, why_not = False, ("a paradox: no consistent truth value; the refusal is permanent and "
                                  "no check can settle it (only redefining the sentence can)")
    elif "UNDERDETERMINED" in kinds.values():
        settle, why_not = False, ("underdetermined: both truth values are consistent; no check settles it, "
                                  "only a stipulation (a choice you declare) can")
    d = j.get("disposition")
    meaning = _MEANING.get(d, "no verdict: " + ("no claim was given; only the passport was computed"
                                                 if not (doc.get("claim") or "").strip() else "see issues"))
    irrelevant = []
    solved = [i.get("where") for i in (result.get("issues") or []) if i.get("code") == "W_SOLVED"]
    if solved and d == "EARNED":
        who = ", ".join(map(str, solved))
        meaning = (f"the ANSWER to 'which values of {who} satisfy the claim' is established (see `solved`); "
                   f"it does NOT say the claim holds for the actual {who} — if {who} is a real input not "
                   f"known yet, give its range, e.g. [0,inf] (W_SOLVED)")
    if settle is False:
        meaning, irrelevant, to_check = "no truth value: " + why_not, to_check, []
    elif d in ("EARNED", "REFUTED") and j.get("grade") == "hereditary" and not solved:
        # Settled already: the unverified names cannot change it. Listing them
        # as "to check" sent a reader to work that changes nothing (29.09).
        irrelevant, to_check = to_check, []
    out = {"verdict": j.get("verdict"), "disposition": d, "grade": j.get("grade"),
           "meaning": meaning, "to_check": to_check, "settle_by_checking": settle, "source": src}
    if irrelevant:
        out["unverified_but_irrelevant"] = irrelevant
    if why_not:
        out["why_not"] = why_not
    if comparisons:
        out["comparisons"] = comparisons
    if result.get("split"):
        out["parts"] = result["split"]["parts"]
    return out


# NO CEILING ON A CONJUNCTION (29.09; ported from an earlier checker, where
# it was MEASURED: split == whole on 1 234 random documents for verdict,
# disposition, grade and unverified). Only along a top-level &, only into
# groups that share no name (directly or through a defined row), only when the
# document failed on the atom cap alone, and not for numeric rows.
_TOP_OTHER = re.compile(r"\||->|<->|\^|==|!=|<=|>=|<|>|\+|\*|/|=")
MAX_PARTS = 1000
_GRADE = ["hereditary", "sound", "until-verification"]


def _split_and(claim):
    depth, cur, out, flat = 0, "", [], ""
    for ch in claim:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "&" and depth == 0:
            out.append(cur.strip())
            cur = ""
            flat += " "
            continue
        cur += ch
        if depth == 0 and ch != ")":
            flat += ch
    out.append(cur.strip())
    if len(out) < 2 or any(not c for c in out) or _TOP_OTHER.search(flat):
        return None
    return out


def _groups(doc):
    rows = [r for r in (doc.get("rows") or []) if isinstance(r, dict)]
    if any(str(r.get("value", "")).strip() for r in rows):
        return None
    conj = _split_and(doc.get("claim") or "")
    if conj is None:
        return None
    by_name = {r.get("name"): r for r in rows}

    def closure(text):
        seen, todo = set(), set(zfl._reading_atoms(text)[0])
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            r = by_name.get(n)
            if r and r.get("status") == "defined":
                todo |= set(zfl._reading_atoms(r.get("ground") or "")[0])
        return seen

    cl = [closure(c) for c in conj]
    parent = list(range(len(conj)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    owner = {}
    for i, names in enumerate(cl):
        for n in names:
            if n in owner:
                parent[find(i)] = find(owner[n])
            else:
                owner[n] = i
    groups = {}
    for i in range(len(conj)):
        groups.setdefault(find(i), []).append(i)
    if len(groups) < 2 or len(groups) > MAX_PARTS:
        return None
    out = []
    for idx in groups.values():
        names = set().union(*(cl[i] for i in idx))
        out.append({"rows": [by_name[n] for n in sorted(names) if n in by_name],
                    "claim": " & ".join(f"({conj[i]})" for i in idx)})
    return out


def _combine(parts):
    disp = [p.get("disposition") for p in parts]
    if "E" in disp:
        d = "E"
    elif "REFUTED" in disp:
        d = "REFUTED"
    elif "REFUTED IF STABLE" in disp:          # 2026-10-29: the parts share no name, so the condition carries over
        d = "REFUTED IF STABLE"
    elif all(x == "EARNED" for x in disp):
        d = "EARNED"
    elif all(x in ("EARNED", "EARNED IF STABLE") for x in disp):
        d = "EARNED IF STABLE"
    elif all(p.get("verdict") == "T" for p in parts):
        # 2026-10-29 (evening, IF STABLE by completions): an EARNED IF STABLE part may carry the verdict F (p -> p),
        # so ON CREDIT is "the conjunction's verdict is T", not "every part is in the T-ish dispositions"
        d = "ON CREDIT"
    else:
        d = "OPEN"
    if d in ("REFUTED", "REFUTED IF STABLE"):
        pool, pick = [p.get("grade") for p in parts if p.get("disposition") == d], min
    else:
        pool, pick = [p.get("grade") for p in parts], max
    known = [x for x in pool if x in _GRADE]
    g = pick(known, key=_GRADE.index) if known and len(known) == len(pool) else "/".join(sorted(set(map(str, pool))))
    return {"verdict": "T" if all(p.get("verdict") == "T" for p in parts) else "F",
            "disposition": d, "grade": g,
            "unverified": sorted({u for p in parts for u in p.get("unverified") or []})}


def _judge_split(doc, failed):
    errors = [i for i in failed.get("issues") or [] if i.get("level") == "error"]
    if not errors or any(i.get("code") != "E_TOOBIG" for i in errors):
        return None
    groups = _groups(doc)
    if groups is None:
        return None
    judged = []
    for g in groups:
        r = zfl.run(g)
        if not r.get("ok"):
            return None                     # a group over the cap: the original refusal stands
        judged.append({"claim": g["claim"], "judge": (r.get("report") or {}).get("judge") or {}})
    return {"ok": True, "issues": [i for i in failed.get("issues") or [] if i.get("level") != "error"],
            "report": {"judge": _combine([p["judge"] for p in judged]), "parts": judged},
            "split": {"parts": len(judged),
                      "note": "the claim is a conjunction over the atom cap; it was judged as independent "
                              "groups sharing no name and combined (EARNED iff every part is, REFUTED iff one is)"}}


# ------------------------------------------------------------------ two more checkers (2026-11-05, ZTL @ 4fb971a)
# The kernels' own JSON doors, unchanged: an unreadable or oversized request is refused with the reason, by the
# form of the request (the same request always gets the same answer). The studio serves the same at /tools.

def check_steps(request) -> dict:
    """The price of each step of an algebra derivation, judged by solution sets, exactly.

    Args:
        request: {"equations": ["x^2 = x", "x = 1"], "var": "x", "domain": [lo, hi] or null,
            "domain_verified": true} — each equation the next step; one unknown; + - * / ^ (whole powers).

    Returns:
        verdict of the chain (its weakest step) and every step: EARNED (same solutions), ON CREDIT (same, on an
        unverified domain), REFUTED (a solution lost or gained — the witness and the cause), E (divides by an
        expression that is 0 for every value: nothing to check).
    """
    try:
        req = json.loads(request) if isinstance(request, str) else request
    except (ValueError, RecursionError):
        return {"ok": False, "refused": "the request is not valid JSON"}
    r = zsteps.check(req)
    r["studio_commit"] = STUDIO_COMMIT
    return r


def judge_naive(universe) -> dict:
    """Sets defined by a property, self-reference allowed: which memberships the definitions ground, a passport
    for each one they leave undecided (PARADOX / UNDERDETERMINED / INTRINSIC / INPUT / DOWNSTREAM), and the
    judge's disposition for each claim.

    Args:
        universe: {"sets": {name: {"list": [...]} | {"data": {name: "T"|"F"|"Z"}} | {"abs": formula}},
            "claims": [formula, ...]}; formulas ["in", a, S], ["not", f], ["and"|"or"|"imp"|"iff", f, g],
            ["all"|"ex", "y", f], ["eq", S, T]; the bound variable of an abs set is "x".
    """
    try:
        spec = json.loads(universe) if isinstance(universe, str) else universe
    except (ValueError, RecursionError):
        return {"ok": False, "refused": "the universe is not valid JSON"}
    r = znaive.check(spec)
    r["studio_commit"] = STUDIO_COMMIT
    return r


def checker_examples() -> dict:
    return {"steps": zsteps.EXAMPLES, "naive": znaive.EXAMPLES}
