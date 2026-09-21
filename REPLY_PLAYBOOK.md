# Reply Playbook - draft templates for the triage loop (DEMO COPY)

Single source of truth for reply drafts. `jev_triage.py` parses the fenced
blocks below (tagged ````draft:key ````) at runtime. Edit the copy here and the
triage loop picks it up. Slots: `{business}`, `{contact}`, `{first}`.

This is demo copy for a fictional business ("Demo Coffee Roasters"). Rewrite
it in your own voice before using it on real replies.

Voice rules: short, reactive, your voice. Proof where it fits.
No pitchiness, no em dashes.

## Positive: book the call

```draft:book_call
Love it {first}. Got 10 minutes tomorrow? I will show you what we did for a cafe across town, phones answered and orders coming in, and what a version for {business} looks like. What time works?
```

## Objection counters

```draft:counter_price
Totally get it {first}. Quick math though: if the AI catches even a few missed orders a week during the rush, that is the month paid for. A cafe across town is live on it right now. Worth a 10 minute call to see the numbers for {business}?
```

```draft:counter_timing
No pressure on timing. Most owners tell me that, then the next Saturday rush hits and the phone rings off the hook. I can have a preview ready for {business} this week, you look at it whenever. Fair?
```

```draft:counter_trust
Fair, you have never heard of me. I am local, Springfield, and a cafe across town is running my system right now. Phones answered by AI, orders straight to the counter. Come see it live or I will show you on a call, your pick.
```

```draft:counter_has_vendor
Makes sense. One question: does your current setup answer the phone during the morning rush? That is the one thing most vendors do not do. If yours does, I will leave you alone.
```

```draft:counter_not_decision_maker
Appreciate you pointing me the right way {first}. Who handles that side of things? Happy to send them the 2 minute version.
```

```draft:counter_diy
Respect. Most DIY sites do not take orders straight to the counter or answer the phones though. Want me to show you what that looks like for {business}? Takes me a day.
```

```draft:counter_other
Hear you {first}. What is the main thing holding you back? If I can solve that piece, does it make sense to talk?
```

## Question answers

```draft:answer_pricing
$250 setup, $149 a month, no contract. That covers the AI answering your phones and the website that sends orders to the counter. A cafe across town is paying it right now.
```

```draft:answer_how_it_works
AI answers every call, takes the order, fires it to your counter display. The website does the same thing online. Nothing for your staff to learn.
```

```draft:answer_who_are_you
Alex Rivera, founder of Demo Coffee Roasters, Springfield. I build AI phones and order-taking websites for independent cafes. (Demo bio: replace with yours.)
```

```draft:answer_contract
Month to month. If it does not earn its keep, you cancel. I would rather lose a client than lock one in.
```

```draft:answer_other
Good question {first}. Short answer: I will show you faster than I can type it. Got 10 minutes this week?
```

## Wrong person: reroute

```draft:reroute
Thanks {first}. Who would be the right person to talk to about the phones and website? I will keep it to 2 minutes for them.
```

## Out of office: note only (no draft sent)

```draft:ooo_note
Auto-reply received. Re-ping after the away window with a fresh angle, do not stack on the OOO thread.
```
