# Complaint History Pattern Detection

## Problem/Feature Description

Lisa Park (American Airlines AAdvantage Executive Platinum, 6 years, 1.8M lifetime miles) needs help with a new complaint. Her flight AA1847 from Dallas (DFW) to Miami (MIA) on March 5, 2026 was cancelled 90 minutes before departure due to "operational reasons." She was rebooked on a flight 7 hours later and missed a critical client dinner.

The complaint bank already contains two prior complaints for Lisa with American Airlines:

**Prior complaint #1 (filed 4 months ago):** AA flight AA923 DFW-MIA on November 12, 2025. Category: CANCELLATION, Severity: MAJOR. Requested full refund + 50,000 miles. Resolution: DENIED (offered $100 voucher, unacceptable).

**Prior complaint #2 (filed 6 weeks ago):** AA flight AA1502 DFW-ORD on January 28, 2026. Category: DELAY, Severity: MODERATE. 3-hour delay, missed meeting. Requested 25,000 miles. Resolution: PARTIAL (received 10,000 miles).

Assume the complaint bank `check` command returns this history. The credits inventory shows no active credits for Lisa.

## Output Specification

Produce a file named `letter.md` — the complaint letter to American Airlines on Lisa's behalf. The letter should appropriately leverage (or not leverage) the complaint history based on what genuinely strengthens the case.
