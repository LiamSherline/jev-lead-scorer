#!/usr/bin/env python3
"""Jev lead scorer - outreach log + funnel.

Every send gets tagged (variant, channel, touch, daypart). Every outcome gets
logged against the send. `funnel` reads the numbers the self-improvement loop
learns from. SQLite via stdlib only.

Usage:
  outreach.py add-lead --business "Name" --contact "Owner" --channel fb_dm --segment "mexican/et"
  outreach.py add-variant --id B --name "Question-led" --opener "..." [--status proposed]
  outreach.py send --lead 3 --variant A --touch 1 --batch batch-2 [--daypart "2-4pm"]
  outreach.py outcome --send 12 --kind reply [--notes "..."]
  outreach.py funnel [--days 30] [--variant A]
  outreach.py variants
  outreach.py leads [--top N] [--all]
  outreach.py score [--rescore]
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

DB = Path(__file__).with_name("outreach.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads(
  id INTEGER PRIMARY KEY,
  business TEXT NOT NULL,
  contact TEXT,
  channel TEXT,
  segment TEXT,
  notes TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS variants(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  opener TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sends(
  id INTEGER PRIMARY KEY,
  lead_id INTEGER NOT NULL REFERENCES leads(id),
  variant_id TEXT NOT NULL REFERENCES variants(id),
  channel TEXT NOT NULL,
  touch INTEGER NOT NULL DEFAULT 1,
  daypart TEXT,
  batch TEXT,
  sent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes(
  id INTEGER PRIMARY KEY,
  send_id INTEGER NOT NULL REFERENCES sends(id),
  kind TEXT NOT NULL,
  at TEXT NOT NULL,
  notes TEXT
);
"""

# outcome kinds, ordered by funnel depth
KINDS = ["delivered", "reply", "positive_reply", "meeting_set",
         "meeting_held", "closed_won", "closed_lost", "opt_out", "not_interested"]
FUNNEL_KINDS = ["reply", "positive_reply", "meeting_set"]


SCORE_SCHEMA = """
ALTER TABLE leads ADD COLUMN fit_score REAL;
ALTER TABLE leads ADD COLUMN angle TEXT;
ALTER TABLE leads ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0;
ALTER TABLE leads ADD COLUMN scored_at TEXT;
CREATE TABLE IF NOT EXISTS lead_score_log(
  id INTEGER PRIMARY KEY,
  lead_id INTEGER NOT NULL REFERENCES leads(id),
  fit_score REAL,
  angle TEXT,
  skipped INTEGER,
  confidence REAL,
  ms INTEGER,
  scored_at TEXT NOT NULL
);
"""


TRIAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS reply_triage(
  id INTEGER PRIMARY KEY,
  lead_id INTEGER REFERENCES leads(id),
  send_id INTEGER REFERENCES sends(id),
  channel TEXT,
  reply_text TEXT NOT NULL,
  thread_context TEXT,
  intent TEXT,
  intent_conf REAL,
  objection_type TEXT,
  question_topic TEXT,
  urgency REAL,
  needs_human INTEGER,
  needs_human_p REAL,
  suggested_move TEXT,
  final_move TEXT,
  needs_review INTEGER NOT NULL DEFAULT 0,
  draft_key TEXT,
  draft TEXT,
  ms INTEGER,
  triaged_at TEXT NOT NULL
);
"""


RESEARCH_SCHEMA = """
ALTER TABLE leads ADD COLUMN website TEXT;
ALTER TABLE leads ADD COLUMN facebook TEXT;
ALTER TABLE leads ADD COLUMN has_website INTEGER;
ALTER TABLE leads ADD COLUMN has_ordering INTEGER;
ALTER TABLE leads ADD COLUMN owner_known INTEGER;
ALTER TABLE leads ADD COLUMN contact_path TEXT;
ALTER TABLE leads ADD COLUMN researched_at TEXT;
"""


def _apply_alters(con, schema_sql):
    cols = {r[1] for r in con.execute("PRAGMA table_info(leads)")}
    for stmt in schema_sql.strip().split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        if stmt.startswith("ALTER TABLE leads ADD COLUMN"):
            col = stmt.split("ADD COLUMN")[1].split()[0]
            if col in cols:
                continue
        con.execute(stmt)


def db():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    _apply_alters(con, SCORE_SCHEMA)
    _apply_alters(con, RESEARCH_SCHEMA)
    con.executescript(TRIAGE_SCHEMA)
    con.commit()
    return con


def now():
    return datetime.now().isoformat(timespec="seconds")


def cmd_add_lead(a):
    con = db()
    cur = con.execute(
        "INSERT INTO leads(business,contact,channel,segment,notes,created_at)"
        " VALUES(?,?,?,?,?,?)",
        (a.business, a.contact, a.channel, a.segment, a.notes, now()))
    con.commit()
    print(f"lead {cur.lastrowid}: {a.business}")
    con.close()


def cmd_add_variant(a):
    con = db()
    con.execute(
        "INSERT OR REPLACE INTO variants(id,name,opener,status,created_at)"
        " VALUES(?,?,?,?,?)",
        (a.id.upper(), a.name, a.opener, a.status, now()))
    con.commit()
    print(f"variant {a.id.upper()} [{a.status}]: {a.name}")
    con.close()


def cmd_send(a):
    con = db()
    exists = con.execute("SELECT 1 FROM leads WHERE id=?", (a.lead,)).fetchone()
    if not exists:
        sys.exit(f"no lead {a.lead}")
    v = con.execute("SELECT status FROM variants WHERE id=?",
                    (a.variant.upper(),)).fetchone()
    if not v:
        sys.exit(f"no variant {a.variant}")
    if v[0] != "active":
        print(f"warning: variant {a.variant.upper()} is '{v[0]}', logging anyway")
    cur = con.execute(
        "INSERT INTO sends(lead_id,variant_id,channel,touch,daypart,batch,sent_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (a.lead, a.variant.upper(), a.channel, a.touch, a.daypart, a.batch, now()))
    con.commit()
    print(f"send {cur.lastrowid}: lead {a.lead} variant {a.variant.upper()} touch {a.touch}")
    con.close()


def cmd_outcome(a):
    if a.kind not in KINDS:
        sys.exit(f"kind must be one of: {', '.join(KINDS)}")
    con = db()
    exists = con.execute("SELECT 1 FROM sends WHERE id=?", (a.send,)).fetchone()
    if not exists:
        sys.exit(f"no send {a.send}")
    cur = con.execute(
        "INSERT INTO outcomes(send_id,kind,at,notes) VALUES(?,?,?,?)",
        (a.send, a.kind, now(), a.notes))
    con.commit()
    print(f"outcome {cur.lastrowid}: send {a.send} -> {a.kind}")
    con.close()


def funnel_rows(con, where, params):
    rows = []
    for (vid, vname, status) in con.execute(
            "SELECT id,name,status FROM variants ORDER BY id"):
        w = where + " AND s.variant_id=?" if where else "WHERE s.variant_id=?"
        p = params + [vid]
        sends = con.execute(
            f"SELECT COUNT(*) FROM sends s {w}", p).fetchone()[0]
        if not sends:
            continue
        rates = {}
        for kind in FUNNEL_KINDS:
            n = con.execute(
                f"""SELECT COUNT(DISTINCT s.id) FROM sends s
                    JOIN outcomes o ON o.send_id=s.id AND o.kind=?
                    {w}""", [kind] + p).fetchone()[0]
            rates[kind] = (n, n / sends if sends else 0)
        rows.append((vid, vname, status, sends, rates))
    return rows


def cmd_funnel(a):
    con = db()
    where, params = "", []
    if a.days:
        cutoff = (datetime.now() - timedelta(days=a.days)).isoformat()
        where, params = "WHERE s.sent_at >= ?", [cutoff]
    if a.variant:
        extra = " AND s.variant_id=?" if where else "WHERE s.variant_id=?"
        where, params = where + extra, params + [a.variant.upper()]
    label = f"last {a.days}d" if a.days else "all time"
    print(f"funnel ({label})")
    print(f"{'var':<5}{'sends':>7}{'reply%':>8}{'pos%':>7}{'book%':>7}  name")
    for vid, vname, status, sends, rates in funnel_rows(con, where, params):
        r = rates["reply"][1] * 100
        p = rates["positive_reply"][1] * 100
        b = rates["meeting_set"][1] * 100
        flag = "" if sends >= 30 else "  (n<30: directional only)"
        print(f"{vid:<5}{sends:>7}{r:>7.1f}%{p:>6.1f}%{b:>6.1f}%  {vname} [{status}]{flag}")
    con.close()


def cmd_variants(a):
    con = db()
    for vid, name, opener, status in con.execute(
            "SELECT id,name,opener,status FROM variants ORDER BY id"):
        print(f"[{status}] {vid}: {name}\n    {opener}\n")
    con.close()


def cmd_leads(a):
    con = db()
    top = a.top
    if top:
        q = ("SELECT id,business,contact,channel,segment,fit_score,angle,skipped"
             " FROM leads")
        if not a.all:
            q += " WHERE skipped=0"
        q += " ORDER BY fit_score DESC NULLS LAST, id LIMIT ?"
        rows = con.execute(q, (top,))
        print(f"top {top} leads by fit score")
        for lid, biz, contact, channel, segment, fs, angle, skipped in rows:
            mark = "SKIP " if skipped else ""
            print(f"{mark}{lid}: {biz} [{fs if fs is not None else '-':>}]"
                  f" {angle or '-'} ({contact or '?'}, {segment or '?'})")
    else:
        for lid, biz, contact, channel, segment in con.execute(
                "SELECT id,business,contact,channel,segment FROM leads ORDER BY id"):
            print(f"{lid}: {biz} ({contact or '?'}, {channel or '?'}, {segment or '?'})")
    con.close()


def cmd_score(a):
    import time
    sys.path.insert(0, str(Path(__file__).parent))
    from jev_score import score_lead
    con = db()
    q = "SELECT id,business,contact,channel,segment,notes FROM leads"
    if not a.rescore:
        q += " WHERE fit_score IS NULL"
    q += " ORDER BY id"
    leads = [dict(zip(["id", "business", "contact", "channel", "segment", "notes"], r))
             for r in con.execute(q)]
    if not leads:
        print("nothing to score")
        con.close()
        return
    print(f"scoring {len(leads)} leads...")
    done, failed = 0, 0
    for lead in leads:
        res = score_lead(lead)
        if not res:
            failed += 1
            continue
        ts = now()
        con.execute(
            "UPDATE leads SET fit_score=?, angle=?, skipped=?, scored_at=? WHERE id=?",
            (res["fit_score"], res["angle"], int(res["skipped"]), ts, lead["id"]))
        con.execute(
            "INSERT INTO lead_score_log(lead_id,fit_score,angle,skipped,confidence,ms,scored_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (lead["id"], res["fit_score"], res["angle"], int(res["skipped"]),
             res["confidence"], res["ms"], ts))
        con.commit()
        done += 1
        print(f"  {lead['business']}: {res['fit_score']:.2f}"
              f" {res['angle'] or '-'}{' SKIP' if res['skipped'] else ''}")
        time.sleep(2)  # stay far under rate limits
    con.close()
    print(f"done: {done} scored, {failed} failed")


def cmd_triage(a):
    sys.path.insert(0, str(Path(__file__).parent))
    from jev_triage import triage_reply
    con = db()
    reply = {"lead_id": a.lead, "send_id": a.send, "channel": a.channel,
             "text": a.text, "context": a.context}
    if a.lead:
        row = con.execute("SELECT business, contact FROM leads WHERE id=?",
                          (a.lead,)).fetchone()
        if not row:
            sys.exit(f"no lead {a.lead}")
        reply["business"], reply["contact"] = row
    res = triage_reply(reply)
    if not res:
        sys.exit("triage failed")
    ts = now()
    cur = con.execute(
        """INSERT INTO reply_triage(lead_id, send_id, channel, reply_text,
           thread_context, intent, intent_conf, objection_type, question_topic,
           urgency, needs_human, needs_human_p, suggested_move, final_move,
           needs_review, draft_key, draft, ms, triaged_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (a.lead, a.send, a.channel, a.text, a.context, res["intent"],
         res["intent_conf"], res["objection_type"], res["question_topic"],
         res["urgency"], int(res["needs_human"]), res["needs_human_p"],
         res["suggested_move"], res["final_move"], int(res["needs_review"]),
         res["draft_key"], res["draft"], res["ms"], ts))
    tid = cur.lastrowid
    # auto-log the outcome on the linked send so the funnel learns from triage
    if a.send and res["intent"] in ("positive", "objection", "question",
                                    "brush_off", "wrong_person"):
        kinds = ["reply"] + (["positive_reply"] if res["intent"] == "positive"
                              else [])
        for kind in kinds:
            con.execute("INSERT INTO outcomes(send_id, kind, at, notes)"
                        " VALUES(?,?,?,?)",
                        (a.send, kind, ts, f"triage {tid}: {res['intent']}"))
    con.commit()
    flag = "REVIEW " if res["needs_review"] else ""
    print(f"triage {tid}: {flag}{res['intent']} (conf {res['intent_conf']:.2f})"
          f" urgency {res['urgency']:.2f} -> {res['final_move']}")
    if res["draft"]:
        print(f"--- draft [{res['draft_key']}] ---")
        print(res["draft"])
    con.close()


def cmd_triage_batch(a):
    import json
    import time
    sys.path.insert(0, str(Path(__file__).parent))
    from jev_triage import triage_reply
    items = json.loads(Path(a.file).read_text())
    con = db()
    done, failed = 0, 0
    for item in items:
        reply = {"lead_id": item.get("lead_id"), "send_id": item.get("send_id"),
                 "channel": item.get("channel"), "text": item.get("text"),
                 "context": item.get("context") or ""}
        lid = reply["lead_id"]
        if lid:
            row = con.execute("SELECT business, contact FROM leads WHERE id=?",
                              (lid,)).fetchone()
            if row:
                reply["business"], reply["contact"] = row
        res = triage_reply(reply)
        if not res:
            failed += 1
            continue
        ts = now()
        con.execute(
            """INSERT INTO reply_triage(lead_id, send_id, channel, reply_text,
               thread_context, intent, intent_conf, objection_type, question_topic,
               urgency, needs_human, needs_human_p, suggested_move, final_move,
               needs_review, draft_key, draft, ms, triaged_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lid, reply["send_id"], reply["channel"], reply["text"],
             reply["context"], res["intent"], res["intent_conf"],
             res["objection_type"], res["question_topic"], res["urgency"],
             int(res["needs_human"]), res["needs_human_p"], res["suggested_move"],
             res["final_move"], int(res["needs_review"]), res["draft_key"],
             res["draft"], res["ms"], ts))
        con.commit()
        done += 1
        flag = "REVIEW " if res["needs_review"] else ""
        print(f"  {flag}{res['intent']} -> {res['final_move']}:"
              f" {(reply['text'] or '')[:60]}")
        time.sleep(2)  # stay far under rate limits
    con.close()
    print(f"done: {done} triaged, {failed} failed")


def cmd_inbox(a):
    con = db()
    q = """SELECT t.id, l.business, t.intent, t.urgency, t.final_move,
                  t.needs_review, t.draft_key, substr(t.draft, 1, 120),
                  t.triaged_at
           FROM reply_triage t LEFT JOIN leads l ON l.id = t.lead_id"""
    if a.hot:
        q += " ORDER BY t.needs_review DESC, t.urgency DESC, t.triaged_at DESC"
    else:
        q += " ORDER BY t.triaged_at DESC"
    rows = list(con.execute(q))
    if not rows:
        print("inbox empty")
        con.close()
        return
    for tid, biz, intent, urg, move, review, dkey, dprev, ts in rows:
        flag = "REVIEW " if review else ""
        print(f"{tid}: {flag}{intent} [{urg:.2f}] -> {move}"
              f" ({biz or '?'}, {ts})")
        if dkey:
            print(f"    [{dkey}] {(dprev or '').replace(chr(10), ' ')}...")
    con.close()


def main():
    p = argparse.ArgumentParser(description="Jev lead scorer: outreach log + funnel")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add-lead")
    s.add_argument("--business", required=True)
    s.add_argument("--contact")
    s.add_argument("--channel")
    s.add_argument("--segment")
    s.add_argument("--notes")

    s = sub.add_parser("add-variant")
    s.add_argument("--id", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--opener", required=True)
    s.add_argument("--status", default="active",
                   choices=["active", "proposed", "retired"])

    s = sub.add_parser("send")
    s.add_argument("--lead", type=int, required=True)
    s.add_argument("--variant", required=True)
    s.add_argument("--channel", required=True)
    s.add_argument("--touch", type=int, default=1)
    s.add_argument("--daypart")
    s.add_argument("--batch")

    s = sub.add_parser("outcome")
    s.add_argument("--send", type=int, required=True)
    s.add_argument("--kind", required=True)
    s.add_argument("--notes")

    s = sub.add_parser("funnel")
    s.add_argument("--days", type=int)
    s.add_argument("--variant")

    sub.add_parser("variants")

    s = sub.add_parser("leads")
    s.add_argument("--top", type=int, help="rank top N by Jev fit score")
    s.add_argument("--all", action="store_true", help="include skipped leads")

    s = sub.add_parser("score", help="score unscored leads with Jev")
    s.add_argument("--rescore", action="store_true", help="re-score every lead")

    s = sub.add_parser("triage", help="triage one inbound reply with Jev")
    s.add_argument("--lead", type=int)
    s.add_argument("--send", type=int)
    s.add_argument("--channel")
    s.add_argument("--text", required=True)
    s.add_argument("--context", default="")

    s = sub.add_parser("triage-batch", help="triage replies from a JSON file")
    s.add_argument("--file", required=True)

    s = sub.add_parser("inbox", help="show triaged replies")
    s.add_argument("--hot", action="store_true",
                   help="urgency first, review flags on top")

    a = p.parse_args()
    {"add-lead": cmd_add_lead, "add-variant": cmd_add_variant, "send": cmd_send,
     "outcome": cmd_outcome, "funnel": cmd_funnel, "variants": cmd_variants,
     "leads": cmd_leads, "score": cmd_score, "triage": cmd_triage,
     "triage-batch": cmd_triage_batch, "inbox": cmd_inbox}[a.cmd](a)


if __name__ == "__main__":
    main()
