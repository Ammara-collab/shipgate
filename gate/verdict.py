"""Deterministic BLOCK/SHIP verdict. LLM NEVER votes here.

Rules (pure code, no model calls):
- SHIP iff: every claim has >= 1 passing test AND no test failed AND
  the sabotage check PASSED (mutated code went red) AND no containment
  failure. A browser check that ran and failed also blocks; a browser
  check skipped as "unavailable" is recorded but does not block (the
  dossier says so explicitly).
- Otherwise BLOCK, with machine-readable reasons.

Sabotage semantics: sabotage_passed=True means the suite WENT RED
when the code was deliberately broken (the gate CAN fail — good).
sabotage_passed=False means the suite stayed green after sabotage
(the gate CANNOT fail — untrustworthy) -> BLOCK.
"""

SHIP = "SHIP"
BLOCK = "BLOCK"


def compute_verdict(test_results, sabotage, containment, browser=None):
    """test_results: list of {id, claim_id, passed, line}.
    sabotage: {status: 'passed'|'failed'|'inconclusive', ...}.
    containment: {status: 'contained'|'breach'|'not_run', ...}.
    browser: None or {status: 'green'|'red'|'skipped', ...}.
    Returns (verdict, reasons:list[str], claim_coverage:dict).
    """
    reasons = []
    failed = [t for t in test_results if not t["passed"]]
    if failed:
        reasons.append(
            "failing tests: " + ", ".join(t["id"] for t in failed)
        )
    covered = {}
    for t in test_results:
        covered.setdefault(t["claim_id"], []).append(t["passed"])
    uncovered = [c for c, ps in covered.items() if not any(ps)]
    if uncovered:
        reasons.append(
            "claims with no passing test: " + ", ".join(uncovered)
        )
    if not test_results:
        reasons.append("no tests ran")
    if sabotage.get("status") == "failed":
        reasons.append(
            "sabotage check FAILED: suite stayed green after the code "
            "was deliberately broken — this gate cannot fail"
        )
    if sabotage.get("status") == "inconclusive":
        reasons.append(
            "note: sabotage check inconclusive (no mutation applied); "
            "not blocking"
        )
    if containment.get("status") == "breach":
        reasons.append("containment BREACH: hostile run escaped the sandbox")
    if browser and browser.get("status") == "red":
        reasons.append("browser check red")
    blocking = [r for r in reasons if not r.startswith("note:")]
    verdict = SHIP if not blocking else BLOCK
    return verdict, reasons, covered
