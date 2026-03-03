# Flight Verification: Conflicting Details and Disambiguation

## Problem/Feature Description

A passenger has reached out for help with a complaint, but the details they've provided contain issues that should be caught before writing a letter.

**Passenger — Tom Russo:**
Tom is a Southwest Airlines A-List Preferred member. He says he was on "Southwest flight 2174 from Oakland to Los Angeles on February 20, 2026" and that his flight was delayed by 4 hours, causing him to miss his son's basketball championship game. He says the airline blamed "weather" but he checked and the weather was clear at both airports. He paid $247 for the ticket and wants compensation.

Important context for the evaluator: Southwest flight numbers are frequently reused across multiple routes on the same day. The agent must verify the actual flight on FlightAware and handle any ambiguity — confirming the correct route, departure time, and actual delay duration with independently verified data before writing the complaint.

## Output Specification

Produce the following files:

1. **`verification-report.md`** — Document what you found when verifying the flight on FlightAware. Include: the flight lookup process, what FlightAware shows for this flight number on this date, any discrepancies or ambiguities found, and how they were resolved. If the flight number serves multiple routes, note that.

2. **`letter.md`** — The complaint letter to Southwest Airlines on Tom's behalf, incorporating verified flight data. The letter should cite independently verified timestamps and delay information, not just Tom's stated account.
