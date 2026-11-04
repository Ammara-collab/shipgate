"""Evidence dossier builder for ShipGate.

Maps every claim -> its tests -> the log lines that prove the outcome
-> screenshots (when the browser check ran). The dossier is the
downloadable artefact a human reviews before Approve/Reject.
"""

import json


def build_dossier(run_id, verdict, reasons, claims, tests, test_results,
                  sabotage, containment, browser, heal=None, engine="",
                  notes=""):
    by_claim = {}
    result_by_id = {t["id"]: t for t in test_results}
    test_by_id = {t["id"]: t for t in tests}
    for claim in claims:
        ctests = [t for t in tests if t["claim_id"] == claim["id"]]
        entries = []
        for t in ctests:
            r = result_by_id.get(t["id"], {})
            entries.append(
                {
                    "test_id": t["id"],
                    "expr": t["expr"],
                    "description": t["description"],
                    "passed": r.get("passed"),
                    "log_line": r.get("line", ""),
                }
            )
        by_claim[claim["id"]] = {
            "claim": claim["text"],
            "tests": entries,
        }
    dossier = {
        "run_id": run_id,
        "verdict": verdict,
        "reasons": reasons,
        "engine": engine,
        "claims": by_claim,
        "sabotage_check": sabotage,
        "containment": containment,
        "browser_check": browser,
        "heal": heal,
        "screenshots": (browser or {}).get("screenshots", []),
        "notes": notes,
    }
    return dossier


def to_text(dossier):
    lines = [f"ShipGate dossier — run {dossier['run_id']}",
             f"VERDICT: {dossier['verdict']}",
             f"Engine: {dossier['engine']}"]
    for r in dossier["reasons"]:
        lines.append(f"  reason: {r}")
    for cid, c in dossier["claims"].items():
        lines.append(f"Claim {cid}: {c['claim']}")
        for t in c["tests"]:
            mark = "PASS" if t["passed"] else "FAIL"
            lines.append(f"  [{mark}] {t['test_id']}: {t['expr']} "
                         f"({t['description']})")
            if t["log_line"]:
                lines.append(f"        log: {t['log_line']}")
    sab = dossier["sabotage_check"]
    lines.append(f"Sabotage check: {sab.get('status')} — {sab.get('detail')}")
    con = dossier["containment"]
    lines.append(f"Containment: {con.get('status')}")
    br = dossier["browser_check"] or {}
    lines.append(f"Browser check: {br.get('status')} — {br.get('note')}")
    if dossier.get("heal"):
        lines.append(f"Heal: {dossier['heal']}")
    return "\n".join(lines) + "\n"


def to_json(dossier):
    return json.dumps(dossier, indent=2)
