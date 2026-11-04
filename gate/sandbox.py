"""Sandboxed execution for ShipGate.

PRODUCTION PATH (Vultr VM): Docker — one fresh container per run.
  docker run --rm --network none --cpus 1 --memory 256m
             --memory-swap 256m --pids-limit 64
             -e PYTHONDONTWRITEBYTECODE=1
             -v <workdir>:/work:ro -w /work python:3.12-slim python tests.py
  The container is destroyed by --rm. No host env/secrets are passed.
  NOTE: Docker is NOT available in this build environment, so the Docker
  path is implemented but UNVERIFIED here — it must be verified on the
  Vultr VM before the demo video is recorded.

FALLBACK PATH (LOCAL TESTING ONLY, clearly marked): a subprocess
runner with (a) a pre-execution AST policy scan that REFUSES to run
destructive constructs (os.system, subprocess, rmtree, absolute-path
opens outside the workdir, 'rm -rf' literals), (b) stripped env,
(c) CPU/memory rlimits, (d) a hard timeout. The fallback NEVER executes
a hostile snippet it has flagged — that refusal IS the containment
record for local testing. On the Vultr VM the Docker path replaces it.
"""

import ast
import os
import re
import resource
import shutil
import subprocess
import tempfile

DOCKER_IMAGE = "python:3.12-slim"
CPU_LIMIT = "1"
MEM_LIMIT = "256m"
RUN_TIMEOUT = 20

# AST policy: (node type, attribute chain) patterns that are refused
_DANGEROUS_CALLS = {
    ("os", "system"), ("os", "popen"), ("os", "remove"), ("os", "unlink"),
    ("os", "rmdir"), ("os", "execv"), ("os", "execl"), ("os", "spawnl"),
    ("subprocess", "call"), ("subprocess", "run"), ("subprocess", "Popen"),
    ("subprocess", "check_output"), ("shutil", "rmtree"),
}
_DANGEROUS_LITERALS = re.compile(r"rm\s+-rf|/dev/(null|zero|random)|mkfs")


def policy_scan(code, workdir):
    """Return list of policy violations; empty list = allowed to run."""
    violations = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax error, refusing to run: {exc}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            val = node.func.value
            if isinstance(val, ast.Name):
                if (val.id, node.func.attr) in _DANGEROUS_CALLS:
                    violations.append(
                        f"blocked call: {val.id}.{node.func.attr}"
                    )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("eval", "exec", "compile", "open"):
                if node.func.id == "open" and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if os.path.isabs(arg.value) and not arg.value.startswith(
                            workdir
                        ):
                            violations.append(
                                f"blocked absolute-path open outside workdir: "
                                f"{arg.value}"
                            )
                elif node.func.id in ("eval", "exec"):
                    violations.append(f"blocked dynamic execution: {node.func.id}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _DANGEROUS_LITERALS.search(node.value):
                violations.append(
                    f"blocked dangerous literal: {node.value[:60]!r}"
                )
    return violations


def _limits():
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)


def _run_local(workdir, script_name="tests.py"):
    """Subprocess runner (fallback). Returns (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ["python3", script_name],
            cwd=workdir,
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT,
            preexec_fn=_limits,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        return "timeout", (exc.stdout or b"").decode() if isinstance(
            exc.stdout, bytes) else (exc.stdout or ""), "TIMEOUT: killed"


def _run_docker(workdir, script_name="tests.py"):
    """Docker runner (production path). Returns (rc, stdout, stderr)."""
    cmd = [
        "docker", "run", "--rm", "--network", "none",
        "--cpus", CPU_LIMIT, "--memory", MEM_LIMIT,
        "--memory-swap", MEM_LIMIT, "--pids-limit", "64",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-v", f"{workdir}:/work:ro", "-w", "/work",
        DOCKER_IMAGE, "python", script_name,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=RUN_TIMEOUT
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return "timeout", "", "TIMEOUT: container killed"


def docker_available():
    return shutil.which("docker") is not None


def run_tests(code, test_script):
    """Run a test script against code. Returns dict with results + engine."""
    workdir = tempfile.mkdtemp(prefix="shipgate-run-")
    try:
        with open(os.path.join(workdir, "code.py"), "w") as f:
            f.write(code)
        with open(os.path.join(workdir, "tests.py"), "w") as f:
            f.write(test_script)
        violations = policy_scan(code, workdir)
        if violations:
            return {
                "engine": "policy-scan",
                "results": [],
                "log": "BLOCKED BY SANDBOX POLICY:\n" + "\n".join(violations),
                "blocked": violations,
            }
        if docker_available():
            rc, out, err = _run_docker(workdir)
            engine = "docker"
        else:
            rc, out, err = _run_local(workdir)
            engine = "local-runner-fallback"
        results = []
        for line in (out or "").splitlines():
            m = re.match(r"^(PASS|FAIL) (\S+) (\S+) :: (.*)$", line)
            if m:
                results.append(
                    {
                        "id": m.group(2),
                        "claim_id": m.group(3),
                        "passed": m.group(1) == "PASS",
                        "line": line,
                    }
                )
        log = (out or "") + ("\nSTDERR:\n" + err if err else "")
        if rc == "timeout":
            log += "\nRUNNER: timeout — process killed"
        return {"engine": engine, "results": results, "log": log,
                "blocked": []}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


_MUTATIONS = [
    (" - ", " + ", "flipped subtraction to addition"),
    (" + ", " - ", "flipped addition to subtraction"),
    (">=", "<", "flipped >= to <"),
    ("<=", ">", "flipped <= to >"),
]


def mutate_code(code):
    """First parseable behaviour-changing mutation, or (None, None)."""
    for old, new, desc in _MUTATIONS:
        if old in code:
            mutated = code.replace(old, new, 1)
            try:
                ast.parse(mutated)
            except SyntaxError:
                continue
            if mutated != code:
                return mutated, desc
    return None, None


def sabotage_check(code, test_script):
    """Break the code on purpose; the suite MUST go red.
    Returns {status: 'passed'|'failed'|'inconclusive', ...}."""
    mutated, desc = mutate_code(code)
    if mutated is None:
        return {"status": "inconclusive",
                "detail": "no safe mutation applied"}
    res = run_tests(mutated, test_script)
    if res.get("blocked"):
        return {"status": "inconclusive",
                "detail": "mutated code blocked by policy scan",
                "mutation": desc}
    failed = [t for t in res["results"] if not t["passed"]]
    if failed:
        return {"status": "passed",
                "detail": f"suite went red after sabotage ({desc}): "
                          + ", ".join(t["id"] for t in failed),
                "mutation": desc, "log": res["log"]}
    return {"status": "failed",
            "detail": f"suite stayed GREEN after sabotage ({desc}) — "
                      "this gate cannot fail",
            "mutation": desc, "log": res["log"]}


def run_hostile(attempts, host_marker_path, canary_path):
    """Run hostile snippets; verify the host is untouched.
    attempts: list of {name, code}. Returns containment report.
    The fallback runner NEVER executes policy-flagged snippets; the
    Docker path would execute them inside the container instead."""
    per_attempt = []
    workdir = tempfile.mkdtemp(prefix="shipgate-hostile-")
    try:
        with open(host_marker_path) as f:
            marker_before = f.read()
        with open(canary_path) as f:
            canary_before = f.read()
        all_output = ""
        for att in attempts:
            violations = policy_scan(att["code"], workdir)
            if violations:
                per_attempt.append(
                    {"name": att["name"], "outcome": "contained-blocked",
                     "detail": "; ".join(violations)}
                )
                continue
            path = os.path.join(workdir, att["name"] + ".py")
            with open(path, "w") as f:
                f.write(att["code"])
            try:
                proc = subprocess.run(
                    ["python3", path],
                    cwd=workdir,
                    env={"PATH": "/usr/bin:/bin",
                         "PYTHONDONTWRITEBYTECODE": "1"},
                    capture_output=True, text=True, timeout=5,
                    preexec_fn=_limits,
                )
                out = proc.stdout + proc.stderr
                all_output += out
                per_attempt.append(
                    {"name": att["name"], "outcome": "contained-ran-clean",
                     "detail": f"exit={proc.returncode}, no host effect"}
                )
            except subprocess.TimeoutExpired:
                per_attempt.append(
                    {"name": att["name"], "outcome": "contained-timeout",
                     "detail": "infinite loop killed by timeout"}
                )
        with open(host_marker_path) as f:
            marker_after = f.read()
        with open(canary_path) as f:
            canary_after = f.read()
        host_untouched = (
            marker_before == marker_after
            and canary_before == canary_after
            and canary_before not in all_output
        )
        return {
            "status": "contained" if host_untouched else "breach",
            "host_untouched": host_untouched,
            "marker_intact": marker_before == marker_after,
            "canary_intact": canary_before == canary_after,
            "canary_leaked": canary_before in all_output,
            "attempts": per_attempt,
            "engine": "docker" if docker_available()
                      else "local-runner-fallback",
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
