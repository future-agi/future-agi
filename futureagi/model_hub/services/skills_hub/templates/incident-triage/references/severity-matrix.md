# Sample severity matrix

Apply the team's approved severity policy when available. These sample levels are
for triage, not contractual response-time commitments.

| Level | Evidence of impact | Initial response |
| --- | --- | --- |
| Critical | Confirmed broad outage, ongoing data loss, or suspected active compromise | Contact the designated incident or security owner using the approved channel; prioritize containment and preservation of evidence. |
| High | A core workflow is unavailable for a meaningful subset of users and no safe workaround is known | Identify the affected scope, test a reversible mitigation, and agree on an update cadence. |
| Moderate | Degraded performance or a limited failure with a verified workaround | Record the affected paths and workaround, monitor scope, and schedule investigation with the owner. |
| Low | No verified user impact; isolated internal warning or cosmetic issue | Validate the signal and open a follow-up with reproducible evidence. |

## Reassessment

Raise or lower severity as verified scope changes. Record the old level, new level,
reason, and time rather than rewriting the history. A low-volume incident can still
be critical when it causes irreversible data loss or compromises access controls.

## Evidence table

For each event capture time with time zone, source, observation, and confidence.
Mark estimates as estimates. Preserve trace and deployment identifiers, not raw
personal information. Link to existing authorized evidence instead of copying
large sensitive logs into a shared report.

## Recovery check

Choose a representative affected workflow and a healthy control. Define the error
rate, latency, or invariant to observe and a suitable observation window with the
owner. Confirm both the original failure and unwanted mitigation side effects.
Do not declare recovery from a single successful retry.
