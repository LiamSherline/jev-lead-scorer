#!/usr/bin/env python3
"""Jev reply triage.

One Jev call per inbound reply: intent, objection/question subtype, urgency,
needs-human probability, suggested move. Code routes, picks the draft from
REPLY_PLAYBOOK.md, logs everything, and flags uncertain calls for review.

Transport: TYPESAFE_API_KEY env first, then TYPESAFE_CLI, then JEV_STUB=1.
Stdlib only.

Usage:
  JEV_STUB=1 python3 jev_triage.py --text "sounds good, when can we talk?"
"""

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from jev_score import (  # noqa: E402
    call_jev_direct, call_jev_via_cli, cli_cmd, load_icp,
)

PLAYBOOK = HERE / "REPLY_PLAYBOOK.md"

INTENTS = ["positive", "objection", "brush_off", "question",
           "out_of_office", "wrong_person", "spam"]
OBJECTION_TYPES = ["price", "timing", "trust", "has_vendor",
                   "not_decision_maker", "diy", "other"]
QUESTION_TOPICS = ["pricing", "how_it_works", "who_are_you", "contract", "other"]
MOVES = ["book_call", "send_counter", "answer_question", "reroute",
         "soft_followup", "log_and_wait", "ignore"]

DEFAULT_MOVE = {
    "positive": "book_call",
    "objection": "send_counter",
    "question": "answer_question",
    "wrong_person": "reroute",
    "brush_off": "soft_followup",
    "out_of_office": "log_and_wait",
    "spam": "ignore",
}


def build_questions(icp=None):
    icp = icp or load_icp()
    ctx = icp["icp_context"]
    return {
        "reply_intent": {
            "type": "choice",
            "question": "What is the intent of this inbound reply to a cold outreach message?",
            "instructions": ctx,
            "criteria": {
                "positive": "interested, wants details, a demo, a call, or to move forward",
                "objection": "engaged but pushing back with a concern (price, timing, trust...)",
                "brush_off": "polite or blunt no: not interested, stop, remove me",
                "question": "asking something specific before deciding",
                "out_of_office": "auto-reply, away, on vacation",
                "wrong_person": "not the owner/decision maker, redirecting",
                "spam": "irrelevant solicitation, telemarketer, scam",
            },
        },
        "objection_type": {
            "type": "choice",
            "question": "If this reply contains an objection, what kind is it?",
            "instructions": ctx,
            "criteria": {
                "price": "too expensive, cannot afford, cost concern",
                "timing": "bad time, too busy, maybe later, not right now",
                "trust": "never heard of you, sounds like a scam, skeptical of claims",
                "has_vendor": "already have someone, already have a website/agency",
                "not_decision_maker": "not my call, owner decides, need to ask someone",
                "diy": "we do it ourselves, family member handles it",
                "other": "an objection that fits none of the above, or no objection present",
            },
        },
        "question_topic": {
            "type": "choice",
            "question": "If this reply asks a question, what is it about?",
            "instructions": ctx,
            "criteria": {
                "pricing": "how much does it cost",
                "how_it_works": "how does it work, what do I get, explain the product",
                "who_are_you": "who is this, who is the company",
                "contract": "contract terms, commitment, cancellation",
                "other": "a different question, or no question present",
            },
        },
        "urgency": {
            "type": "score",
            "question": "How urgently does this reply need the founder's attention?",
            "instructions": ctx,
            "criteria": [
                "Ignore: spam or noise, no action needed.",
                "Routine: brush-offs and auto-replies, can wait days.",
                "Normal: questions and mild objections, answer within a day.",
                "Hot: positive buying signals, answer today.",
                "Drop everything: wants a call now, ready to pay, or an upset customer.",
            ],
        },
        "needs_human": {
            "type": "noul",
            "question": "The founder must personally write or review the response to this reply (not a template).",
            "instructions": ctx + " Hot positives, tricky objections, and anything unusual need the founder personally.",
            "criteria": {
                "true": "needs the founder personally - hot lead, tricky objection, unusual or sensitive",
                "false": "a playbook draft is fine - routine question, standard objection, low stakes",
            },
        },
        "suggested_move": {
            "type": "choice",
            "question": "What should happen next with this reply?",
            "instructions": ctx,
            "criteria": {
                "book_call": "push for a short call now",
                "send_counter": "answer the objection with a counter",
                "answer_question": "answer the question directly",
                "reroute": "ask for the right contact",
                "soft_followup": "back off gracefully, schedule a later touch",
                "log_and_wait": "log it, re-ping after the away window",
                "ignore": "log only, do nothing",
            },
        },
    }


def build_state(reply, icp=None):
    icp = icp or load_icp()
    return {
        "reply_text": reply.get("text"),
        "thread_context": reply.get("context") or "no prior thread context",
        "business": reply.get("business"),
        "contact": reply.get("contact"),
        "channel": reply.get("channel"),
        "context": icp["icp_context"],
    }


def stub_answer(reply):
    t = (reply.get("text") or "").lower()

    def base(**kw):
        d = {"urgency": 0.5, "needs_human": 0.5, "suggested_move": None,
             "confidence": 0.9, "ms": 0}
        d.update(kw)
        return d

    if any(k in t for k in ("seo", "rank #1", "marketing agency", "viagra", "crypto")):
        return base(intent="spam", urgency=0.0, needs_human=0.0,
                    suggested_move="ignore")
    if any(k in t for k in ("out of office", "ooo", "vacation", "away until", "back on")):
        return base(intent="out_of_office", urgency=0.25, needs_human=0.05,
                    suggested_move="log_and_wait")
    if any(k in t for k in ("not the owner", "wrong person", "talk to my", "ask for")):
        return base(intent="wrong_person", question_topic="other", urgency=0.5,
                    needs_human=0.4, suggested_move="reroute")  # 0.4 -> review band
    if any(k in t for k in ("how much", "what does it cost", "pricing", "price of")):
        return base(intent="question", question_topic="pricing", urgency=0.5,
                    needs_human=0.3, suggested_move="answer_question")
    if any(k in t for k in ("how does it work", "what do you do", "explain")):
        return base(intent="question", question_topic="how_it_works", urgency=0.5,
                    needs_human=0.3, suggested_move="answer_question")
    if any(k in t for k in ("expensive", "too much", "afford", "costs too", "$")):
        return base(intent="objection", objection_type="price", urgency=0.5,
                    needs_human=0.5, suggested_move="send_counter")  # 0.5 -> review
    if any(k in t for k in ("too busy", "bad time", "later", "not right now")):
        return base(intent="objection", objection_type="timing", urgency=0.5,
                    needs_human=0.7, suggested_move="send_counter")
    if any(k in t for k in ("not interested", "no thanks", "stop", "remove me",
                            "leave me alone", "unsubscribe")):
        return base(intent="brush_off", urgency=0.25, needs_human=0.1,
                    suggested_move="soft_followup")
    if any(k in t for k in ("interested", "let's talk", "call me", "demo",
                            "sounds good", "send me", "yes")):
        return base(intent="positive", urgency=0.75, needs_human=0.9,
                    suggested_move="book_call")
    # default: uncertain -> lands in review
    return base(intent="question", question_topic="other", urgency=0.5,
                needs_human=0.5, suggested_move="answer_question", confidence=0.4)


def parse_answers(data):
    a = data.get("answers", {})

    def choice(qid, valid):
        v = a.get(qid, {}).get("choice")
        return v if v in valid else None

    def conf(qid):
        try:
            return float(a.get(qid, {}).get("confidence", 0))
        except (TypeError, ValueError):
            return 0.0

    intent = choice("reply_intent", INTENTS)
    urgency_raw = a.get("urgency", {}).get("score", a.get("urgency", {}).get("value"))
    try:
        urgency = min(max(float(urgency_raw) / 4.0, 0.0), 1.0)
    except (TypeError, ValueError):
        urgency = None

    nh = a.get("needs_human", {})
    nh_raw = nh.get("noul", nh.get("choice", nh.get("value")))
    if isinstance(nh_raw, bool):
        needs_human = 1.0 if nh_raw else 0.0
    elif isinstance(nh_raw, (int, float)):
        needs_human = float(nh_raw)
    elif isinstance(nh_raw, str):
        needs_human = 1.0 if nh_raw.strip().lower() in ("true", "yes", "1") else 0.0
    else:
        needs_human = None

    ms = data.get("usage", {}).get("ms", 0)
    return {
        "intent": intent,
        "intent_conf": conf("reply_intent"),
        "objection_type": choice("objection_type", OBJECTION_TYPES),
        "question_topic": choice("question_topic", QUESTION_TOPICS),
        "urgency": urgency,
        "needs_human": needs_human,
        "suggested_move": choice("suggested_move", MOVES),
        "ms": ms,
    }


def load_templates():
    """Parse ````draft:key blocks from REPLY_PLAYBOOK.md."""
    text = PLAYBOOK.read_text()
    found = {}
    for m in re.finditer(r"```draft:([a-z_]+)\n(.*?)```", text, re.S):
        found[m.group(1)] = m.group(2).strip()
    return found


def draft_key(intent, objection_type, question_topic):
    if intent == "positive":
        return "book_call"
    if intent == "objection":
        return f"counter_{objection_type or 'other'}"
    if intent == "question":
        return f"answer_{question_topic or 'other'}"
    if intent == "wrong_person":
        return "reroute"
    if intent == "out_of_office":
        return "ooo_note"
    return None


def render_draft(reply, parsed, templates):
    key = draft_key(parsed["intent"], parsed.get("objection_type"),
                    parsed.get("question_topic"))
    if not key:
        return None, None
    tpl = templates.get(key) or templates.get(
        "counter_other" if key.startswith("counter_") else "answer_other")
    if not tpl:
        return key, None
    contact = reply.get("contact") or ""
    first = contact.split()[0] if contact.split() else "there"
    try:
        draft = tpl.format(business=reply.get("business") or "your place",
                           contact=contact, first=first)
    except KeyError:
        draft = tpl
    return key, draft


def triage_reply(reply, icp=None):
    """Full pipeline: Jev judgments -> move -> draft -> review flags.

    reply: {lead_id, send_id, channel, business, contact, text, context}
    Returns dict with everything the DB row needs, or None on failure.
    """
    if os.environ.get("JEV_STUB") == "1":
        s = stub_answer(reply)
        parsed = {
            "intent": s["intent"], "intent_conf": s["confidence"],
            "objection_type": s.get("objection_type"),
            "question_topic": s.get("question_topic"),
            "urgency": s["urgency"], "needs_human": s["needs_human"],
            "suggested_move": s["suggested_move"], "ms": 0,
        }
    else:
        state, questions = build_state(reply, icp), build_questions(icp)
        try:
            if os.environ.get("TYPESAFE_API_KEY"):
                data, ms = call_jev_direct(state, questions)
            elif cli_cmd():
                data, ms = call_jev_via_cli(state, questions)
            else:
                print("no TYPESAFE_API_KEY and no TYPESAFE_CLI; "
                      "set one or use JEV_STUB=1", file=sys.stderr)
                return None
            parsed = parse_answers(data)
            parsed["ms"] = ms
        except Exception as e:
            print(f"Jev triage failed: {e}", file=sys.stderr)
            return None

    if not parsed["intent"] or parsed["urgency"] is None or parsed["needs_human"] is None:
        print(f"unparsable Jev triage answer: {parsed}", file=sys.stderr)
        return None

    default_move = DEFAULT_MOVE[parsed["intent"]]
    final_move = parsed["suggested_move"] or default_move

    needs_review = (
        parsed["intent_conf"] < 0.60
        or 0.35 <= parsed["needs_human"] <= 0.65
        or final_move != default_move
    )

    templates = load_templates()
    tpl_key, draft = render_draft(reply, parsed, templates)

    return {
        "intent": parsed["intent"],
        "intent_conf": parsed["intent_conf"],
        "objection_type": parsed.get("objection_type"),
        "question_topic": parsed.get("question_topic"),
        "urgency": parsed["urgency"],
        "needs_human": bool(parsed["needs_human"] >= 0.65),
        "needs_human_p": parsed["needs_human"],
        "suggested_move": parsed["suggested_move"],
        "final_move": final_move,
        "needs_review": bool(needs_review),
        "draft_key": tpl_key,
        "draft": draft,
        "ms": parsed["ms"],
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--text", required=True)
    p.add_argument("--business", default="Demo Diner")
    p.add_argument("--contact", default="Alex")
    p.add_argument("--context", default="")
    a = p.parse_args()
    print(json.dumps(triage_reply({"business": a.business, "contact": a.contact,
                                   "text": a.text, "context": a.context}), indent=2))
