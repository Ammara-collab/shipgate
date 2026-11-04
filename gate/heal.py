"""Patch agent for ShipGate — with FROZEN TESTS enforced.

The patcher may NEVER edit tests to pass. Enforcement: the test script
is hashed before patching; after patching, the hash must be identical,
otherwise the heal is refused and the original BLOCK stands.

Patches are deterministic, minimal, and explainable (no LLM writes
code here in the local path):
- cap fix: if a cap-kind test failed and the code has no min() cap,
  insert `pct = min(pct, 50)` at the top of the function body.
- rounding fix: if a rounding-kind test failed and the code has no
  round(), wrap the return expression in round(..., 2).

After patching, the FROZEN (byte-identical) tests re-run against the
patched code. A heal NEVER flips the original verdict: it produces a
candidate fix + fresh proof, which must be re-submitted to ship.
"""

import hashlib
import re


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def attempt_heal(code, test_script, failing_tests, run_fn, failing_kinds=()):
    """run_fn(code, test_script) -> run_tests-style dict.
    failing_kinds: kinds of the claims whose tests failed.
    Returns {patched, patched_code, frozen_tests_intact, results, note}."""
    frozen_hash = _sha(test_script)
    patched = code
    applied = []
    if "cap" in failing_kinds and "min(" not in patched:
        patched = _insert_after_def(patched, "pct = min(pct, 50)")
        applied.append("added 50% cap: pct = min(pct, 50)")
    if "rounding" in failing_kinds and "round(" not in patched:
        patched = _wrap_return_round(patched)
        applied.append("wrapped return in round(..., 2)")
    if not applied:
        return {"patched": False, "patched_code": code,
                "frozen_tests_intact": True, "results": [],
                "note": "no deterministic patch applies to these failures"}
    res = run_fn(patched, test_script)
    intact = _sha(test_script) == frozen_hash  # test script never rewritten
    return {"patched": True, "patched_code": patched,
            "frozen_tests_intact": intact,
            "results": res.get("results", []),
            "log": res.get("log", ""),
            "patches": applied,
            "note": ("frozen tests re-ran against patched code; "
                     "original verdict unchanged — re-submit to ship")
                     if intact else
                     "REFUSED: test script changed during heal"}


def _insert_after_def(code, line):
    lines = code.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("def "):
            indent = "    "
            lines.insert(i + 1, indent + line.strip())
            return "\n".join(lines) + "\n"
    return code


def _wrap_return_round(code):
    def repl(m):
        return f"return round({m.group(1)}, 2)"
    new, n = re.subn(r"return (.+)", repl, code, count=1)
    return new if n else code
