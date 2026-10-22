import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, Chip, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../../components/clickable";
import { ATTEMPT_BUDGET, TIERS, attemptProof, attemptsOf, sessionState } from "../../_mock/improverAttempts";
import { codingAgentById, heldBackHiddenFrom, openPr, useIntegrations } from "../../_mock/integrations";
import { prOfImprovement } from "../../_mock/improvementPr";
import { definitionOf } from "../../_mock/agentRegistry";
import { candidateRouteOf } from "../../_mock/candidateRoute";
import { creditsForUsd, creditsLabel } from "../../_mock/metering";
import { improvementTotalSpend } from "../../_mock/optimizationRuns";
import { scenarioTitle } from "../../_mock/scenarios";

const MONO = "ui-monospace, Menlo, monospace";
const OUTCOME = { fixed: "Fixed", broke: "Broke", same: "Unchanged", "still-failing": "Still failing", flaky: "Flaky", "not-run": "Not run" };

/**
 * The customer's coding agent, working on this improvement: each submission
 * as a trial labelled with its improver, judged the same way as ours. The
 * agent proposes; only Future AGI marks a candidate proven.
 *
 * Shows the attempts so far, what each cost against the improvement's
 * budget, where it ran, the verdict the agent was sent — per-scenario
 * results for the visible scenarios, pass or fail only for must-pass — and,
 * once one passes, the proof it carries into its PR.
 */
export default function ImproverAttempts({ record, env, envState, session, name, searchSpent }) {
  const [now, setNow] = useState(Date.now());
  const [open, setOpen] = useState(null);
  const budgetLeft = (record.budget || Infinity) - (searchSpent || 0);
  /* Where its candidates can run follows how the agent came in. */
  const tier = candidateRouteOf(envState?.agent).tier;
  const attempts = useMemo(() => attemptsOf(record, session, { now, surface: env?.surface, budgetLeft, tier }), [record, session, now, env, budgetLeft, tier]);
  const status = sessionState(session, attempts);
  const { prs } = useIntegrations();

  /* Submissions arrive while the agent works — tick until it's done. */
  useEffect(() => {
    if (status?.key !== "waiting") return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [status?.key]);

  /* A passing verdict: the agent opens its PR with our proof — when the
     agent's code lives in a repository it can push to. */
  const accepted = status?.key === "accepted" ? status.attempt : null;
  const def = definitionOf(envState?.agent);
  const repo = (def.kind === "repo" || def.kind === "framework") && def.repoUrl ? (def.repoUrl || "").replace(/^https?:\/\/github\.com\//, "") : null;
  const pr = prOfImprovement((prs || []).filter((p) => p.by === accepted?.improver), record.id, env?.id);
  useEffect(() => {
    if (!accepted || !repo || pr) return;
    openPr({
      repo,
      title: `Fix: ${accepted.title}`,
      files: [{ path: "agent", title: accepted.title }],
      handoffKey: record.id,
      /* Improvement ids repeat across environments — the PR names its own. */
      envId: env?.id, envName: env?.name,
      by: accepted.improver,
      body: attemptProof(record, accepted, name),
      /* The verify check re-runs everything on the PR's commit — visible,
         held-back and must-pass, not just what the agent could see. */
      check: {
        scenarios: accepted.visible.length + accepted.mustPass.total + (record.result?.heldMeasured || 0),
        /* Today's agent and the accepted attempt, on the visible scenarios. */
        before: accepted.before,
        after: accepted.score,
      },
    });
  }, [accepted, repo, pr, record, name, env?.id, env?.name]);

  if (!session) return null;
  /* Everything the improvement spent — reproducing, the search and these
     attempts — the same figure the Improvements list shows. */
  const total = improvementTotalSpend(record, env?.surface, { attempts });
  const improver = codingAgentById(session.agentId)?.label || "Your coding agent";
  /* What it submits and how we test it — the same for any agent, worded
     for where this one lives. */
  const route = candidateRouteOf(envState?.agent);

  return (
    <Box>
      {/* ── where the session stands, and what it has spent ── */}
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}>
        <Iconify
          icon={status.key === "waiting" ? "eos-icons:three-dots-loading" : status.key === "accepted" ? "solar:check-circle-linear" : "solar:pause-circle-linear"}
          width={16} sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {status.key === "accepted"
              ? `${accepted.improver} · attempt ${accepted.n} accepted`
              : status.key === "budget"
                ? status.money
                  ? `Stopped at the ${creditsLabel(record.budget)} budget after ${attempts.length} attempts — none was accepted`
                  : `${improver} used all ${ATTEMPT_BUDGET} attempts — none was accepted`
                : `${improver} is working on this`}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {status.key === "waiting"
              ? attempts.length
                ? "Waiting for its next submission — it gets a verdict for every one."
                : `Waiting for its first submission. It submits ${route.submits}; ${route.tested.charAt(0).toLowerCase()}${route.tested.slice(1)}`
              : status.key === "accepted"
                ? pr ? `It opened PR #${pr.number} on ${pr.repo} carrying this proof.` : repo ? "It's opening its PR with this proof." : "This agent isn't in a repository it can open a PR on — ship the change below; each verdict carries its proof."
                : "Ship this improvement's own winner, or hand it over again with a fresh brief."}
          </Typography>
        </Box>
        <Tooltip
          arrow
          title={attempts.length
            ? `Scored by Future AGI and counted against the improvement's budget. ${attempts.map((a) => `Attempt ${a.n}: ${a.gate ? "rejected before running — not run, not charged" : `${TIERS[a.where]?.label || a.where} · ${creditsLabel(a.spend)}`}`).join(" · ")}.`
            : "Every attempt is scored by Future AGI and counts against the improvement's budget — each says where it ran."}
        >
          <Typography sx={{ typography: "s3", color: "text.secondary", fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>
            {`${attempts.length} of ${ATTEMPT_BUDGET} attempts · ${creditsForUsd(total).toLocaleString()} of ${creditsLabel(record.budget || 0)}`}
          </Typography>
        </Tooltip>
      </Stack>

      {/* ── each submission, judged like our own trials ── */}
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />} sx={{ borderBottom: attempts.length ? "1px solid" : "none", borderColor: "divider" }}>
        {attempts.map((a) => {
          const isOpen = open === a.n;
          /* Laid out like our own trials: score, change, what it tried, the
             held-back figure we never sent, the verdict and who proposed it. */
          const scored = typeof a.score === "number";
          return (
            <Box key={a.id}>
              <Stack
                direction="row" alignItems="center" spacing={1.5}
                {...clickable(() => setOpen(isOpen ? null : a.n), { expanded: isOpen })}
                sx={{ px: 2, py: 1.25, cursor: "pointer", "&:hover": { bgcolor: "action.hover" }, ...focusRing }}
              >
                <Typography sx={{ typography: "s3", color: "text.disabled", width: 26, flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>{`A${a.n}`}</Typography>
                <Typography sx={{ typography: "s2", fontWeight: 700, width: 44, flexShrink: 0, fontVariantNumeric: "tabular-nums", color: scored ? "text.primary" : "text.disabled" }}>{scored ? `${a.score}%` : "—"}</Typography>
                <Typography sx={{ typography: "s3", width: 40, flexShrink: 0, fontVariantNumeric: "tabular-nums", color: !scored ? "text.disabled" : a.delta > 0 ? "#16A34A" : a.delta < 0 ? "#DC2626" : "text.disabled" }}>
                  {!scored ? "not run" : a.delta > 0 ? `+${a.delta}` : a.delta}
                </Typography>
                <Typography noWrap sx={{ typography: "s2", color: "text.subtitle", flex: 1, minWidth: 0 }}>{a.title}</Typography>
                {scored && (
                  <Tooltip arrow title="Scored here on the held-back set — never sent to the agent">
                    <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>{`${a.held}% held back`}</Typography>
                  </Tooltip>
                )}
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, maxWidth: 200 }}>{a.label}</Typography>
                <Chip
                  size="small" label={a.accepted ? "accepted" : a.gate ? "rejected before running" : "rejected"}
                  sx={{
                    height: 18, borderRadius: 0.5, flexShrink: 0, bgcolor: "transparent", border: "1px solid",
                    color: a.accepted ? "#16A34A" : a.gate ? "#DC2626" : "text.secondary",
                    borderColor: a.accepted ? alpha("#16A34A", 0.4) : a.gate ? alpha("#DC2626", 0.4) : "divider",
                    "& .MuiChip-label": { px: 0.625, typography: "s3", fontWeight: 700 },
                  }}
                />
                <Iconify icon={isOpen ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"} width={14} sx={{ color: "text.disabled", flexShrink: 0 }} />
              </Stack>
              {isOpen && <AttemptVerdict attempt={a} previous={attempts.slice(0, attempts.indexOf(a)).reverse().find((x) => !x.gate)} record={record} name={name} session={session} />}
            </Box>
          );
        })}
      </Stack>
    </Box>
  );
}

ImproverAttempts.propTypes = {
  record: PropTypes.object, env: PropTypes.object, envState: PropTypes.object, session: PropTypes.object,
  name: PropTypes.string, searchSpent: PropTypes.number,
};

/* What the coding agent was sent back — and, when it passed, the proof. */
function AttemptVerdict({ attempt: a, previous, record, name, session }) {
  const [copied, setCopied] = useState(false);
  const counts = ["fixed", "broke", "same", "still-failing", "flaky"].map((k) => [k, a.visible.filter((x) => x.outcome === k).length]).filter(([k, n]) => n || ["fixed", "broke"].includes(k));
  const copy = () => {
    try { navigator.clipboard?.writeText(attemptProof(record, a, name)); } catch { /* clipboard unavailable */ }
    setCopied(true);
  };
  return (
    <Stack spacing={1.5} sx={{ px: 2, pb: 2, pl: 4 }}>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {"Commit "}
        <Box component="span" sx={{ fontFamily: MONO, color: "text.secondary" }}>{a.commit}</Box>
        {a.fingerprint && <>{" + uncommitted changes "}<Box component="span" sx={{ fontFamily: MONO, color: "text.secondary" }}>{a.fingerprint}</Box></>}
        {a.gate ? " · rejected before running — nothing was scored or charged." : ` · ${TIERS[a.where]?.label}. ${TIERS[a.where]?.note}.`}
      </Typography>
      {!!a.files?.length && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: -1 }}>
          {"Diff touches "}
          <Box component="span" sx={{ fontFamily: MONO, color: "text.secondary" }}>{a.files.join(", ")}</Box>
        </Typography>
      )}

      <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
        <Typography sx={{ px: 1.5, py: 0.875, typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.5, borderBottom: "1px solid", borderColor: "divider" }}>
          {`Verdict sent to ${a.improver}`}
        </Typography>
        <Row label={a.accepted ? "Accepted" : "Rejected"} value={a.accepted ? "Proven — it may open its PR with this proof" : a.why} strong />
        {/* Whether its changes are moving the pass rate — against today's
            agent and against its own last attempt — so it knows whether to
            keep going this way. */}
        {!a.gate && (
          <Row
            label="Passing"
            value={[
              `${a.score}% of visible scenarios`,
              `${a.delta > 0 ? "up" : a.delta < 0 ? "down" : "no change"}${a.delta ? ` ${Math.abs(a.delta)} pts` : ""} on today's agent`,
              previous && typeof previous.score === "number"
                ? a.score > previous.score ? `better than attempt ${previous.n}` : a.score < previous.score ? `worse than attempt ${previous.n}` : `same as attempt ${previous.n}`
                : null,
            ].filter(Boolean).join(" · ")}
            tone={a.delta < 0 ? "#DC2626" : undefined}
          />
        )}
        <Row label={`Visible scenarios · ${a.visible.length}`} value={a.gate ? "not run" : counts.map(([k, n]) => `${OUTCOME[k].toLowerCase()} ${n}`).join(" · ")} />
        {/* Each visible scenario's outcome — the agent may see these. */}
        {!a.gate && a.visible.length > 0 && (
          <Box sx={{ maxHeight: 180, overflowY: "auto", borderTop: "1px solid", borderBottom: "1px solid", borderColor: "divider" }}>
            {a.visible.map((x) => (
              <Stack key={x.id} direction="row" spacing={1.25} sx={{ px: 1.5, pl: 4, py: 0.375 }}>
                <Typography noWrap sx={{ typography: "s3", color: "text.secondary", flex: 1, minWidth: 0 }}>{scenarioTitle(x.title)}</Typography>
                <Typography sx={{ typography: "s3", width: 90, flexShrink: 0, fontWeight: 600, color: x.outcome === "fixed" ? "#16A34A" : x.outcome === "broke" ? "#DC2626" : "text.subtitle" }}>
                  {OUTCOME[x.outcome] || x.outcome}
                </Typography>
              </Stack>
            ))}
          </Box>
        )}
        {/* Must-pass shows pass or fail only — which one failed isn't sent,
            so the agent can't tune to it. */}
        <Row
          label={`Must-pass · ${a.mustPass.total}`}
          value={a.mustPass.notRun ? "not run" : a.mustPass.failed ? "fail — which one isn't shown to the agent" : "pass"}
          tone={a.mustPass.failed ? "#DC2626" : undefined}
        />
        {/* What the agent couldn't see, said here so the verdict reads honestly. */}
        <Row
          label="Held-back set"
          value={heldBackHiddenFrom(session?.agentId)
            ? `scored here, never sent — hidden from ${a.improver} by its scoped token`
            : `scored here, never sent — but ${a.improver}'s workspace key could read it`}
        />
        {a.checks.map((c) => (
          <Row
            key={c.id} label={c.label} value={c.actual || (c.pass ? "kept" : "broken")}
            tone={c.pass === false ? "#DC2626" : undefined}
            icon={c.pass === null ? "solar:question-circle-linear" : c.pass ? "solar:check-circle-linear" : "solar:close-circle-bold"}
          />
        ))}
      </Box>

      {a.accepted && (
        <Box>
          <Stack direction="row" alignItems="center" sx={{ mb: 0.75 }}>
            <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1 }}>Proof for the PR</Typography>
            <Button
              size="small" variant="outlined" onClick={copy}
              startIcon={<Iconify icon={copied ? "solar:check-circle-bold" : "solar:copy-linear"} width={14} />}
              sx={{ typography: "s3", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
            >
              {copied ? "Copied" : "Copy proof"}
            </Button>
          </Stack>
          <Box component="pre" sx={{ m: 0, p: 1.5, borderRadius: 1, bgcolor: "background.neutral", typography: "s3", fontFamily: MONO, whiteSpace: "pre-wrap", color: "text.secondary", maxHeight: 220, overflowY: "auto" }}>
            {attemptProof(record, a, name)}
          </Box>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}>
            A Future AGI check on the PR re-verifies this exact commit before merge.
          </Typography>
        </Box>
      )}
    </Stack>
  );
}
AttemptVerdict.propTypes = { attempt: PropTypes.object, previous: PropTypes.object, record: PropTypes.object, name: PropTypes.string, session: PropTypes.object };

function Row({ label, value, tone, icon, strong }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.5, py: 0.75, "& + &": { borderTop: "1px solid", borderColor: "divider" } }}>
      {icon && <Iconify icon={icon} width={14} sx={{ color: tone || "text.secondary", flexShrink: 0 }} />}
      <Typography sx={{ typography: "s2", fontWeight: strong ? 700 : 400, width: 230, flexShrink: 0 }}>{label}</Typography>
      <Typography sx={{ typography: "s2", color: tone || "text.secondary", flex: 1, minWidth: 0 }}>{value}</Typography>
    </Stack>
  );
}
Row.propTypes = { label: PropTypes.string, value: PropTypes.string, tone: PropTypes.string, icon: PropTypes.string, strong: PropTypes.bool };
