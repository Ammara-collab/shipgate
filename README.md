# ShipGate — Prove-It Release Gate

**Vultr: Agent Rush Hackathon, lablab.ai — Challenge 1: Blast Radius Zero**
**Team GateKeepers (solo) · $200 Vultr credit, code-only, no card**

An AI change ships only when its claims are **PROVEN by real sandboxed
execution** — otherwise the gate BLOCKS it, with evidence. LLM proposes,
code decides: the verdict is computed deterministically from test
outcomes, never by an LLM vote.

## The verdict

- **SHIP** — every atomic claim verified by a passing test + sabotage
  check passed (the suite goes red when code is deliberately broken)
- **BLOCK** — any claim contradicted, any test red, any sabotage
  check failed, any containment breach, any red browser check

Human Approve/Reject is the final step before SHIP.

## Vultr services used

- **Vultr Cloud Compute (VM, CPU only)** — web app + orchestrator and
  the Docker executor pool run on one VM. No GPU: none is available at
  this event, and ShipGate needs none (no training, no local models).
- **Vultr Serverless Inference** (OpenAI-compatible,
  `https://api.vultrinference.com/v1`) — all LLM calls: claim
  extraction (`gate/claims.py`) and adversarial test generation
  (`gate/tests_gen.py`). Key from `VULTR_INFERENCE_API_KEY`, model from
  `VULTR_INFERENCE_MODEL` (set it from the Vultr console catalogue —
  the catalogue was an open question at kickoff). Without a key/model,
  a clearly-marked deterministic **mock** is used for local testing
  only; the mock never decides the verdict.
- **Docker on the VM** — one fresh container per test run
  (`--rm --network none --cpus 1 --memory 256m --pids-limit 64`,
  read-only workdir mount, no host env/secrets, destroyed after the
  run). This is the sandbox boundary.

## Architecture

```
browser ──> Flask app + orchestrator (app.py, :5000)
               ├─ claims.py      Vultr Inference claim extraction
               ├─ tests_gen.py   adversarial tests (AST-validated)
               ├─ sandbox.py     Docker executor / local-runner fallback
               │                 + sabotage check + hostile containment
               ├─ verdict.py     deterministic BLOCK/SHIP
               ├─ heal.py        patch agent, FROZEN tests enforced
               ├─ browser_check.py  Playwright screenshots (max 3 flows)
               └─ dossier.py     claim -> test -> log line -> screenshot
```

## The containment moment (required in the demo video)

Seeded hostile run (`demo_prs/pr_hostile/`): an `rm -rf /` snippet, a
secret-read snippet, and an infinite loop. The sandbox absorbs all
three — destructive/secret-reading constructs are refused before
execution, the loop is killed by timeout — and the run proves the host
is untouched: a host marker file stays byte-identical and a canary
secret file is unchanged and never appears in any output. On the Vultr
VM the same attempts execute inside the disposable Docker container
instead; the host is untouched either way.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 app.py   # http://localhost:5000
```

Seeded demos (the video uses ONLY these):

- `GET /api/demo/pr_good` → SHIP (truthful explanation)
- `GET /api/demo/pr_bad` → BLOCK with the failing test as proof
  (+ a healed version that passes the frozen tests)
- `GET /api/demo/pr_sabotage` → sabotage check: suite goes red
- `GET /api/demo/pr_hostile` → containment report, host untouched

Or paste your own code + explanation at `/` and hit "Run gate".

## Deploy on the Vultr VM (operator clicks)

1. Create the cheapest CPU compute instance that runs Docker.
2. Install Docker on the VM.
3. Set `VULTR_INFERENCE_API_KEY` (+ `VULTR_INFERENCE_MODEL`) from the
   Vultr console.
4. Copy this folder to the VM, `pip install -r requirements.txt`,
   `python3 app.py`, open port 5000, note the public demo URL.
   (Or build the Dockerfile and run it with the Docker socket mounted
   so it can spawn sibling executor containers.)
5. After the event, destroy the VM so nothing bills past credits.

## Honest status (local build environment)

- Docker: **not available** here — the Docker executor path is
  implemented but **unverified**; verify it on the Vultr VM. Local runs
  use the clearly-marked subprocess fallback (policy scan + rlimits +
  timeout).
- Playwright: install status is checked at runtime; if unavailable the
  browser check reports `skipped` and never fabricates a screenshot.
- Vultr Inference: no key in this environment — mock extractors /
  generators are used, clearly marked, and never decide the verdict.
