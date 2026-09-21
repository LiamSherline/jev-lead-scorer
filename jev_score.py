#!/usr/bin/env python3
"""Jev lead scorer.

One Jev call per lead: fit score 0-1, outreach angle, skip flag. Stdlib only.

Transports, in order:
  1. TYPESAFE_API_KEY env var -> direct HTTPS call to the Jev System One API
  2. TYPESAFE_CLI env var     -> your own connector CLI (must accept
                                 `systemone --file <json>` and print JSON)
  3. JEV_STUB=1               -> deterministic fake answers, zero network

Your ICP lives in icp.json (copy icp.example.json to get started). Without
it, the built-in demo ICP is used.

Usage:
  JEV_STUB=1 python3 jev_score.py   # deterministic fake answers, no network
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"

# Fallback ICP used when no icp.json / icp.yaml is present. Clearly fake,
# so the repo runs a sensible demo out of the box.
DEMO_ICP = {
    "business_name": "Demo Coffee Roasters",
    "offering": "AI phone answering + online-ordering website for independent cafes",
    "icp_context": (
        "Demo Coffee Roasters ICP: independent coffee shops and cafes in the "
        "Springfield metro area with no online ordering of their own. Owner "
        "required. Permanently exclude chains with 5 or more locations."
    ),
    "research_context": (
        "Demo Coffee Roasters sells AI phone answering + online-ordering "
        "websites to independent coffee shops and cafes in the Springfield "
        "metro area. Ideal targets have NO website or NO online ordering of "
        "their own. A DoorDash/UberEats listing is not their own ordering. "
        "Permanently exclude chains with 5 or more locations."
    ),
    "angles": {
        "proof_first": "lead with the demo client result",
        "phone_first": "lead with missed calls during the morning rush",
        "website_teardown": "lead with the free sample roast offer",
    },
}


def _parse_simple_yaml(text):
    """Parse the tiny YAML subset used by icp.yaml.

    Supports top-level `key: value` scalars, `key: |` literal blocks, and one
    nested mapping (angles:). Anything fancier belongs in icp.json.
    """
    data = {}
    cur_key, cur_block, cur_map = None, None, None

    def flush():
        if cur_key is None:
            return
        if cur_block is not None:
            data[cur_key] = "\n".join(cur_block).strip()
        elif cur_map is not None:
            data[cur_key] = cur_map

    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        if raw[0] not in (" ", "\t"):
            flush()
            if ":" not in raw:
                raise ValueError(f"bad line in icp.yaml: {raw}")
            k, v = raw.split(":", 1)
            k, v = k.strip(), v.strip()
            cur_key, cur_block, cur_map = k, None, None
            if v == "|":
                cur_block = []
            elif v == "":
                cur_map = {}
            else:
                data[k] = v.strip('"').strip("'")
                cur_key = None
        else:
            line = raw.strip()
            if cur_block is not None:
                cur_block.append(line)
            elif cur_map is not None and ":" in line:
                k2, v2 = line.split(":", 1)
                cur_map[k2.strip()] = v2.strip().strip('"').strip("'")
    flush()
    return data


def load_icp():
    """Load icp.json (preferred) or icp.yaml next to this file, else DEMO_ICP."""
    here = Path(__file__).parent
    pj = here / "icp.json"
    if pj.exists():
        data = json.loads(pj.read_text())
    else:
        py = here / "icp.yaml"
        if py.exists():
            data = _parse_simple_yaml(py.read_text())
        else:
            return dict(DEMO_ICP)
    merged = dict(DEMO_ICP)
    merged.update({k: v for k, v in data.items() if v})
    if isinstance(data.get("angles"), dict) and data["angles"]:
        merged["angles"] = data["angles"]
    return merged


def build_questions(icp=None):
    icp = icp or load_icp()
    ctx = icp["icp_context"]
    angles = icp["angles"]
    # Score takes criteria as an ORDERED ARRAY of level descriptions (0..n-1).
    # Choice and noul take criteria as dicts. See docs.typesafe.ai/primitives.
    return {
        "fit_score": {
            "type": "score",
            "question": f"How strong a fit is this business for {icp['offering']}?",
            "instructions": ctx,
            "criteria": [
                "No fit: excluded by the ICP or completely wrong business.",
                "Weak fit: right industry but major disqualifiers (already has everything we sell, far outside geography).",
                "Possible fit: right industry, some positive signals, but thin evidence.",
                "Strong fit: matches the ICP, clear positive signals (good reviews, missing what we sell).",
                "Perfect fit: textbook ICP, owner reachable, strong reviews, clear pain we solve.",
            ],
        },
        "outreach_angle": {
            "type": "choice",
            "question": "Which outreach angle fits this lead best?",
            "instructions": ctx,
            "criteria": dict(angles),
        },
        "should_skip": {
            "type": "noul",
            "question": "This lead violates the ICP and should never be contacted.",
            "instructions": ctx,
            "criteria": {
                "true": "violates the ICP - never contact",
                "false": "does not violate the ICP - fine to contact",
            },
        },
    }


def build_state(lead, icp=None):
    icp = icp or load_icp()
    return {
        "business": lead.get("business"),
        "segment": lead.get("segment"),
        "contact": lead.get("contact"),
        "channel": lead.get("channel"),
        "notes": lead.get("notes"),
        "context": icp["icp_context"],
    }


def stub_answer(lead):
    notes = (lead.get("notes") or "").lower()
    skip = "[exclude]" in notes
    return {
        "fit_score": 0.2 if skip else 0.75,
        "angle": None if skip else "phone_first",
        "skipped": skip,
        "confidence": 0.9,
        "ms": 0,
    }


def call_jev_direct(state, questions, timeout=20):
    key = os.environ["TYPESAFE_API_KEY"]
    body = json.dumps({"model": JEV_MODEL, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(
        JEV_URL, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return data, int((time.time() - t0) * 1000)


def cli_cmd():
    """Command for the optional TYPESAFE_CLI connector. None if unset/missing."""
    cli = os.environ.get("TYPESAFE_CLI")
    if not cli:
        return None
    p = Path(cli)
    if not p.exists():
        return None
    return [sys.executable, str(p)] if p.suffix == ".py" else [str(p)]


def call_jev_via_cli(state, questions, timeout=90):
    """One Jev call via your own connector CLI.

    Set TYPESAFE_CLI to a program that accepts `systemone --file <json>`
    and prints the Jev response JSON to stdout. The key stays wherever your
    connector keeps it; this script never sees it.
    """
    import tempfile
    cmd = cli_cmd()
    if not cmd:
        raise RuntimeError("TYPESAFE_CLI is not set or points at nothing")
    body = {"model": JEV_MODEL, "state": state, "questions": questions}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f)
        path = f.name
    try:
        t0 = time.time()
        r = subprocess.run(cmd + ["systemone", "--file", path],
                           capture_output=True, text=True, timeout=timeout)
        ms = int((time.time() - t0) * 1000)
        if r.returncode != 0:
            raise RuntimeError(f"typesafe CLI failed: {r.stderr[:200]}")
        return json.loads(r.stdout), ms
    finally:
        os.unlink(path)


def parse_answers(data, n_angles=None):
    answers = data.get("answers", {})
    fit = answers.get("fit_score", {})
    ang = answers.get("outreach_angle", {})
    skp = answers.get("should_skip", {})

    score = fit.get("score", fit.get("value"))
    try:
        # score comes back on the 0..(n-1) level scale; normalize to 0..1
        n_levels = len((fit.get("legend") or {})) or 5
        score = float(score) / max(n_levels - 1, 1)
        score = min(max(score, 0.0), 1.0)
    except (TypeError, ValueError):
        score = None

    angle = ang.get("choice")
    valid = list((n_angles or {}).keys()) or ["proof_first", "phone_first",
                                             "website_teardown"]
    if angle not in valid:
        angle = None

    raw_skip = skp.get("noul", skp.get("choice", skp.get("value")))
    if isinstance(raw_skip, bool):
        skipped = raw_skip
    elif isinstance(raw_skip, (int, float)):
        skipped = float(raw_skip) >= 0.5  # noul returns P(yes)
    elif isinstance(raw_skip, str):
        skipped = raw_skip.strip().lower() in ("true", "yes", "1", "skip")
    else:
        skipped = False

    conf = fit.get("confidence", ang.get("confidence", 0))
    return score, angle, skipped, conf


def score_lead(lead, icp=None):
    """Returns dict(fit_score, angle, skipped, confidence, ms) or None on failure."""
    icp = icp or load_icp()
    if os.environ.get("JEV_STUB") == "1":
        return stub_answer(lead)
    state, questions = build_state(lead, icp), build_questions(icp)
    try:
        if os.environ.get("TYPESAFE_API_KEY"):
            data, ms = call_jev_direct(state, questions)
        elif cli_cmd():
            data, ms = call_jev_via_cli(state, questions)
        else:
            print("no TYPESAFE_API_KEY and no TYPESAFE_CLI; "
                  "set one or use JEV_STUB=1", file=sys.stderr)
            return None
        score, angle, skipped, conf = parse_answers(data, icp["angles"])
        if score is None:
            print(f"unparsable Jev answer for lead {lead.get('id')}", file=sys.stderr)
            return None
        return {"fit_score": score, "angle": angle, "skipped": skipped,
                "confidence": conf, "ms": ms}
    except Exception as e:
        print(f"Jev call failed for lead {lead.get('id')}: {e}", file=sys.stderr)
        return None


if __name__ == "__main__":
    demo = {"id": 0, "business": "Demo Diner", "segment": "restaurant",
            "contact": "Owner", "channel": "fb_dm", "notes": "4.6 stars, no website"}
    print(json.dumps(score_lead(demo), indent=2))
