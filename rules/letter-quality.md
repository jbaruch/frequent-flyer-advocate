---
alwaysApply: false
description: >
  Letter quality constraints for complaint letters, including the web-form variant and its
  character-count verification. Use when writing, drafting, constructing, or compressing a
  complaint letter to an airline.
---

# Letter Quality Requirements

## Every Letter

- **Opening sentence = loyalty.** The very first sentence states loyalty credentials (years, miles, tier status) before any mention of the incident.
- **FlightAware timestamp required.** The incident narrative includes at least one FlightAware-attributed timestamp (e.g., "per FlightAware records, Flight XX departed at 14:47, 3h47m behind schedule").
- **Verbatim airline quote required.** The letter contains at least one exact verbatim quote from a named airline policy document (Customer Service Plan, Contract of Carriage, mission statement, or CEO communication), contrasted with what actually happened.
- **Response deadline.** The requested remedy section asks for a response within 14–21 business days.

## Form Mode — Droppable From the Body

- Applies to the web-form variant alone; the email and paper variants keep every element
- Droppable: passenger name, loyalty number, flight number, flight date, route, gate assignments
- Droppable only where the form's own fields capture the value
- Confirm the form's field list during intake; never infer it from the airline
- Dropping an element the form does not capture is a defect, not compression

## Form Mode — Survives Compression

- Loyalty **tier** in the opening sentence (e.g. "As an AAdvantage Platinum member…"), whether or not the loyalty number is dropped
- FlightAware-attributed timestamps
- The verbatim airline quote
- The 14–21 business day response deadline
- A letter that drops one of these four to fit the limit is under-compressed elsewhere

## Character Count Is Script-Verified

- A form-mode letter is measured by `skills/frequent-flyer-advocate/scripts/letter-fit.py`
- An inline count, an eyeballed count, and a `len()` computed in conversation are all forbidden as the basis for presenting a letter
- Present a form-mode letter only after the script reports it fits
- Show the user the script's output in place of a count of your own
- Report the count as unverified where the script does
- Strip every formatting construct the script flags, then rerun it
