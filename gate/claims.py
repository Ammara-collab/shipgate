"""Claim extraction for ShipGate.

Real path: Vultr Serverless Inference, OpenAI-compatible API.
  POST {base_url}/chat/completions  (base_url default
  https://api.vultrinference.com/v1)
  Auth: Bearer VULTR_INFERENCE_API_KEY
  Model: VULTR_INFERENCE_MODEL (no default — the model catalogue is an
  open question from the kickoff; the operator sets it from the Vultr
  console. If it is unset, the real path is skipped.)

Mock path (LOCAL TESTING ONLY, clearly marked): used when no API key
(or no model) is configured, or when the API call fails. The mock is
deterministic: it splits the explanation into sentences and tags each
sentence with checkable kinds by keyword. It NEVER invents a verdict —
tests, not claims, decide BLOCK/SHIP (see gate/verdict.py).
"""

import json
import os
import re
import urllib.request

BASE_URL = os.environ.get(
    "VULTR_INFERENCE_BASE_URL", "https://api.vultrinference.com/v1"
)

# keyword -> claim kind (mock extractor only)
_KIND_KEYWORDS = [
    (("cap", "maximum", "at most", "no more than"), "cap"),
    (("round", "decimal"), "rounding"),
    (("free shipping", "shipping"), "free_shipping"),
    (("negative", "below zero", "never negative"), "non_negative"),
    (("percent", "percentage", "discount"), "discount_math"),
]

_CLAIM_PROMPT = (
    "Extract the atomic, testable claims from this AI-generated code "
    "explanation. Return ONLY a JSON object of the form "
    '{"claims": [{"text": "<one claim>", "kind": "<short kind>"}]}. '
    "One claim per fact. Do not judge whether the claims are true.\n\n"
    "Explanation:\n{explanation}\n\nCode:\n{code}"
)


def _mock_extract(explanation):
    """Deterministic local-testing extractor. Marked as mock everywhere."""
    sentences = [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+", explanation.strip())
        if s.strip()
    ]
    claims = []
    for i, sent in enumerate(sentences, start=1):
        low = sent.lower()
        kinds = [kind for keys, kind in _KIND_KEYWORDS if any(k in low for k in keys)]
        claims.append(
            {
                "id": f"C{i}",
                "text": sent,
                "kinds": kinds or ["generic"],
                "source": "mock",
            }
        )
    return claims


def extract_claims(explanation, code=""):
    """Return (claims, used_mock: bool, note: str)."""
    api_key = os.environ.get("VULTR_INFERENCE_API_KEY", "").strip()
    model = os.environ.get("VULTR_INFERENCE_MODEL", "").strip()
    if not api_key or not model:
        return _mock_extract(explanation), True, (
            "mock extractor (no VULTR_INFERENCE_API_KEY or "
            "VULTR_INFERENCE_MODEL set — local testing only)"
        )
    try:
        payload = json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": _CLAIM_PROMPT.format(
                            explanation=explanation, code=code
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
        parsed = json.loads(content[content.index("{") : content.rindex("}") + 1])
        claims = []
        for i, c in enumerate(parsed.get("claims", []), start=1):
            claims.append(
                {
                    "id": f"C{i}",
                    "text": c.get("text", ""),
                    "kinds": [c.get("kind", "generic")],
                    "source": "vultr-inference",
                }
            )
        if not claims:
            raise ValueError("empty claims from model")
        return claims, False, f"vultr-inference model={model}"
    except Exception as exc:  # API failure must never block the gate demo
        return _mock_extract(explanation), True, (
            f"mock extractor (Vultr Inference call failed: {exc} — "
            "local testing only)"
        )
