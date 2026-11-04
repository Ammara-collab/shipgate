"""Adversarial test generation for ShipGate.

Mock path (LOCAL TESTING ONLY): deterministic tests derived from claim
kinds. Each test is an expression over the submitted code's first
top-level function (detected via AST). Tests are DESIGNED TO BREAK the
claim: cap tests use percentages above the cap, rounding tests use
prices that expose bad rounding, non-negative tests use >100% discounts.

Real path: same Vultr Serverless Inference env-key pattern as
gate/claims.py. Model-proposed tests are validated with an AST
allowlist before they are ever executed; anything suspicious falls
back to the mock generator. The model NEVER decides the verdict —
gate/verdict.py does, from test outcomes only.
"""

import ast
import json
import os
import urllib.request

BASE_URL = os.environ.get(
    "VULTR_INFERENCE_BASE_URL", "https://api.vultrinference.com/v1"
)

_TEST_PROMPT = (
    "Write adversarial tests for these claims about the code below. "
    "Return ONLY JSON: {\"tests\": [{\"claim_id\": \"C1\", \"expr\": "
    "\"<python expression that must be True if the claim holds>\", "
    "\"description\": \"<what it checks>\"}]}. Expressions may only call "
    "the code's main function with literal numbers. Design each test to "
    "BREAK the claim if it is false.\n\nClaims:\n{claims}\n\nCode:\n{code}"
)

# expressions use the placeholder FN, replaced with the real function name
_MOCK_TESTS = {
    "cap": [
        ("cap-above", "FN(200, 80) == 100.0",
         "discount above the cap must still stop at the cap"),
        ("cap-at", "FN(100, 50) == 50.0",
         "discount exactly at the cap"),
    ],
    "rounding": [
        ("round-odd", "FN(19.99, 15) == 16.99",
         "result rounded to 2 decimal places"),
    ],
    "discount_math": [
        ("math-basic", "FN(100, 10) == 90.0",
         "plain percentage discount"),
    ],
    "non_negative": [
        ("nonneg-huge", "FN(50, 150) >= 0",
         "result never goes negative, even past 100%"),
    ],
    "free_shipping": [
        ("generic-zero", "FN(100, 0) == 100.0",
         "zero discount leaves the price unchanged"),
    ],
    "generic": [
        ("generic-zero", "FN(100, 0) == 100.0",
         "zero discount leaves the price unchanged"),
    ],
}


def detect_function(code):
    """First top-level function name in the submitted code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            return node.name
    return None


def mock_generate(claims, code):
    func = detect_function(code)
    if func is None:
        return []
    tests = []
    n = 0
    for claim in claims:
        for kind in claim.get("kinds", ["generic"]):
            for suffix, expr, desc in _MOCK_TESTS.get(kind, _MOCK_TESTS["generic"]):
                n += 1
                tests.append(
                    {
                        "id": f"T{n}",
                        "claim_id": claim["id"],
                        "expr": expr.replace("FN", func),
                        "description": desc,
                        "source": "mock",
                    }
                )
    # de-duplicate identical expressions (claims often share kinds)
    seen, uniq = set(), []
    for t in tests:
        if t["expr"] not in seen:
            seen.add(t["expr"])
            uniq.append(t)
    for i, t in enumerate(uniq, start=1):
        t["id"] = f"T{i}"
    return uniq


def _validate_expr(expr, func):
    """AST allowlist: only comparisons/calls to func, literals, arithmetic."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return False
    allowed = (
        ast.Expression, ast.Compare, ast.Call, ast.BinOp, ast.UnaryOp,
        ast.Constant, ast.Name, ast.Load, ast.Eq, ast.NotEq, ast.Lt,
        ast.LtE, ast.Gt, ast.GtE, ast.Add, ast.Sub, ast.Mult, ast.Div,
        ast.Mod, ast.USub, ast.BoolOp, ast.And, ast.Or,
    )
    for node in tree.walk(tree):
        if not isinstance(node, allowed):
            return False
        if isinstance(node, ast.Name) and node.id not in (func,):
            return False
        if isinstance(node, ast.Call) and not (
            isinstance(node.func, ast.Name) and node.func.id == func
        ):
            return False
    return True


def generate_tests(claims, code):
    """Return (tests, used_mock, note)."""
    api_key = os.environ.get("VULTR_INFERENCE_API_KEY", "").strip()
    model = os.environ.get("VULTR_INFERENCE_MODEL", "").strip()
    func = detect_function(code)
    if api_key and model and func:
        try:
            claim_text = "\n".join(
                f'{c["id"]}: {c["text"]}' for c in claims
            )
            payload = json.dumps(
                {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": _TEST_PROMPT.format(
                                claims=claim_text, code=code
                            ),
                        }
                    ],
                    "temperature": 0,
                }
            ).encode()
            req = urllib.request.Request(
                BASE_URL.rstrip("/") + "/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode())
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(
                content[content.index("{") : content.rindex("}") + 1]
            )
            tests = []
            for i, t in enumerate(parsed.get("tests", []), start=1):
                expr = t.get("expr", "")
                if _validate_expr(expr, func):
                    tests.append(
                        {
                            "id": f"T{i}",
                            "claim_id": t.get("claim_id", "?"),
                            "expr": expr,
                            "description": t.get("description", ""),
                            "source": "vultr-inference",
                        }
                    )
            if tests:
                return tests, False, f"vultr-inference model={model}"
        except Exception as exc:
            note = f"mock generator (Vultr Inference failed: {exc})"
            return mock_generate(claims, code), True, note
    return mock_generate(claims, code), True, (
        "mock generator (no VULTR_INFERENCE_API_KEY or "
        "VULTR_INFERENCE_MODEL set — local testing only)"
    )


def render_test_script(tests, func):
    """A stdlib-only test runner script. Prints PASS/FAIL lines + summary."""
    lines = [
        "import sys, traceback",
        f"from code import {func}",
        "results = []",
    ]
    for t in tests:
        expr = t["expr"].replace('"', '\\"')
        lines.append(
            f"try:\n"
            f"    ok = bool({t['expr']})\n"
            f"    print(('PASS' if ok else 'FAIL') + ' {t['id']} "
            f"{t['claim_id']} :: {expr} -> ' + repr(ok))\n"
            f"    results.append(ok)\n"
            f"except Exception as e:\n"
            f"    print('FAIL {t['id']} {t['claim_id']} :: {expr} -> ERROR ' + repr(e))\n"
            f"    results.append(False)"
        )
    lines.append(
        "print('SUMMARY passed=%d failed=%d' % "
        "(sum(results), len(results) - sum(results)))"
    )
    lines.append("sys.exit(0 if all(results) else 1)")
    return "\n".join(lines) + "\n"
