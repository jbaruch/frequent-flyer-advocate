# Pending Complaint Follow-Up

## Problem/Feature Description

A returning user wants to file a new complaint. However, the complaint bank already contains two pending complaints from previous sessions:

**Pending complaint #1:** Delta flight DL891 BNA-ATL on January 8, 2026. Category: DELAY, Severity: MODERATE. Baruch Sadogursky (Diamond Medallion). Requested 25,000 miles + meal reimbursement.

**Pending complaint #2:** Delta flight DL2044 JFK-CDG on February 19, 2026. Category: DOWNGRADE, Severity: MAJOR. Baruch Sadogursky (Diamond Medallion). Requested refund of fare difference + 50,000 miles.

The user's responses when asked about pending complaints:
- For #1 (DL891 delay): "Yeah, they gave me 15,000 miles and a $50 voucher expiring December 2026. Not great but I took it."
- For #2 (DL2044 downgrade): "Nothing. Complete silence. I'm done waiting on that one."

After resolving the pending items, the user wants help with a new complaint: Spirit Airlines flight NK412 from Fort Lauderdale to Chicago on March 10, 2026, delayed 4 hours due to crew scheduling, causing a missed connection.

Assume the complaint bank has been pre-populated with the two pending complaints. Simulate running `complaints-bank.py pending` and getting the results above.

## Output Specification

Produce a file named `session-log.md` that documents:
1. How you handled each pending complaint (what resolution was recorded and why)
2. Whether any credits/vouchers from the resolutions should be logged to the travel credits inventory, and the exact commands
3. The intake for the new Spirit complaint (questions you would ask)
