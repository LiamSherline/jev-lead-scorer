# jev-lead-scorer

Score sales leads with typed AI decisions instead of vibes. One Jev call per
lead returns a fit score, an outreach angle, and a skip flag, all with
confidence numbers your code can route on. Then a SQLite funnel tells you
which opener actually books meetings.

Built for cold outbound to local businesses. The same code scores leads for
any business: your ICP lives in `icp.json`, not in the code.

## Why typed decisions beat vibes

A normal LLM gives you prose: "this looks like a decent fit." You cannot
route on prose. Jev primitives return structured judgments:

- **score**: an ordered rubric (no fit / weak / possible / strong / perfect).
  The model picks a level, you get a 0-1 number plus confidence.
- **choice**: a closed set (which outreach angle?). You get the pick, the
  confidence, and per-option probabilities.
- **noul**: a boolean question ("should this lead be skipped?"). You get
  P(yes). Probabilities near 0.5 are treated as *uncertain*, written as NULL,
  and flagged for human review instead of being logged as fact.

The model judges. Code routes. Every judgment lands in SQLite, so the funnel
learns from real outcomes instead of opinions.

Jev docs: https://docs.typesafe.ai (primitives under `/primitives/`)

## What is in here

| File | What it does |
|---|---|
| `jev_score.py` | One Jev call per lead: fit score 0-1, outreach angle, skip flag. Transports + ICP config live here. |
| `jev_research.py` | Enrichment judge: reads an evidence bundle (website text, socials, notes) and decides has-website / has-ordering / owner-known / best contact path. |
| `jev_triage.py` | Reply triage: intent, objection subtype, urgency, needs-human probability, suggested move. Picks a draft from `REPLY_PLAYBOOK.md` and flags uncertain calls for review. |
| `outreach.py` | SQLite log + CLI: leads, variants, sends, outcomes, per-variant funnel, scoring, triage, inbox. |
| `followup.py` | 5-touch follow-up engine over 21 days, new angle each touch. Drafts only, nothing sends itself. |
| `demo.py` | Full end-to-end demo in stub mode (no key, no network, throwaway DB). |
| `REPLY_PLAYBOOK.md` | Draft templates the triage loop parses at runtime. Demo copy, rewrite in your voice. |

## Quickstart (no key needed)

```bash
python3 demo.py
```

That runs the whole loop in `JEV_STUB=1` stub mode: loads 5 demo leads,
scores them, logs sends and outcomes, prints the funnel, triages a reply,
researches a lead, and renders follow-up drafts. Zero network calls, temp
database, safe to run anywhere.

## With a real Jev key

Get a key at https://typesafe.ai, then:

```bash
export TYPESAFE_API_KEY=your_key_here
cp icp.example.json icp.json   # describe YOUR ideal customer, then edit it

python3 outreach.py add-lead --business "Acme Diner" --contact "Rosa" \
    --channel fb_dm --segment "diner/springfield" --notes "4.7 stars, no website"
python3 outreach.py add-variant --id A --name "Control" \
    --opener "I put together a free sample for you, mind if I send it over?"
python3 outreach.py score          # Jev-scores every unscored lead
python3 outreach.py leads --top 5  # ranked by fit score
python3 outreach.py funnel         # per-variant reply / positive / booking rates
```

No key in code, ever. It comes from `TYPESAFE_API_KEY` only.

If you have your own connector that holds the key, point `TYPESAFE_CLI` at a
program that accepts `systemone --file <json>` and prints the Jev response
JSON. The script never sees the key either way.

## Cost

Jev pricing at the time of writing: about $0.042 per million input tokens,
output free. A lead scores in roughly 1K input tokens, so 1,000 leads cost
about four cents. Triage and research calls are the same shape.

## The funnel is the point

`outreach.py funnel` prints reply rate, positive rate, and booking rate per
variant. Variants under 30 sends are flagged "directional only" so you do not
declare a winner on noise. The loop is: score with Jev, send the best first,
log every outcome, let the funnel pick the opener.

## Requirements

None. Python 3.8+ standard library only. No pip install, no dependencies.

## License

MIT. See LICENSE.
