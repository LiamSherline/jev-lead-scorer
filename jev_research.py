#!/usr/bin/env python3
"""Jev lead researcher.

The overnight enrichment loop, split in two halves:
  1. Evidence gathering (done by an agent with browser tools): search the web
     for the business, find its website + social pages, grab homepage text.
  2. Judgment (this script): Jev reads the evidence and decides what it means.

Jev cannot see a screen or click. The loop is: code/agent reads the page,
summarizes it into evidence, Jev judges, code writes the verdict to the DB.
That is the honest shape of "Jev controlling the browser."

Usage:
  python3 jev_research.py --evidence evidence.json
  JEV_STUB=1 python3 jev_research.py --evidence evidence.json  # no network

evidence.json: {"lead_id": 3, "business": "...", "website": "https://...",
  "facebook": "https://...", "website_text": "...", "search_notes": "..."}

Transports: TYPESAFE_API_KEY env, or TYPESAFE_CLI pointing at your own
connector. No keys in this script.
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from jev_score import (  # noqa: E402
    JEV_MODEL, call_jev_direct, call_jev_via_cli, cli_cmd, load_icp,
)

DB = Path(__file__).parent / "outreach.db"


def build_questions(icp=None):
    icp = icp or load_icp()
    ctx = icp["research_context"]
    return {
        "has_website": {
            "type": "noul",
            "question": "This business has its own working website.",
            "instructions": ctx,
            "criteria": {
                "true": "a real business website was found (not just a social page or directory listing)",
                "false": "no website found, only social media or directory listings",
            },
        },
        "has_online_ordering": {
            "type": "noul",
            "question": "Does this business let customers order online for pickup or delivery through its own website?",
            "instructions": ctx,
            "criteria": {
                "true": "I can point to a working online ordering flow on their own site",
                "false": "no online ordering exists; the site is informational only (hours, menu, contact)",
            },
        },
        "owner_identifiable": {
            "type": "noul",
            "question": "The owner's name can be identified from these public sources.",
            "instructions": ctx,
            "criteria": {
                "true": "a person's name is tied to ownership (about page, news article, social page owner)",
                "false": "no owner name visible",
            },
        },
        "best_contact": {
            "type": "choice",
            "question": "What is the best first-contact path for outreach?",
            "instructions": ctx,
            "criteria": {
                "facebook_dm": "active social page that looks monitored - DM is the warmest path",
                "website_form": "website has a contact form but social looks weak or absent",
                "phone": "no good digital path - calling is the only real option",
            },
        },
    }


def build_state(ev, icp=None):
    icp = icp or load_icp()
    return {
        "business": ev.get("business"),
        "website": ev.get("website"),
        "facebook": ev.get("facebook"),
        "website_text": (ev.get("website_text") or "")[:4000],
        "search_notes": (ev.get("search_notes") or "")[:2000],
        "context": icp["research_context"],
    }


def stub_answer(ev):
    return {
        "has_website": bool(ev.get("website")),
        "has_ordering": False,
        "owner_known": False,
        "contact_path": "facebook_dm" if ev.get("facebook") else "phone",
        "confidence": 0.9,
        "ms": 0,
    }


def call_jev(state, questions, timeout=90):
    body = {"model": JEV_MODEL, "state": state, "questions": questions}
    if os.environ.get("TYPESAFE_API_KEY"):
        return call_jev_direct(state, questions, timeout=timeout)
    if cli_cmd():
        return call_jev_via_cli(state, questions, timeout=timeout)
    raise RuntimeError("no Jev transport: set TYPESAFE_API_KEY or TYPESAFE_CLI "
                       "(or JEV_STUB=1)")


def _as_tri(v):
    """Tri-state: True / False / None (uncertain).

    A noul probability near 0.5 means the model is guessing. Writing a guess
    to the DB as a fact is how pipelines go wrong. None lands as NULL and the
    lead gets flagged for human review instead.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        p = float(v)
        if 0.35 < p < 0.65:
            return None
        return p >= 0.5
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    return None


def parse_answers(data):
    a = data.get("answers", {})
    hw = a.get("has_website", {})
    ho = a.get("has_online_ordering", {})
    oi = a.get("owner_identifiable", {})
    bc = a.get("best_contact", {})
    path = bc.get("choice")
    if path not in ("facebook_dm", "website_form", "phone"):
        path = "phone"
    out = {
        "has_website": _as_tri(hw.get("noul", hw.get("value"))),
        "has_ordering": _as_tri(ho.get("noul", ho.get("value"))),
        "owner_known": _as_tri(oi.get("noul", oi.get("value"))),
        "contact_path": path,
        "confidence": hw.get("confidence", bc.get("confidence", 0)),
    }
    out["review"] = [k for k in ("has_website", "has_ordering", "owner_known")
                     if out[k] is None]
    return out


def judge(evidence, icp=None):
    if os.environ.get("JEV_STUB") == "1":
        return stub_answer(evidence)
    data, ms = call_jev(build_state(evidence, icp), build_questions(icp))
    out = parse_answers(data)
    out["ms"] = ms
    return out


def store(lead_id, ev, res):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    def b(v):
        return None if v is None else int(v)

    con = sqlite3.connect(DB)
    con.execute(
        "UPDATE leads SET website=?, facebook=?, has_website=?, has_ordering=?, "
        "owner_known=?, contact_path=?, researched_at=? WHERE id=?",
        (ev.get("website"), ev.get("facebook"),
         b(res["has_website"]), b(res["has_ordering"]),
         b(res["owner_known"]), res["contact_path"], now, lead_id),
    )
    con.commit()
    con.close()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True)
    args = ap.parse_args()
    ev = json.load(open(args.evidence))
    lead_id = ev.get("lead_id")
    if not lead_id:
        print("evidence needs lead_id", file=sys.stderr)
        sys.exit(2)
    try:
        res = judge(ev)
    except Exception as e:
        print(f"research failed for lead {lead_id}: {e}", file=sys.stderr)
        sys.exit(1)
    store(lead_id, ev, res)
    print(json.dumps({"lead_id": lead_id, **res}, indent=2))


if __name__ == "__main__":
    main()
