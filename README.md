# ZTL Judge — MCP server

The judge of **ZTL (Zero-Trust Logic)** as an MCP server, the code behind the hosted endpoint

    https://api.ztljudge.com/mcp        (streamable HTTP, no sign-in)

An agent writes a claim as a ZFL table (its grounds, which of them are verified, the claim) and the judge answers
with a verdict and a disposition — EARNED, EARNED IF STABLE, ON CREDIT, OPEN, REFUTED IF STABLE, REFUTED — and what is
still unverified. Nothing here calls a language model: every answer is computed by the ZTL core in `ztlcore/`.

## Tools

| tool | what it does |
|---|---|
| `language` | the ZFL language: columns, statuses, how to write a claim |
| `examples` | worked ZFL documents |
| `judge` | judge one ZFL document: verdict, disposition, unverified grounds, why |
| `check_steps` | check a chain of algebra derivation steps |
| `judge_naive` | judge claims about sets defined by a property |
| `checker_examples` | examples for the two checkers above |

The same functions are also served over REST (`GET /language`, `GET /examples`, `POST /judge`, `POST /steps`,
`POST /naive`, `GET /checker-examples`, `GET /health`). Signed verdicts for external verifiers live under
`/signed/v1/*` and `/cage/v1/*`; their terms are in `cage_terms.md`.

## Run it yourself

    pip install -r requirements.txt
    python3 server.py              # listens on 127.0.0.1:8230; MCP at /mcp

Optional environment: `ZTL_API_HOST`, `ZTL_API_HOSTS`, `PORT`, `ZTL_API_RATE`, `ZTL_API_RATE_TOTAL`, `ZTL_API_LOG`
(unset = no request log), `ZTL_CAGE_KEY_FILE` (Ed25519 key for signed receipts; without it the signed paths answer 503),
`ZTL_CONTACT_SECRET_FILE`, `ZTL_CONTACT_LOG`. No secrets are in this repository.

Tests: `python3 test_tools.py`, `python3 test_api.py`, `python3 test_cage.py`, `python3 test_contact.py`.

## Where things come from

- `ztlcore/` — a vendored copy of the ZTL core from [inventor1975/ZTL](https://github.com/inventor1975/ZTL), the
  source of truth (logic, judge, proofs in Lean 4); `ztlcore/VERSION.md` says which commit.
- `zfl.py`, `zflexamples.py`, `zflcert.py`, `backread.py`, `warrant_receipt.py` — the same files as in the ZTL studio,
  [inventor1975/ztlstudio](https://github.com/inventor1975/ztlstudio) (studio: https://studio.ztljudge.com).
- About ZTL: https://ztljudge.com.

## License

Dual-licensed under Apache-2.0 or MIT, at your option (`LICENSE-APACHE`, `LICENSE-MIT`).

## AI disclosure

This code was written with Claude (Anthropic) under the direction of Vitaly Reznik, who is responsible for it.
