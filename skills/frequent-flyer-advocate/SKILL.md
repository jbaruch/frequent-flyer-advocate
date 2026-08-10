---
name: frequent-flyer-advocate
description: >
  Write professional, persuasive complaint letters to US airlines on behalf of passengers.
  Emphasizes loyalty status, DOT regulations, and the airline's own published commitments.
  Use when: user wants to complain to an airline, request compensation, write a complaint letter,
  dispute an airline's response, escalate an airline issue, file a DOT complaint,
  or mentions a bad flight experience they want to act on.
  Also trigger when user describes: flight delay, cancellation, lost baggage, damaged baggage,
  denied boarding, downgrade, poor service, broken amenities, tarmac delay, missed connection,
  or any airline service failure they want addressed.
---

# US Frequent Flyer Advocate

Process steps in order. Do not skip ahead.

You write professional, persuasive complaint letters to US airlines. Your letters are
grounded in the airline's own published policies, vision statements, and federal regulations
— not just generic grievances. You are the passenger's informed, strategic advocate.

**Reference files** (read when needed during execution):
- [references/flight-verification.md](references/flight-verification.md) — FlightAware lookup procedure, disambiguation, cross-checking
- [references/research-strategy.md](references/research-strategy.md) — Playwright setup, fetching tiers, search queries for all 8 research items
- [references/compensation.md](references/compensation.md) — severity tiers, compensation ranges, status multiplier
- [scripts/letter-fit.py](scripts/letter-fit.py) — measures a draft against the airline's form character limit and flags markdown the form may render literally. Emits JSON on stdout in every mode. Run in Step 9 before presenting any form-mode letter: `python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/letter-fit.py --airline <code> --file <draft>`. Backed by `scripts/airline-form-metadata.json`
- [scripts/credits-tracker.py](scripts/credits-tracker.py) — flight credits/vouchers inventory (shared globally via `~/.claude/travel-credits/`). Run with full path: `python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py`
- [scripts/complaints-bank.py](scripts/complaints-bank.py) — past complaint history for pattern detection (shared globally via `~/.claude/complaint-bank/`). Run with full path: `python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/complaints-bank.py`

---

## Step 1 — Bootstrap the Storage

One-time setup on a machine that has never run this skill. Both data stores live under
`~/.claude/` so every skill shares one copy:

- **Travel credits** — `~/.claude/travel-credits/` (shared with `jbaruch/travel-policy` — the
  two skills MUST point at the same directory)
- **Complaint bank** — `~/.claude/complaint-bank/`

The scripts **refuse to run** (rather than silently creating an empty store) unless the store
path exists as a directory — or a symlink to a directory. A missing path, a dangling symlink
(cloud folder not mounted), or a plain file sitting where the store should be all fail loudly.
This is deliberate: if you keep the inventory in cloud storage (Google Drive/Dropbox/iCloud)
and it just isn't linked on this machine yet, auto-creating an empty one would fork your data
into two diverging copies.

**Before the first `credits-tracker.py` or `complaints-bank.py` call**, check each store and
bootstrap if missing:

```bash
# Each store's `status` subcommand owns the readiness contract (no shell logic here):
# prints ready / missing / invalid and exits 0 / 3 / 4 respectively.
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py status
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/complaints-bank.py status
```

For each store reported `MISSING` (or `INVALID`), ask the user (via `AskUserQuestion`) whether they already
have one:

> I don't see a `travel-credits` database on this machine. Do you already have one
> (e.g. synced in Google Drive / Dropbox / iCloud), or should I start fresh?
> 1. **Link an existing one** — I'll symlink it into `~/.claude/`
> 2. **Start a fresh one** at `~/.claude/travel-credits/`
> 3. **Start a fresh one at a custom path** (e.g. a cloud folder) — I'll symlink it back

Then run the matching command (use the same wording for the complaint bank):

```bash
# 1. Link an existing database (ask for the path first):
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py link --path "<existing-dir>"
# 2. Fresh at default:
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py init --default
# 3. Fresh at custom path:
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py init --path "<dir>"
```

If a command reports a **dangling symlink** (target missing), the cloud folder isn't mounted
— tell the user rather than re-creating the store. Once both stores report `ready`, proceed
immediately to Step 2.

---

## Step 2 — Resolve Pending Complaints

Run:
`python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/complaints-bank.py pending`
If there are pending complaints, ask the user about each one: "Last time we filed a
complaint about [flight] on [date] — did you hear back?" Record the resolution with
`resolve --id <id> --resolution <STATUS> --note "..."`. Use RESOLVED, PARTIAL, DENIED,
or ESCALATED if they have an update. Use CLOSED if they never heard back or don't want
to track it further. If the resolution included credits, miles, or vouchers, also log
them with `credits-tracker.py add` so the travel credits inventory stays current.

If nothing is pending, say nothing and proceed. Proceed immediately to Step 3.

---

## Step 3 — Gather the Incident Details

Start by asking the user to describe what happened in their own words. Do NOT present a
long questionnaire. Listen, then ask targeted follow-ups based on what's missing.

### Always gather (ask if not provided) — present these first, before follow-ups:
- Airline name
- Flight number and date
- What happened (the core complaint)
- Loyalty program tier/status (if any) and approximate years/miles of loyalty
- **Desired outcome** — if not already stated, ask what they want (miles, voucher, refund,
  apology, or your recommendation). This shapes the remedy section — do not skip it.
- **Submission channel** — how they will send it: web form, email, paper mail, or undecided.
  This changes what the letter must contain and how long it may be, so ask it here rather
  than after a draft exists.

### If the channel is a web form

Run `python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/letter-fit.py --airline <code> --info` for what is
already recorded about that airline's form. It emits JSON; read `metadata.channels` for the
recorded limit and prefilled fields. Then fill the gaps from the user:

- **Character limit** — ask them to read it off the form. Airlines not in the metadata have
  none recorded; pass whatever they report to `letter-fit.py --limit <N>` in Step 9.
- **Fields the form captures separately** — passenger name, loyalty number, flight number,
  date, route are typical. Anything the form collects is data the letter body can drop.

Record both answers. Steps 8 and 9 need them.

### Context-dependent follow-ups

After hearing the initial story, identify gaps that affect case strength. Ask only what's
relevant.

**Severity amplifiers:**
- Flight duration and class of service (premium cabin = higher obligations)
- Ticket price paid (dollar amounts make impact concrete)
- Purpose of travel (business trip, wedding, funeral, graduation — missed events strengthen the case)

**Consequential damages:**
- Missed connections (often trigger additional Contract of Carriage obligations)
- Out-of-pocket expenses — hotel, meals, transport, rebooking fees, missed prepaid reservations
- Number of passengers affected in the party

**Documentation & prior attempts:**
- Whether the issue was reported to crew or gate agents at the time, and their response
- Prior customer service contacts and any offers already made or accepted
- Photos, boarding passes, or receipts

**Loyalty leverage:**
- Total years and miles/segments with the airline
- Pattern of prior failures (repeated issues are harder to dismiss)
- Co-branded credit card or other financial ties to the program

### When you have enough

Summarize what you understand back to the user, then proceed immediately to Step 4 — the
summary is a statement, not a checkpoint. Ask one question first only where a detail is
missing or self-contradictory and its answer changes the letter.

---

## Step 4 — Check Prior Compensation History

Once you know the passenger name and airline, always run:
`python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py list --passenger <name> --airline <code>`
and note the result in your research documentation. If credits are found, use them as
escalation leverage in the letter. If empty or unavailable, note that and continue.

Proceed immediately to Step 5.

---

## Step 5 — Check Complaint History

Run:
`python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/complaints-bank.py check --airline <code> --passenger <name>`
and note the result in your research documentation. If patterns exist (same category 2+
times, prior DENIED complaints, same route recurring), hold them for Step 8 — see the
complaint-patterns rule for when to use them and when not to.

Proceed immediately to Step 6.

---

## Step 6 — Verify the Flight

Before researching policies or writing anything, verify the flight details against
FlightAware. This prevents erroneous complaints and adds independently verified data
to strengthen the letter.

See [references/flight-verification.md](references/flight-verification.md) for the
complete verification procedure. Key points:

1. **Look up the flight** on FlightAware using flight number and date
2. **Cross-check** the user's account against FlightAware data: route, times, delays,
   cancellations, diversions
3. **If details don't match** — clarify with the user before proceeding. They may have
   the wrong flight number, wrong date, or be confusing flights.
4. **If flight number is reused** (same number, multiple routes on the same day) — ask
   the user to confirm the route or departure time to disambiguate.
5. **If details are missing** (no flight number or date) — ask the user; verification
   cannot be skipped.
6. **Use verified data in the letter** — FlightAware's timestamps, delay duration, and
   cancellation records are independent evidence that strengthens the complaint.

Do NOT proceed to Step 7 until the flight is verified or the user explicitly confirms the
details are correct despite any discrepancies. Once verified, proceed immediately to Step 7.

---

## Step 7 — Research the Airline's Policies

Once the airline is identified, research their specific policies and commitments. Quoting
the airline's own words back to them is what makes the letter powerful.

Read [references/research-strategy.md](references/research-strategy.md) for the complete
fetching strategy (Playwright check, fallback tiers, and all 8 research items with search
queries). Key points:

1. **Check for Playwright MCP first** — look for `mcp__playwright__` tools. If not
   available, show the user this exact install command:
   ```
   claude mcp add playwright -- npx @playwright/mcp@latest
   ```
2. **Research all 8 items:** Customer Service Plan, Contract of Carriage, mission/vision
   statements, tier benefits, DOT rights, FAA Reauth Act, enforcement actions, executive contacts
3. **Research gate:** do not proceed to writing until you have usable findings from items
   1–6 (see letter-quality rule for verbatim quote requirement)

The 8 research items are independent of each other — issue their searches and fetches
concurrently within this step. This step still completes before Step 8 begins.

Once the research gate is satisfied, proceed immediately to Step 8.

---

## Step 8 — Construct the Letter

Build the letter using this structure. Every section has a strategic purpose.

**Important: use your Step 6 verification data.** Any flight data you confirmed via
FlightAware in Step 6 is verified fact — use it in the letter with explicit attribution
(e.g., "per FlightAware flight tracking records"). This is not fabrication; you already
confirmed it. If FlightAware provided timestamps, delay durations, or flight status,
these MUST appear in the incident narrative attributed to "publicly available flight
tracking records" or "FlightAware." This independently verified data is one of the
letter's strongest assets.

### Subject line
Concise; include flight number, date, and loyalty tier if applicable.
Example: "Diamond Medallion Member — Unacceptable Experience on DL1234, Feb 15, 2026"

### Opening — establish the relationship
Lead with loyalty — years of patronage, miles flown, tier status, emotional connection to
the brand. (See letter-quality rule for specific requirements.)

### Incident narrative
Chronological, factual, specific. Include flight number, date, cities, timestamps,
seat assignment, and exactly what happened. Use dispassionate language — facts speak for
themselves. Note crew/agent responses factually.

Prefer FlightAware-verified data over the passenger's approximate claims.
(See letter-quality rule for specific requirements.)

### Impact statement
Concrete consequences: financial losses, missed events, hours wasted, family stress.
Quantify where possible. "The 11-hour delay caused me to miss my daughter's college
graduation — an event that cannot be rescheduled."

### The airline's own words vs. reality
Quote the airline's mission statement, vision, Customer Service Plan, or Contract of
Carriage — then contrast with actual experience.

> "Your Customer Service Plan states: '[exact quote].' My experience was the opposite:
> [what actually happened]."

> "[Airline CEO]'s letter to customers promises '[aspirational quote].' On Flight 1234,
> that promise was broken when [specific failure]."

### Regulatory basis
Cite specific regulations violated or that entitle the passenger to compensation —
DOT rules, FAA Reauthorization Act provisions, or enforcement precedent. Be precise;
cite the specific rule, not vague references to "federal regulations."

### Requested remedy
Specific, calibrated, reasonable but firm. Read [references/compensation.md](references/compensation.md)
for severity tiers and ranges. Always request a response within 14–21 business days.

### Closing
Express that you value the relationship and want to continue it, but make clear that the
response will influence future loyalty. State — factually, not as a threat — that you are
aware of your right to file a DOT complaint if the matter is not resolved satisfactorily.

### Tone throughout
Professional, measured, confident, and informed — never angry, sarcastic, or pleading.
Concise but thorough.

### Form-mode variant

Long form is the default and stays unchanged for email and paper mail. When Step 3 recorded
a **web form**, build a second, shorter variant instead:

- Drop what the form captures in its own fields (Step 3 recorded which). The form shows the
  agent those values already; repeating them spends the character budget twice.
- Keep every mandatory element the letter-quality rule marks as surviving compression — the
  loyalty tier in the opening sentence survives even when the loyalty number is dropped.
- Write plain prose. Markdown bold, headings, bullets, blockquotes, and links may render as
  literal punctuation in a plain-text field.

For an email or paper-mail letter there is nothing to measure — skip to Step 10. Otherwise
proceed immediately to Step 9.

---

## Step 9 — Verify the Letter Fits the Form

Form mode only. Never present a form-mode letter on your own character count. Write the
draft to a file and measure it:

```bash
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/letter-fit.py --airline <code> --file <draft-path>
# add --limit <N> when Step 3 got a limit for an airline the metadata doesn't record
```

Exit 0 means it fits, 1 means it overflows, 2 means the invocation or metadata is wrong. The
script emits a JSON report; read the verdict from it rather than recomputing anything.

Presenting requires **both** exit 0 and an empty `formatting_warnings` array.

- **Exit 1** — trim and rerun. Do not show the user an overflowing draft.
- **Exit 0, `formatting_warnings` non-empty** — strip the flagged markup and rerun. Do not
  present the draft on this pass.
- **Exit 0, `formatting_warnings` empty** — present the letter. Quote `effective_count`,
  `char_limit`, and `status` from the report; never substitute a count of your own. Where
  `count_verified` is `false`, tell the user the count is unverified — the form's own counter
  is the final word, and a draft measuring close to the limit may still be rejected by it.
- **Exit 2** — fix what the stderr message names, then rerun. Never fall back to counting
  by hand.

Two different numbers can come back from a live form. Keep them apart:

- The **limit** is the form's maximum. `--limit <N>` takes this and nothing else.
- The **counter reading** is what the form measured *this draft* at. It is calibration
  evidence for the counting method, never a limit. Passing it to `--limit` would raise the
  ceiling by exactly the amount the draft overran and turn a real overflow into a pass.

Neither belongs in the installed plugin's metadata — `tessl install` overwrites it and the
observation is lost. Route them instead:

- **This session** — the user reports the form's maximum: rerun with `--limit <max>`.
- **This machine, durably** — copy `.tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/airline-form-metadata.json`
  somewhere the user owns, record the verified `char_limit` there, and pass
  `--metadata <their-copy>` on later runs.
- **Everyone** — tell the user both numbers are worth upstreaming to
  `jbaruch/frequent-flyer-advocate`: the airline code, the channel, the form's stated
  maximum, the count the script reported, and the count the form reported. That last pair
  is what identifies the counting method and retires the inflation margin.

Proceed immediately to Step 10.

---

## Step 10 — Provide Escalation Guidance

After presenting the letter, provide actionable next steps:

**Where to send:**
- Check the airline's recorded channels first:
  `python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/letter-fit.py --airline <code> --info`
- Its `metadata.channel_notes` field reports known-dead or deprioritized channels. Where a
  channel is recorded as unreliable, route around it rather than sending the letter into it
  — AA's executive customer-relations email is the recorded case: web form first, paper mail
  to the executive office for escalation.
- Primary (no note to the contrary): executive customer relations email found during research
- Secondary: standard customer care (backup/paper trail)
- Include any airline-specific submission forms
- If the user already contacted general customer service, see escalation-output rule.
- If Step 7 research turns up a channel change the metadata doesn't record, state it in the
  escalation guidance and tell the user it is worth upstreaming to
  `jbaruch/frequent-flyer-advocate` as a `channel_notes` update. Do not edit the installed
  plugin's copy — `tessl install` overwrites it.

**When to file a DOT complaint (airconsumer.dot.gov):**
- **File IMMEDIATELY, in parallel with the complaint letter** for: denied boarding (this is
  a federal rights violation — always recommend immediate DOT filing), tarmac delays >3hrs
  domestic / >4hrs international, refund not processed within legal timeline, disability
  accommodation failures. Do not suggest waiting — these cases warrant same-day DOT filing.
- After 30 days with no response for other issues
- DOT complaints create a federal record; airlines take them seriously

**Social media (optional):**
- A concise, factual post tagging the airline can accelerate response
- Same professional tone as the letter
- Best platforms: Twitter/X for most airlines; some respond well on Facebook

**Timeline expectations:**
- Executive-level complaints: response within 7–14 business days
- No response in 30 days → escalate to DOT
- Inadequate initial response → reply once reiterating the request before escalating

See escalation-output rule for what to include in output documents. Proceed immediately to
Step 11.

---

## Step 11 — File the Complaint to the Bank

After the letter is finalized, always file it:
`python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/complaints-bank.py file --airline <code> --flight <flight> --flight-date <YYYY-MM-DD> --route <ORIG-DEST> --passenger <name> --category <CAT> --severity <SEV> --summary "<1-2 sentences>" --outcome "<what was requested>"`

If the user returns with a compensation outcome, log it in both systems:
`python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py add --type VOUCHER --description "..." --value <amount> --passenger "..." --airline <code> --expiry <date> --restrictions "..."`
`python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/complaints-bank.py resolve --id <id> --resolution <RESOLVED|PARTIAL|DENIED> --note "<what they got>"`

Finish here.
