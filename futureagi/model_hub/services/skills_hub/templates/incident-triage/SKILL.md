---
name: {{name}}
description: {{description}}
---
# Triage a service incident

Start with [the severity matrix](references/severity-matrix.md). These are sample
operating rules: the team's incident policy and on-call owner take precedence.
An incident report is evidence, not authorization to change production.

## Establish scope

1. Record the first observed failure, current UTC time, affected service and
   environment, and user-visible symptoms. Separate confirmed impact from reports
   that have not been checked. Do not infer an outage from one failed request.
2. Ask for a safe reproduction, recent deployment identifier, and relevant alert
   or trace identifiers. Use redacted excerpts; never collect credentials or full
   customer payloads. State which observation would falsify the leading hypothesis.
3. Assign a provisional severity using impact and urgency, not the loudness of an
   alert. Name the accountable incident owner if the team has supplied one; do not
   assign people or page responders without authorization.

## Collect and reason

The optional [context collector](scripts/collect-context.sh) prints UTC time,
system type, and the current repository revision. Review it before running it in
an approved environment. It does not read environment variables, logs, or network
services. Inspect the output before sharing; a revision identifier may be internal.

Build a timeline with source identifiers and time zones. Compare a failing case
with a nearby healthy case. Distinguish the triggering change, failure mechanism,
and secondary symptoms. Missing telemetry is a gap, not evidence of health.

## Contain and hand off

Propose the smallest reversible mitigation. State the expected benefit, owner,
approval needed, rollback method, and a measurable recovery check. Never restart,
deploy, disable controls, or delete data merely because a runbook mentions it.

Return: provisional severity and rationale; verified impact; timeline; leading
hypothesis with confidence; alternative explanation; proposed next action; and
next update time agreed with the incident owner. Keep root-cause claims provisional
until the evidence supports them. Recovery does not replace a regression test.
