"""ShipGate — Prove-It Release Gate. Flask web app + orchestrator.

Pipeline (spec: 7-stage flow):
  paste -> claims (Vultr Inference, mock if no key) -> adversarial
  tests -> sandboxed execution (Docker on Vultr VM, local-runner
  fallback here) -> sabotage check -> containment (hostile demos) ->
  heal (frozen tests) + browser screenshots -> dossier + verdict.

LLM proposes, code decides: gate/verdict.py computes BLOCK/SHIP from
test outcomes only. Human Approve/Reject is the final step before SHIP.
"""

import json
import os
import uuid

from flask import Flask, jsonify, request

from gate import browser_check, dossier as dossier_mod
from gate.claims import extract_claims
from gate.heal import attempt_heal
from gate.sandbox import (
    docker_available, run_hostile, run_tests, sabotage_check,
)
from gate.tests_gen import detect_function, generate_tests, render_test_script
from gate.verdict import BLOCK, SHIP, compute_verdict

BASE = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.join(BASE, "demo_prs")
RUNS_DIR = os.path.join(BASE, "runs")
os.makedirs(RUNS_DIR, exist_ok=True)

HOST_MARKER = "/tmp/shipgate-host-marker.txt"
HOST_CANARY = "/tmp/shipgate-host-secret.txt"

app = Flask(__name__)
RUNS = {}


def _read_demo(name):
    d = os.path.join(DEMO_DIR, name)
    code = open(os.path.join(d, "code.py")).read()
    expl = open(os.path.join(d, "explanation.txt")).read()
    return code, expl


def run_gate(code, explanation, run_id, browser_flows=None):
    """Full pipeline. Returns the run dict (also saved to runs/)."""
    claims, claims_mock, claims_note = extract_claims(explanation, code)
    tests, tests_mock, tests_note = generate_tests(claims, code)
    func = detect_function(code)
    notes = [claims_note, tests_note]
    if func is None:
        verdict, reasons = BLOCK, ["no top-level function found in code"]
        test_results, engine, log = [], "none", ""
        sabotage = {"status": "inconclusive",
                    "detail": "no function to sabotage"}
        browser = {"status": "skipped",
                   "note": "no function to boot in a browser"}
        heal = None
    else:
        script = render_test_script(tests, func)
        run = run_tests(code, script)
        test_results, engine, log = (
            run["results"], run["engine"], run["log"])
        if run.get("blocked"):
            verdict, reasons = BLOCK, [
                "code blocked by sandbox policy: " + "; ".join(run["blocked"])
            ]
            sabotage = {"status": "inconclusive",
                        "detail": "original code blocked, sabotage skipped"}
            browser = {"status": "skipped",
                       "note": "code blocked, browser check skipped"}
            heal = None
        else:
            sabotage = sabotage_check(code, script)
            if browser_flows:
                shot_dir = os.path.join(RUNS_DIR, run_id)
                os.makedirs(shot_dir, exist_ok=True)
                browser = browser_check.run_browser_check(
                    browser_flows[0]["url"], browser_flows, shot_dir)
            else:
                browser = {"status": "skipped", "screenshots": [],
                           "note": "no web app to boot for pasted code"}
            containment = {"status": "not_run"}
            verdict, reasons, _ = compute_verdict(
                test_results, sabotage, containment, browser)
            heal = None
            if verdict == BLOCK:
                failed = [t for t in test_results if not t["passed"]]
                failed_claims = {t["claim_id"] for t in failed}
                kinds = set()
                for c in claims:
                    if c["id"] in failed_claims:
                        kinds.update(c.get("kinds", []))
                heal = attempt_heal(
                    code, script, failed,
                    lambda c, s: run_tests(c, s), failing_kinds=kinds)
    doss = dossier_mod.build_dossier(
        run_id, verdict, reasons, claims, tests, test_results,
        sabotage, {"status": "not_run"}, browser, heal=heal,
        engine=engine, notes="; ".join(notes))
    out = {
        "run_id": run_id, "verdict": verdict, "reasons": reasons,
        "claims": claims, "tests": tests, "test_results": test_results,
        "sabotage": sabotage, "browser": browser, "heal": heal,
        "engine": engine, "log": log, "dossier": doss,
        "dossier_text": dossier_mod.to_text(doss),
    }
    RUNS[run_id] = out
    with open(os.path.join(RUNS_DIR, run_id + ".json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    return out


def run_hostile_demo(run_id):
    """Containment demo: 3 hostile attempts, host must stay untouched."""
    with open(HOST_MARKER, "w") as f:
        f.write("HOST-INTACT-SHIPGATE\n")
    with open(HOST_CANARY, "w") as f:
        f.write("CANARY-SECRET-DO-NOT-LEAK\n")
    attempts = []
    d = os.path.join(DEMO_DIR, "pr_hostile")
    for fname, name in [("hostile_rm.py", "rm-rf"),
                        ("hostile_secret.py", "secret-read"),
                        ("hostile_loop.py", "infinite-loop")]:
        attempts.append(
            {"name": name,
             "code": open(os.path.join(d, fname)).read()})
    report = run_hostile(attempts, HOST_MARKER, HOST_CANARY)
    out = {"run_id": run_id, "demo": "pr_hostile",
           "containment": report,
           "host_untouched": report["host_untouched"],
           "verdict": "CONTAINED" if report["status"] == "contained"
                      else "BREACH"}
    RUNS[run_id] = out
    with open(os.path.join(RUNS_DIR, run_id + ".json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    return out


@app.route("/")
def index():
    return INDEX_HTML


@app.route("/demo-app")
def demo_app():
    """Tiny calculator page for browser before/after screenshots."""
    variant = request.args.get("variant", "orig")
    price = float(request.args.get("price", 200))
    pct = float(request.args.get("pct", 80))
    if variant == "healed":
        code, _ = _read_demo("pr_bad")
        # healed = pr_bad code + the same deterministic cap patch
        lines = code.splitlines()
        for i, ln in enumerate(lines):
            if ln.startswith("def "):
                lines.insert(i + 1, "    pct = min(pct, 50)")
                break
        code = "\n".join(lines) + "\n"
    else:
        code, _ = _read_demo("pr_bad")
    ns = {}
    exec(compile(code, "demo", "exec"), ns)
    result = ns["discount"](price, pct)
    return (f"<h1>ShipGate demo shop</h1>"
            f"<p>price={price} pct={pct} variant={variant}</p>"
            f"<p><b>discounted price: {result}</b></p>"
            f"<p>Claim: discount capped at a maximum of 50%.</p>")


@app.route("/api/gate", methods=["POST"])
def api_gate():
    data = request.get_json(force=True)
    run_id = uuid.uuid4().hex[:12]
    return jsonify(run_gate(data.get("code", ""),
                            data.get("explanation", ""), run_id))


@app.route("/api/demo/<name>")
def api_demo(name):
    run_id = uuid.uuid4().hex[:12]
    if name == "pr_hostile":
        return jsonify(run_hostile_demo(run_id))
    if name not in ("pr_good", "pr_bad", "pr_sabotage"):
        return jsonify({"error": "unknown demo"}), 404
    code, expl = _read_demo(name)
    base = request.host_url.rstrip("/")
    flows = None
    if name == "pr_bad":
        flows = [
            {"name": "before",
             "url": f"{base}/demo-app?variant=orig&price=200&pct=80"},
            {"name": "after",
             "url": f"{base}/demo-app?variant=healed&price=200&pct=80"},
        ]
    out = run_gate(code, expl, run_id, browser_flows=flows)
    out["demo"] = name
    if name == "pr_sabotage":
        # red-suite proof = the sabotage log (mutated code failed tests)
        out["red_suite_proof"] = out["sabotage"]
    with open(os.path.join(RUNS_DIR, run_id + ".json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    return jsonify(out)


@app.route("/api/run/<run_id>")
def api_run(run_id):
    if run_id in RUNS:
        return jsonify(RUNS[run_id])
    path = os.path.join(RUNS_DIR, run_id + ".json")
    if os.path.exists(path):
        return jsonify(json.load(open(path)))
    return jsonify({"error": "unknown run"}), 404


@app.route("/api/approve/<run_id>", methods=["POST"])
def api_approve(run_id):
    data = request.get_json(force=True, silent=True) or {}
    decision = data.get("decision", "approve")
    run = RUNS.get(run_id)
    if not run:
        return jsonify({"error": "unknown run"}), 404
    run["human_decision"] = decision
    if run.get("verdict") == SHIP and decision == "approve":
        run["final"] = "SHIPPED (gate SHIP + human approved)"
    elif decision == "reject":
        run["final"] = "REJECTED by human — does not ship"
    else:
        run["final"] = "gate verdict stands: " + str(run.get("verdict"))
    with open(os.path.join(RUNS_DIR, run_id + ".json"), "w") as f:
        json.dump(run, f, indent=2, default=str)
    return jsonify(run)


INDEX_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>ShipGate — Prove-It Release Gate</title>
<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:2em auto;
padding:0 1em}textarea{width:100%;height:120px}.ship{color:green}
.block{color:red}pre{background:#111;color:#eee;padding:1em;overflow:auto}
button{margin:.3em;padding:.5em 1em}</style></head><body>
<h1>🛡️ ShipGate — Prove-It Release Gate</h1>
<p>An AI change ships only when its claims are <b>proven</b> by real
sandboxed execution. LLM proposes, code decides.</p>
<h2>Seeded demos (video uses only these)</h2>
<button onclick="demo('pr_good')">PR-GOOD → expect SHIP</button>
<button onclick="demo('pr_bad')">PR-BAD → expect BLOCK</button>
<button onclick="demo('pr_sabotage')">PR-SABOTAGE → red suite</button>
<button onclick="demo('pr_hostile')">PR-HOSTILE → containment</button>
<h2>Or paste your own diff + explanation</h2>
<textarea id="code" placeholder="paste code here (python function)"></textarea>
<textarea id="expl" placeholder="paste the AI explanation here"></textarea>
<button onclick="gate()">Run gate</button>
<div id="out"></div>
<script>
async function show(r){
  const o=document.getElementById('out');
  let h='<h2>Verdict: <span class="'+(r.verdict==='SHIP'?'ship':'block')+'">'+r.verdict+'</span></h2>';
  if(r.reasons) h+='<p>Reasons: '+r.reasons.join(' | ')+'</p>';
  if(r.test_results) h+='<p>Tests: '+r.test_results.map(t=>(t.passed?'✅ ':'❌ ')+t.id+' '+t.line).join('<br>')+'</p>';
  if(r.sabotage) h+='<p>Sabotage: '+r.sabotage.status+' — '+r.sabotage.detail+'</p>';
  if(r.containment) h+='<p>Containment: '+r.containment.status+' — host untouched: '+r.containment.host_untouched+'</p>';
  if(r.heal) h+='<p>Heal: patched='+r.heal.patched+' frozen_tests_intact='+r.heal.frozen_tests_intact+' — '+(r.heal.patches||[]).join(', ')+'</p>';
  if(r.dossier_text) h+='<h3>Dossier</h3><pre>'+r.dossier_text.replace(/</g,'&lt;')+'</pre>';
  if(r.run_id) h+='<p><button onclick="approve(\\''+r.run_id+'\\',\\'approve\\')">Human: Approve</button> <button onclick="approve(\\''+r.run_id+'\\',\\'reject\\')">Human: Reject</button></p>';
  o.innerHTML=h;
}
async function demo(n){ show(await (await fetch('/api/demo/'+n)).json()); }
async function gate(){
  const r=await fetch('/api/gate',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code:document.getElementById('code').value,
    explanation:document.getElementById('expl').value})});
  show(await r.json());
}
async function approve(id,d){
  const r=await fetch('/api/approve/'+id,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({decision:d})});
  const j=await r.json(); alert(j.final);
}
</script></body></html>
"""

if __name__ == "__main__":
    print(f"ShipGate on :5000 — docker_available={docker_available()}")
    app.run(host="0.0.0.0", port=5000)
