---
alwaysApply: false
applyTo: "skills/frequent-flyer-advocate/** — when writing, drafting, constructing, or compressing a complaint letter to an airline"
description: >
  Letter quality constraints for complaint letters, including the web-form variant and its
  character-count verification.
---

# Letter Quality Requirements

## Every Letter

- **Opening sentence = loyalty.** The very first sentence states loyalty credentials (years, miles, tier status) before any mention of the incident.
- **FlightAware timestamp required.** The incident narrative includes at least one FlightAware-attributed timestamp (e.g., "per FlightAware records, Flight XX departed at 14:47, 3h47m behind schedule").
- **Verbatim airline quote required.** The letter contains at least one exact verbatim quote from a named airline policy document (Customer Service Plan, Contract of Carriage, mission statement, or CEO communication), contrasted with what actually happened.
- **Response deadline.** The requested remedy section asks for a response within 14–21 business days.

## Web Form Message Field

- Give the user only copy-ready plain text for the message field. Leave the subject,
  salutation, courtesy closing, and signature out of that text.
- Omit identifiers already supplied through prefilled fields, dedicated fields, or
  the linked reservation. These can include name, confirmation number, loyalty
  number, flight number, date, and route. Use the field list established during intake.
- Include missing details when they identify the affected flight or explain what
  happened. A prefilled flight number can go, while a gate change central to the
  complaint still belongs in the narrative.
- A standalone email or paper letter includes a subject, salutation, signed closing,
  and enough identifying information for the airline to locate the reservation.

## Form Mode — Survives Compression

- Loyalty **tier** in the opening sentence (e.g. "As an AAdvantage Platinum member…"), whether or not the loyalty number is dropped
- FlightAware-attributed timestamps
- The verbatim airline quote
- The requested remedy
- The 14–21 business day response deadline

## Character Count Is Script-Verified

- A form-mode letter is measured by `skills/frequent-flyer-advocate/scripts/letter-fit.py`
- An inline count, an eyeballed count, and a `len()` computed in conversation are all forbidden as the basis for presenting a letter
- With a known character limit, present a form-mode letter only after the script reports it fits
- If the character limit is unavailable, label the compact draft's fit unverified
- Show the user the script's output in place of a count of your own
- Report the count as unverified where the script does
- Strip every formatting construct the script flags, then rerun it
