# jbaruch/frequent-flyer-advocate

[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2Fjbaruch%2Ffrequent-flyer-advocate)](https://tessl.io/registry/jbaruch/frequent-flyer-advocate)

Write professional, persuasive complaint letters to US airlines on behalf of passengers — grounded in the airline's own published policies, federal DOT regulations, and the passenger's loyalty status, not generic grievances.

## Installation

```
tessl install jbaruch/frequent-flyer-advocate
```

## What's Included

### Skill

| Skill | Description |
|-------|-------------|
| [frequent-flyer-advocate](skills/frequent-flyer-advocate/SKILL.md) | Intake → flight verification → policy research → letter construction for airline service failures (delays, cancellations, baggage, downgrades, denied boarding). Fits the letter to the airline's submission channel and its character limit. Tracks compensation credits and prior complaints across a shared inventory. |

### Rules

| Rule | Summary |
|------|---------|
| [boundaries](rules/boundaries.md) | Never fabricate regulations, docket numbers, citations, or policy quotes — cite only verifiable sources. |
| [letter-quality](rules/letter-quality.md) | The mandatory requirements every complaint letter must satisfy, plus what the web-form variant may drop and what survives compression. |
| [escalation-output](rules/escalation-output.md) | Required contents for every escalation guide / next-steps document. |
| [complaint-patterns](rules/complaint-patterns.md) | How to use prior-complaint history (from the complaint bank) as escalation leverage. |

See [CHANGELOG.md](CHANGELOG.md) for version history.
