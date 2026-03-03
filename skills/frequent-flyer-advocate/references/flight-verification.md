# Flight Verification via FlightAware

Verify flight details before writing the complaint. This prevents erroneous complaints
and provides independently verified evidence for the letter.

## Step 1: Look up the flight

Search FlightAware for the flight using the flight number and date.
Search: `site:flightaware.com [airline ICAO or IATA code][flight number]`
Or fetch directly: `https://www.flightaware.com/live/flight/[designator]/history/[YYYYMMDD]`

Flight designator format: IATA code + number, no spaces (e.g., DL1234, UA456, AA789).

If Playwright MCP is available, use it — FlightAware renders data via JavaScript. If not,
use WebSearch + WebFetch. FlightAware pages are generally fetchable.

## Step 2: Extract and cross-check

From FlightAware, extract:
- **Route** (origin → destination) — does it match the user's claim?
- **Scheduled vs actual departure/arrival times** — confirms delay duration precisely
- **Flight status** — cancelled, diverted, delayed, on time
- **Delay duration** — exact minutes, independently verified
- **Aircraft type** — useful for amenity complaints (some aircraft don't have certain features)
- **Gate/terminal changes** — relevant for gate-related complaints

Cross-check against the user's account:
- Does the route match?
- Does the delay duration match what the user claimed?
- Was the flight actually cancelled if that's the claim?
- Are the dates correct?

## Step 3: Handle discrepancies

**Details don't match:**
Tell the user specifically what doesn't align. Examples:
- "You mentioned a 5-hour delay, but FlightAware shows DL1234 on Feb 15 arrived only
  47 minutes late. Could you double-check the flight number or date?"
- "FlightAware shows UA789 on March 1 flew SFO→ORD, but you mentioned flying to LAX.
  Was it a different flight number?"

Do not assume the user is wrong — FlightAware data can have gaps. But flag it clearly.

**Flight number reuse (same number, multiple routes on one day):**
Some airlines reuse flight numbers for different legs on the same day (e.g., DL123 might
fly ATL→JFK in the morning and JFK→LAX in the afternoon). If FlightAware shows multiple
segments:
- Ask the user to confirm the departure city and approximate departure time
- Or ask for the route (origin → destination)

**Flight not found on FlightAware:**
- Verify the flight number format (IATA vs ICAO code)
- Try alternate date (user might be off by one day, especially for overnight/redeye flights)
- Try the codeshare partner's flight number
- If still not found, ask the user to confirm — proceed with their stated details but note
  in the letter that verification was attempted

**Details are missing from the user:**
If the user hasn't provided a flight number or date, you MUST ask — verification cannot be
skipped. Flight number and date are also essential for the complaint letter itself.

## Step 4: Use verified data in the letter

FlightAware data strengthens the complaint by providing independent evidence:
- "According to FlightAware records, Flight DL1234 on February 15, 2026 departed
  3 hours and 47 minutes late" — this is much stronger than "my flight was delayed
  about 4 hours"
- Exact timestamps make the complaint precise and harder to dismiss
- If FlightAware shows additional issues the user didn't mention (diversion, equipment
  change, prior delay on the same aircraft), consider mentioning them — they may
  strengthen the case

Always attribute: "per FlightAware flight tracking records" or "according to publicly
available flight tracking data."
