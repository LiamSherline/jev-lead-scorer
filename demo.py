#!/usr/bin/env python3
"""End-to-end demo of the Jev lead scorer. Stub mode only.

Copies the package into a temp dir, blocks ALL socket traffic via
sitecustomize, then runs the full loop against a throwaway SQLite DB:
load demo leads, add variants, Jev-score, rank, log sends, log outcomes,
funnel, triage a reply, research a lead, render follow-up drafts.

Safe to run anywhere. Proves the stub path makes zero network calls.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
FILES = ["jev_score.py", "jev_research.py", "jev_triage.py", "outreach.py",
         "followup.py", "REPLY_PLAYBOOK.md", "leads.demo.json",
         "evidence.demo.json", "icp.example.json", "icp.example.yaml"]

SITECUSTOMIZE = '''
import socket
def _blocked(*a, **k):
    raise RuntimeError("NETWORK BLOCKED: demo mode must not touch the network")
socket.create_connection = _blocked
socket.socket.connect = _blocked
'''

FORBIDDEN = ["latch", "barrios", "sherline", "oak ridge", "865-",
             "@gmail", "typesafe.ai/v1"]


def run(cmd, env, cwd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd)
    print("$", " ".join(str(c) for c in cmd))
    if r.stdout:
        print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    if check and r.returncode != 0:
        raise SystemExit(f"FAILED: {' '.join(str(c) for c in cmd)}")
    return r


def main():
    tmp = Path(tempfile.mkdtemp(prefix="jev-demo-"))
    print(f"demo dir: {tmp}")
    for f in FILES:
        shutil.copy(HERE / f, tmp / f)
    (tmp / "sitecustomize.py").write_text(SITECUSTOMIZE)
    (tmp / "_draftcheck.py").write_text(
        "import followup\n"
        "lead = {'id': 1, 'business': 'Bluebird Diner', 'website': None, 'facebook': 'x'}\n"
        "for t in (2, 3, 4, 5):\n"
        "    print('--- touch %d ---' % t)\n"
        "    print(followup.draft_copy(lead, t))\n"
        "    print()\n"
    )

    env = dict(os.environ)
    env["JEV_STUB"] = "1"
    env.pop("TYPESAFE_API_KEY", None)   # prove stub does not need a key
    env.pop("TYPESAFE_CLI", None)
    env["PYTHONPATH"] = str(tmp) + os.pathsep + env.get("PYTHONPATH", "")
    py = sys.executable

    def oc(*args):
        return run([py, "outreach.py", *args], env, tmp)

    print("\n=== 1. load demo leads ===")
    for l in json.loads((tmp / "leads.demo.json").read_text()):
        oc("add-lead", "--business", l["business"], "--contact", l["contact"],
           "--channel", l["channel"], "--segment", l["segment"],
           "--notes", l["notes"])

    print("\n=== 2. add variants ===")
    oc("add-variant", "--id", "A", "--name", "Control: free sample offer",
       "--opener", "I put together a free sample for you, mind if I send it over?")
    oc("add-variant", "--id", "B", "--name", "Question-led",
       "--opener", "Quick question: do most of your regulars order ahead by phone?")

    print("\n=== 3. Jev-score (stub) ===")
    oc("score")

    print("\n=== 4. ranked leads ===")
    oc("leads", "--top", "5")

    print("\n=== 5. sends + outcomes ===")
    oc("send", "--lead", "1", "--variant", "A", "--channel", "fb_dm",
       "--touch", "1", "--batch", "demo")
    oc("send", "--lead", "2", "--variant", "B", "--channel", "fb_dm",
       "--touch", "1", "--batch", "demo")
    oc("send", "--lead", "3", "--variant", "A", "--channel", "instagram",
       "--touch", "1", "--batch", "demo")
    oc("outcome", "--send", "1", "--kind", "reply")
    oc("outcome", "--send", "1", "--kind", "positive_reply")
    oc("outcome", "--send", "1", "--kind", "meeting_set")
    oc("outcome", "--send", "2", "--kind", "reply")

    print("\n=== 6. funnel ===")
    oc("funnel")

    print("\n=== 7. triage a reply (stub) ===")
    oc("triage", "--lead", "1", "--send", "1", "--channel", "fb_dm",
       "--text", "sounds good, when can we talk?")
    oc("inbox", "--hot")

    print("\n=== 8. research a lead (stub) ===")
    run([py, "jev_research.py", "--evidence", "evidence.demo.json"], env, tmp)

    print("\n=== 9. follow-up drafts ===")
    run([py, "followup.py", "due"], env, tmp)
    run([py, "_draftcheck.py"], env, tmp)

    print("\n=== 10. config loading ===")
    r = run([py, "-c",
             "import jev_score, json;"
             " print('default ICP:', jev_score.load_icp()['business_name'])"],
            env, tmp)
    assert "Demo Coffee Roasters" in r.stdout, "default demo ICP not used"
    shutil.copy(tmp / "icp.example.json", tmp / "icp.json")
    r = run([py, "-c",
             "import jev_score;"
             " print('icp.json ICP:', jev_score.load_icp()['business_name'])"],
            env, tmp)
    assert "Latch the Company" in r.stdout, "icp.json not picked up"
    r = run([py, "-c",
             "import jev_score;"
             " icp = jev_score._parse_simple_yaml(open('icp.example.yaml').read());"
             " print('icp.yaml angles:', sorted(icp['angles']))"],
            env, tmp)
    assert "phone_first" in r.stdout, "icp.yaml mini-parser broken"

    print("\n=== 11. verify DB contents ===")
    db = tmp / "outreach.db"
    assert db.exists(), "outreach.db was not created"
    con = sqlite3.connect(db)
    leads = con.execute(
        "SELECT id, business, fit_score, angle, skipped FROM leads ORDER BY id"
    ).fetchall()
    assert len(leads) == 5, f"expected 5 leads, got {len(leads)}"
    by_id = {r[0]: r for r in leads}
    assert by_id[5][4] == 1, "MegaBite (chain) should be skipped"
    assert by_id[5][2] is not None and by_id[5][2] < 0.5, "skipped lead score wrong"
    for lid in (1, 2, 3, 4):
        assert by_id[lid][4] == 0, f"lead {lid} wrongly skipped"
        assert by_id[lid][2] is not None and by_id[lid][2] > 0.5, \
            f"lead {lid} score wrong: {by_id[lid][2]}"
    n_log = con.execute("SELECT COUNT(*) FROM lead_score_log").fetchone()[0]
    assert n_log == 5, f"expected 5 score log rows, got {n_log}"
    n_tri = con.execute("SELECT COUNT(*) FROM reply_triage").fetchone()[0]
    assert n_tri == 1, f"expected 1 triage row, got {n_tri}"
    blob = " ".join(
        str(v) for row in con.execute(
            "SELECT business, contact, channel, segment, notes FROM leads")
        for v in row
    ).lower()
    for bad in FORBIDDEN:
        assert bad not in blob, f"LEAK: {bad!r} found in demo DB"
    con.close()

    print("\nALL DEMO CHECKS PASSED")
    print(f"throwaway dir (inspect if you like): {tmp}")


if __name__ == "__main__":
    main()
