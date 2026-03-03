# Research Strategy & Fetching

## Browser capability check (do this FIRST)

Airline websites are often hostile to automated fetching (Cloudflare, bot detection, JS
rendering walls). Before starting research, check whether Playwright MCP is available by
looking for tools prefixed with `mcp__playwright__` in your available tools.

### If Playwright IS available

Use it as the PRIMARY fetcher for all airline website pages. It renders JavaScript, handles
Cloudflare challenges, and sees pages exactly as a human would. Use WebSearch in parallel
to find URLs, then fetch them with Playwright. For government sites (transportation.gov),
WebFetch is fine.

### If Playwright is NOT available

Tell the user before starting research. You MUST include the exact install command in your
message — always output it as a copyable code block:

"I'm about to research [airline]'s policies, but airline websites often block automated
access. A real browser would give much stronger results with direct policy quotes.

**Quick setup (one command):**
```
claude mcp add playwright -- npx @playwright/mcp@latest
```
Restart this Claude Code session and re-run `/frequent-flyer-advocate` — or I can proceed
now using web search and public sources."

Always output that exact `claude mcp add` command. Do not paraphrase or omit it.
If the user wants to proceed without Playwright, use the fallback tiers below.

## Fetching tiers (without Playwright)

**Tier 1 — WebSearch:** Airline policies are SEO-optimized. Run targeted searches with
quoted phrases to surface specific policy language.
Example: `"delta air lines" "customer service plan" "we will notify"`

**Tier 2 — WebFetch on search result URLs:** Government sites and airline investor/media
pages tend to work. Airline consumer pages are hit-or-miss.

**Tier 3 — Alternative sources:**
- Google cached versions or Wayback Machine archives
- PDF versions of policies (often fetch better than HTML)
- News articles or aviation blogs quoting policy verbatim
- DOT copies of airline Customer Service Plans at transportation.gov

**Tier 4 — Ask the user (last resort):** If a critical source can't be accessed after
Tiers 1-3, ask the user to open the URL and paste the relevant content. Only do this for
sources that would meaningfully strengthen the letter.

## Required research items

Complete ALL before writing. Do not proceed until you have usable quotes from at least
items 1–6.

**1. Customer Commitment / Customer Service Plan**
Every US airline must publish a DOT-required Customer Service Plan with binding commitments
on delays, cancellations, baggage, and service standards.
Search: `"[airline]" "customer service plan"` or `site:transportation.gov "[airline]" customer service plan`

**2. Contract of Carriage**
The legal passenger-airline contract. Find sections relevant to the complaint type.
Search: `"[airline]" "contract of carriage" [relevant topic]`

**3. Mission / Vision / Values / CEO Letters**
Aspirational language is ideal for "you promised X, I experienced Y" framing. Check press
releases and investor presentations — they contain the most quotable language.
Search: `"[airline]" "our mission"` / `"[airline]" CEO letter customers` / `"[airline]" "we are committed" customers`

**4. Frequent flyer tier benefits**
If the user has status, find the specific benefits for their tier — especially any they
were entitled to but didn't receive.
Search: `"[airline]" "[tier name]" benefits` or `"[airline]" "[program name]" elite benefits`

**5. DOT passenger rights**
Fetch current DOT guidance for the relevant complaint type.
Search: `site:transportation.gov air passenger rights [complaint type]`
Key page: https://www.transportation.gov/airconsumer

**6. FAA Reauthorization Act of 2024 provisions**
Key protections: automatic cash refunds for cancelled/significantly delayed flights;
refunds for unprovided paid services (Wi-Fi, seat selection, baggage); 7-day refund
timeline (credit card) / 20 days (other payment); fee transparency requirements.
Search for provisions relevant to the specific complaint.

**7. Recent DOT enforcement actions against this airline**
Recent fines or consent orders demonstrate a pattern and add implicit pressure.
Search: `DOT enforcement action "[airline]"` or `site:transportation.gov consent order "[airline]"`

**8. Executive contacts**
Many airlines have executive customer relations channels that bypass front-line service.
Aviation advocacy sites (e.g., Elliott Advocacy) often maintain current lists.
Search: `"[airline]" executive customer relations email` or `"[airline]" CEO email complaints`
