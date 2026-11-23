import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Link,
  MenuItem,
  Select,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { creditsLabel } from "../_mock/metering";
import { runPlanHref } from "../_mock/runPlanLink";
import { defaultRunCap, estimateRunPlan } from "../_mock/pricing";
import { FIX_DEFAULTS, repeatsFor } from "../_mock/improvementStart";
import { resolveEval } from "../_mock/evals";
import { protoRunId } from "../_mock/executionAdapter";
import { useConnectedAgents, useSimStore } from "../store";
import { LiveConsentCard } from "../agents/AgentLiveConsent";
import { proofKindOf } from "../_mock/rsiModel";
import CostPartsTable from "../improvements/CostPartsTable";
import { SHOW_RUN_COST } from "../_mock/costFlags";
import { ConstraintPicker } from "../improvements/ImprovementConstraints";
import BuildEnvironmentDialog from "../agents/BuildEnvironmentDialog";
import { useRebuildForAgent } from "../workspace/useRebuildForAgent";
import { latestEnvVersion, toolFit } from "../_mock/toolFit";
import { currentAgentVersion } from "../_mock/versions";
import { buildPresetFor } from "../agents/agentViewUtils";
import {
  envFitsIssue,
  epochBudgetLine,
  issueFixNav,
  issueMechanismOf,
  issueRunFor,
  scenarioGroupsOf,
} from "./issueFix";
import TraceReviewDrawer from "../workspace/scenarios/TraceReviewDrawer";
import { fixImportIdOf, issueImportOf } from "../_mock/errorFeedSource";
import ClosableDialog from "../components/ClosableDialog";

/*
  Run Epoch, confirmed in place: what it tests, where, and what it may
  spend. One main action at a time — Review the drafts, then Reproduce, then
  run the epoch. Run scenarios only (a link in Scenarios) runs the issue's
  scenarios as a normal run instead, and Edit plan opens the whole plan.
*/
function Row({ k, children }) {
  return (
    <Stack
      direction="row"
      spacing={1.5}
      sx={{ py: 1, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: 110,
          flexShrink: 0,
        }}
      >
        {k}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
    </Stack>
  );
}
Row.propTypes = { k: PropTypes.string, children: PropTypes.node };

/* Run Epoch again: what to change before this one, by how the last one
   ended — the same epoch on the same rules and scenarios would likely end
   the same way. */
const LAST_NEXT = {
  "no-fix":
    "Every change fixed too little or broke something. Loosen a rule, edit the scenarios, or hand it to your coding agent.",
  "not-reproduced":
    "The issue didn't show up on the agent — it may be gone. Add scenarios from newer traces, or keep watching.",
  "over-budget":
    "It stopped at its budget before trying a change. Pick a bigger budget below.",
  reopened:
    "It was released and the issue came back. This epoch starts from the agent as it runs now.",
  stopped:
    "It was stopped before it finished, so nothing it tried was checked. This one starts over.",
};

export default function FixStartDialog({
  open,
  onClose,
  issue,
  plan,
  onEnv,
  onAdjust,
}) {
  const navigate = useNavigate();
  const { commit } = useSimStore();
  const { setAgentValues } = useConnectedAgents();
  const { agent, main, mainState, drafts, limits, estimate, budgets } = plan;
  const envs = plan.envs || [];
  /* The agent's main environment is the first of its list. */
  const mainId = envs[0]?.id;
  const others = envs.filter((e) => e.id !== main?.id);
  const [buildOpen, setBuildOpen] = useState(false);
  /* The issue's traces go through the same import review as Observe traces
     and uploads (evidence.md) before any fix is tried: its drafts wait in
     the main environment as one import, kept per issue, so closing the box
     loses nothing. The box steps aside while the review is open. */
  const [reviewing, setReviewing] = useState(false);
  const reviewId = fixImportIdOf(issue?.cluster_id || "issue");
  const reviewRows = (mainState?.scenarios || []).filter(
    (r) => r.traceImport?.id === reviewId,
  );
  /* Kept for the fix in the review (or already in the environment). */
  const keptOf = (r) => !r.proposed || !!r.traceReview?.kept;
  const reviewStats = {
    kept: reviewRows.filter(keptOf).length,
    waiting: reviewRows.filter((r) => !keptOf(r)).length,
  };
  const reviewed = reviewRows.length > 0 && reviewRows.every(keptOf);
  /* Drafts the user removed in the review stay removed — they're not drafted again. */
  const removedBefore = (mainState?.traceImports || []).some(
    (x) => x.importId === reviewId,
  );
  const startReview = () => {
    if (!reviewRows.length && !removedBefore && main) {
      const at = new Date().toISOString();
      patchMain({
        scenarios: [
          ...(mainState?.scenarios || []),
          ...issueImportOf(
            drafts.map((draft) => ({
              issue: {
                id: issue?.cluster_id,
                project: issue?.project_name || null,
              },
              draft,
            })),
            { id: reviewId, at },
          ).map((r) => ({
            ...r,
            proposed: r.proposed || { at, source: "error-feed" },
          })),
        ],
      });
    }
    setReviewing(true);
  };
  /* What runs: the drafts as the review left them (merged, settled,
     rejected ones out) — the review decides the tests, not the first draft. */
  const runDrafts = reviewed
    ? reviewRows.filter(keptOf).map(({ proposed: _p, ...r }) => r)
    : drafts;
  /* Reviewed drafts join the environment when the run starts (step 4,
     "save and validate"), so the run reproduces what was reviewed. */
  const joinReviewed = () =>
    reviewRows.length &&
    patchMain({
      scenarios: (mainState?.scenarios || []).map((r) =>
        r.traceImport?.id === reviewId && r.proposed && keptOf(r)
          ? { ...r, proposed: undefined }
          : r,
      ),
    });
  /* Tools the agent calls that this environment can't answer: taught here,
     in seconds — the same rebuild as the World tab's, never a trip there. */
  const patchMain = (p) =>
    main && commit({ type: "patchEnvState", envId: main.id, patch: p });
  const rebuild = useRebuildForAgent(main, mainState, patchMain);
  const toolGap = plan.toolGap || [];
  /* Serious only when the issue's own failure goes through a missing tool —
     then the fix couldn't prove anything. Otherwise it only thins the check
     that nothing breaks. */
  const mech = issueMechanismOf(issue?.cluster_id);
  const issueNeeds = mech?.match
    ? toolGap.filter((t) => mech.match.test(t))
    : [];
  const teach = () => {
    if (!main) return;
    const latest = latestEnvVersion(main, mainState);
    const latestFits =
      latest &&
      latest.label !== toolFit(main, mainState).envVersion &&
      toolFit(main, mainState, { envVersion: latest.label }).fits;
    if (latestFits) patchMain({ activeEnvVersion: latest.label });
    else rebuild(currentAgentVersion(mainState)?.label);
  };
  const teachLabel = `Teach it ${toolGap.length === 1 ? "this tool" : `these ${toolGap.length} tools`}`;
  const [budget, setBudget] = useState(null);
  const [showAll, setShowAll] = useState(false);
  /* One click starts one fix — the button turns off once pressed. */
  const [starting, setStarting] = useState(false);
  /* Run scenarios only, priced the way the run plan prices any run. */
  const runRepeats = main ? repeatsFor(main, mainState) : 2;
  const runEstimate = useMemo(
    () =>
      main
        ? estimateRunPlan({
            env: main,
            envState: mainState,
            /* Priced on the reviewed tests once the review is done. */
            scenarios: runDrafts.length,
            attempts: runRepeats,
            evals: (mainState?.evals || []).map(resolveEval).filter(Boolean),
            runLocation: mainState?.agent?.values?.runLocation || null,
          })
        : null,
    [main, mainState, runDrafts.length, runRepeats],
  );
  const runPrice =
    runEstimate && !runEstimate.platform.unknown && !runEstimate.blocked.length
      ? runEstimate.platform.high
      : null;
  useEffect(() => {
    if (open) {
      setBudget(null);
      setShowAll(false);
      setStarting(false);
    }
  }, [open]);
  /* Many scenarios read as the situations they share. */
  const traceTotal =
    issue?.trace_count ||
    plan.allDrafts.reduce((n, d) => n + (d.traceCount || 0), 0);
  const groups = scenarioGroupsOf(drafts);
  const grouped = drafts.length > 6 && groups.length < drafts.length;
  const listed = grouped
    ? groups.map((g) => ({
        id: g.id,
        label: g.label,
        meta: `${g.scenarios} ${g.scenarios === 1 ? "scenario" : "scenarios"} · ${g.traces} traces`,
      }))
    : drafts.map((d) => ({
        id: d.id,
        label: d.title,
        meta: d.traceCount ? `${d.traceCount} traces` : null,
      }));
  const spend =
    budget ??
    (budgets.includes(FIX_DEFAULTS.dollars)
      ? FIX_DEFAULTS.dollars
      : budgets[budgets.length - 1]);
  /* A live agent's consent, complete — given by Start fix itself. */
  const [consent, setConsent] = useState(null);
  const waiting = plan.consentNeeded && !consent;
  const liveConsent =
    agent && proofKindOf(agent).kind === "live" && !plan.consentNeeded
      ? agent.values?.liveTestConsent || null
      : null;
  const issueId = issue?.cluster_id;
  /* The last fix, when this is Fix again: how it ended, what it tried, when. */
  const lastStatus =
    plan.status && LAST_NEXT[plan.status.key] ? plan.status : null;
  const lastRecord = lastStatus
    ? (plan.state?.byEnv?.[lastStatus.envId]?.optimizations || []).find(
        (o) => o.id === lastStatus.optId,
      )
    : null;
  const lastTrials = (lastRecord?.result || lastRecord?.pendingResult)?.trials
    ?.length;

  /* Why it can't start from here — the full plan handles each one. */
  const held = !main
    ? `${agent?.name || "This agent"} has no environment yet — build one first.`
    : plan.building
      ? `${main.name} is still building — the epoch can start once it's done.`
      : issueNeeds.length
        ? `${main.name} can't answer ${issueNeeds.join(", ")} yet — teach it first.`
        : !drafts.length
          ? "No scenarios could be written from this issue's traces."
          : null;

  /* Nothing to decide: an environment that fits the issue's channel, every
     tool answered, consent already given, nothing holding it. The summary
     says what will happen; the rest is one click away. */
  const compact =
    !!main &&
    !!estimate &&
    !held &&
    !plan.consentNeeded &&
    !toolGap.length &&
    envFitsIssue(main, issue);

  const start = () => {
    if (starting) return;
    setStarting(true);
    joinReviewed();
    if (plan.consentNeeded && consent) setAgentValues(agent.key, consent);
    const at = plan.startFix(spend, reviewed ? runDrafts : null);
    onClose();
    /* Lands on the fix's first run, playing live; its banner opens the fix. */
    if (at?.liveRunId)
      navigate(paths.dashboard.simulate.simulationRun(at.envId, at.liveRunId));
    else if (at) navigate(...issueFixNav(at, issueId));
  };
  const reproduceOnly = () => {
    joinReviewed();
    if (plan.consentNeeded && consent) setAgentValues(agent.key, consent);
    onClose();
    const only = runDrafts.map((d) => d.id);
    /* Priced here, so it starts here — straight to the live run, with the
       price as its limit. The issue's scenarios ride on the run; the
       environment gets them only if someone adds them from it. Without a
       price (no rate set), the run plan says why it's held. */
    if (runPrice == null) {
      navigate(runPlanHref(main.id, { only }));
      return;
    }
    const id = protoRunId(
      main.id,
      `issue-${issueId}-${Date.now().toString(36)}`.toLowerCase(),
    );
    const run = issueRunFor(main, mainState, issue, runDrafts, {
      id,
      /* The run plan's own default limit: the estimate plus room for calls already running. */
      cap: defaultRunCap(runEstimate),
    });
    if (!run) return;
    commit({ type: "recordRun", envId: main.id, run });
    navigate(
      `${paths.dashboard.simulate.simulationRun(main.id, id)}?${new URLSearchParams({ only: only.join(",") })}`,
    );
  };

  /* The drafts are reviewed before any change is tried (B31) — until then
     the one main action is the review; after it, the run. */
  const needsReview = !!main && drafts.length > 0 && !reviewed;
  const reviewLabel =
    reviewRows.length > 0 ? "Continue review" : "Review the drafts";
  const scenariosLine = reviewed
    ? `${runDrafts.length} reviewed ${runDrafts.length === 1 ? "scenario" : "scenarios"} from ${traceTotal} failing traces · reproduced first, some held back to prove the change`
    : reviewRows.length > 0
      ? `${reviewStats.kept} of ${reviewRows.length} ${reviewRows.length === 1 ? "draft" : "drafts"} kept so far · review each before any change is tried`
      : `${drafts.length} ${drafts.length === 1 ? "draft" : "drafts"} from ${traceTotal} failing traces${plan.capped ? `, the ${drafts.length} most common of ${plan.allDrafts.length}` : ""} · review each before any change is tried`;
  /* The budget, said the same way the Fix page and the setup sheet say it. */
  const budgetLine = epochBudgetLine({
    candidates: estimate?.runs,
    credits: creditsLabel(spend),
    minutes: plan.minutes,
  });
  const inlineLink = {
    typography: "s3",
    fontWeight: 600,
    verticalAlign: "baseline",
  };
  const cantRunLine = main && (plan.cantRun || []).length > 0 && (
    <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}>
      {`${plan.cantRun.length} more can't run on ${main.name} yet, so ${plan.cantRun.length === 1 ? "it's" : "they're"} left out: ${String(
        plan.cantRun[0].why || "",
      )
        .replace(/\.$/, "")
        .replace(/^./, (c) => c.toLowerCase())}.`}
    </Typography>
  );
  /* A side path, not a peer of the run: the issue's scenarios as a normal run. */
  const reproduceLink = plan.ready && !waiting && (
    <Tooltip
      arrow
      title={`Runs the issue's ${runDrafts.length} ${runDrafts.length === 1 ? "scenario" : "scenarios"} ${runRepeats} times each; no change is tried.`}
    >
      <Link
        component="button"
        type="button"
        underline="hover"
        onClick={reproduceOnly}
        sx={{ ...inlineLink, display: "block", mt: 0.5 }}
      >
        {`Run scenarios only${runPrice != null ? ` · ≈ ${Math.round(runPrice).toLocaleString()} credits` : ""}`}
      </Link>
    </Tooltip>
  );
  const scenariosRow = (
    <Row k="Scenarios">
      <Typography sx={{ typography: "s2" }}>
        {scenariosLine}
        {reviewed && (
          <>
            <Box component="span" sx={{ color: "text.disabled" }}>
              {" · "}
            </Box>
            <Link
              component="button"
              type="button"
              underline="hover"
              onClick={startReview}
              sx={{ typography: "s2", verticalAlign: "baseline" }}
            >
              Open review
            </Link>
          </>
        )}
      </Typography>
      {cantRunLine}
      {/* Names, compactly: scenarios when there are few, the situations
          they share when there are many — three, then the rest in a short
          scrolling list, so the buttons never move off screen. */}
      {!compact && listed.length > 0 && (
        <Box
          component="ul"
          sx={{
            m: 0,
            mt: 0.5,
            p: 0,
            listStyle: "none",
            ...(showAll && { maxHeight: 168, overflowY: "auto" }),
          }}
        >
          {(showAll ? listed : listed.slice(0, 3)).map((x) => (
            <Stack
              key={x.id}
              component="li"
              direction="row"
              spacing={1}
              sx={{ minWidth: 0, lineHeight: "20px" }}
            >
              <Typography
                noWrap
                title={x.label}
                sx={{
                  typography: "s3",
                  color: "text.secondary",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {x.label}
              </Typography>
              {x.meta && (
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flexShrink: 0,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {x.meta}
                </Typography>
              )}
            </Stack>
          ))}
        </Box>
      )}
      {!compact && listed.length > 3 && (
        <Link
          component="button"
          type="button"
          underline="hover"
          onClick={() => setShowAll((v) => !v)}
          sx={{ ...inlineLink, color: "text.secondary" }}
        >
          {showAll
            ? "Show fewer"
            : `Show all ${listed.length} ${grouped ? "situations" : "scenarios"}`}
        </Link>
      )}
      {!compact && plan.capped && (
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
          {`Pick others in Edit plan: ${plan.allDrafts.length - drafts.length} more aren't in this epoch.`}
        </Typography>
      )}
      {reproduceLink}
    </Row>
  );
  /* The full plan: one link, never a second button beside the main one. */
  const primaryOpensPlan =
    !!held &&
    !(!main && agent) &&
    !(main && !plan.building && issueNeeds.length);

  return (
    <>
      <TraceReviewDrawer
        open={open && reviewing}
        onClose={() => setReviewing(false)}
        env={main}
        envState={mainState}
        patch={patchMain}
        importId={reviewId}
        onApprove={(rows) => {
          const ids = new Set(rows.map((r) => r.id));
          patchMain({
            scenarios: (mainState?.scenarios || []).map((r) =>
              ids.has(r.id)
                ? {
                    ...r,
                    traceReview: { ...(r.traceReview || {}), kept: true },
                  }
                : r,
            ),
          });
          setReviewing(false);
        }}
        approveLabel={(n) => (n ? `Use ${n} in the epoch` : "Use in the epoch")}
      />
      <ClosableDialog
        open={open && !reviewing}
        onClose={onClose}
        maxWidth="sm"
        fullWidth
      >
        <Box sx={{ px: 2.5, pt: 2.5, pb: 1 }}>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            {`Run Epoch · ${issueId}`}
          </Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            {issue?.title || issue?.error_name || ""}
          </Typography>
        </Box>
        <Box sx={{ px: 2.5 }}>
          {lastStatus && (
            <Row k="Last epoch">
              <Typography sx={{ typography: "s2" }}>
                {[
                  `${lastStatus.label}${lastStatus.detail ? ` · ${lastStatus.detail}` : ""}`,
                  lastTrials &&
                    `${lastTrials} ${lastTrials === 1 ? "candidate" : "candidates"} tried`,
                  lastRecord?.createdAt &&
                    new Date(lastRecord.createdAt).toLocaleDateString("en-GB", {
                      day: "numeric",
                      month: "short",
                    }),
                ]
                  .filter(Boolean)
                  .join(" · ")}
                <Box component="span" sx={{ color: "text.disabled" }}>
                  {" · "}
                </Box>
                <Link
                  component="button"
                  type="button"
                  underline="hover"
                  onClick={() => {
                    onClose();
                    navigate(...issueFixNav(lastStatus, issueId));
                  }}
                  sx={{ typography: "s2", verticalAlign: "baseline" }}
                >
                  View last epoch
                </Link>
              </Typography>
              <Typography
                sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
              >
                {LAST_NEXT[lastStatus.key]}
              </Typography>
            </Row>
          )}
          <Row k="Agent">
            <Typography sx={{ typography: "s2" }}>{agent?.name}</Typography>
          </Row>
          {compact ? (
            <>
              <Row k="Runs in">
                <Typography sx={{ typography: "s2" }}>{main.name}</Typography>
              </Row>
              {scenariosRow}
              <Row k="Budget">
                <Typography sx={{ typography: "s2" }}>{budgetLine}</Typography>
                {liveConsent && (
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
                  >
                    {`Live tests: consent given ${new Date(liveConsent.at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}`}
                  </Typography>
                )}
              </Row>
            </>
          ) : (
            <>
              <Row k="Runs in">
                {envs.length > 1 ? (
                  /* Several: pick one for this epoch — the agent's main stays as it is. */
                  <Select
                    size="small"
                    value={main?.id || ""}
                    onChange={(e) => onEnv?.(e.target.value)}
                    renderValue={(id) =>
                      envs.find((e) => e.id === id)?.name || ""
                    }
                    sx={{
                      height: 28,
                      minWidth: 260,
                      typography: "s2",
                      "& .MuiSelect-select": { typography: "s2", py: 0.5 },
                    }}
                  >
                    {envs.map((e) => (
                      <MenuItem
                        key={e.id}
                        value={e.id}
                        sx={{ typography: "s2", gap: 2 }}
                      >
                        <Box component="span" sx={{ flex: 1 }}>
                          {e.name}
                        </Box>
                        <Box
                          component="span"
                          sx={{ typography: "s3", color: "text.tertiary" }}
                        >
                          {[
                            e.id === mainId && "Main",
                            !envFitsIssue(e, issue) &&
                              (issue?.modality === "voice"
                                ? "No voice"
                                : "Voice only"),
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </Box>
                      </MenuItem>
                    ))}
                  </Select>
                ) : (
                  <Typography sx={{ typography: "s2" }}>
                    {main ? main.name : "No environment yet"}
                    {/* Its only one: another is built from the agent, never
                        borrowed from another agent's environment. */}
                    {main && envs.length === 1 && agent && (
                      <>
                        <Box component="span" sx={{ color: "text.disabled" }}>
                          {" · "}
                        </Box>
                        <Link
                          component="button"
                          type="button"
                          underline="hover"
                          onClick={() => setBuildOpen(true)}
                          sx={{ typography: "s2", verticalAlign: "baseline" }}
                        >
                          Build another environment
                        </Link>
                      </>
                    )}
                  </Typography>
                )}
                {!main && agent && (
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
                  >
                    {`An epoch tests its changes in an environment. Build one from ${agent.name} first.`}
                  </Typography>
                )}
                {main && !envFitsIssue(main, issue) && (
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
                  >
                    {`This issue happened on ${issue?.modality === "voice" ? "voice calls" : "chat"}, which ${main.name} doesn't test.`}
                  </Typography>
                )}
                {main && others.length > 0 && (
                  <Typography
                    sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
                  >
                    {`Also used in ${others.length} more: ${others
                      .slice(0, 2)
                      .map((e) => e.name)
                      .join(
                        ", ",
                      )}${others.length > 2 ? "…" : ""}. Edit plan to check what must keep passing there too.`}
                  </Typography>
                )}
                {/* Said before anything is spent, with what it actually affects,
                    and fixed right here. */}
                {main && toolGap.length > 0 && (
                  <Stack direction="row" spacing={0.75} sx={{ mt: 0.5 }}>
                    <Iconify
                      icon={
                        issueNeeds.length
                          ? "solar:danger-triangle-linear"
                          : "solar:info-circle-linear"
                      }
                      width={14}
                      sx={{
                        color: issueNeeds.length
                          ? "status.warning.fg"
                          : "text.tertiary",
                        flexShrink: 0,
                        mt: "2px",
                      }}
                    />
                    <Typography
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      {issueNeeds.length
                        ? `The issue goes through ${issueNeeds.join(", ")}, which ${main.name} can't answer yet. `
                        : `${toolGap.length} of the agent's tools aren't in ${main.name} (${toolGap.slice(0, 2).join(", ")}${toolGap.length > 2 ? "…" : ""}). The issue doesn't use them, but scenarios that do won't count in the check that nothing breaks. `}
                      {/* The main action teaches it when the issue needs it. */}
                      {!issueNeeds.length && (
                        <Link
                          component="button"
                          type="button"
                          underline="hover"
                          onClick={teach}
                          sx={inlineLink}
                        >
                          {`${teachLabel} · a few seconds`}
                        </Link>
                      )}
                    </Typography>
                  </Stack>
                )}
              </Row>
              {scenariosRow}
              {/* A live agent that already consented: said, so nothing looks missing. */}
              {liveConsent && (
                <Row k="Live tests">
                  <Typography sx={{ typography: "s2" }}>
                    {`Consent given ${new Date(liveConsent.at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}`}
                  </Typography>
                </Row>
              )}
              {main && estimate && (
                <Row k="Budget">
                  <Box sx={{ maxWidth: 240 }}>
                    <ConstraintPicker
                      agent={agent}
                      limits={limits}
                      field="dollars"
                      value={spend}
                      onChange={setBudget}
                      fullWidth
                    />
                  </Box>
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary", mt: 0.75 }}
                  >
                    {budgetLine}
                  </Typography>
                  {/* A cost breakdown before the epoch starts is Later (costFlags). */}
                  {SHOW_RUN_COST && (
                    <Box sx={{ mt: 1 }}>
                      <CostPartsTable
                        parts={estimate.parts}
                        totalLabel="Up to"
                        totalUsd={estimate.likely}
                      />
                    </Box>
                  )}
                </Row>
              )}
              {/* A live agent: consent is given here, then the run — never a page away. */}
              {!held && plan.consentNeeded && (
                <Box
                  sx={{
                    py: 1.25,
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <LiveConsentCard
                    agent={agent}
                    env={main}
                    onSave={(values) => setAgentValues(agent.key, values)}
                    onReady={setConsent}
                    bare
                  />
                </Box>
              )}
              {/* No environment is said under Runs in, with its button. */}
              {held && main && !issueNeeds.length && (
                <Stack direction="row" spacing={0.75} sx={{ py: 1.25 }}>
                  <Iconify
                    icon="solar:info-circle-linear"
                    width={14}
                    sx={{ color: "text.secondary", mt: "2px", flexShrink: 0 }}
                  />
                  <Typography sx={{ typography: "s3", color: "text.primary" }}>
                    {held}
                  </Typography>
                </Stack>
              )}
            </>
          )}
          {/* Why the run is off, said where the person is looking — never a
              silently disabled button. */}
          {!held && !needsReview && waiting && (
            <Typography
              sx={{ typography: "s3", color: "text.primary", pt: 1.25 }}
            >
              Choose how its tools are handled to start: this agent runs live.
            </Typography>
          )}
        </Box>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2.5,
            py: 2,
            mt: 1,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            {!primaryOpensPlan && (
              <Tooltip
                arrow
                title="Change the diagnosis, scenarios, budget or rules before starting."
              >
                <Link
                  component="button"
                  type="button"
                  underline="hover"
                  onClick={() => {
                    onClose();
                    onAdjust();
                  }}
                  sx={{ typography: "s2", fontWeight: 600 }}
                >
                  Edit plan
                </Link>
              </Tooltip>
            )}
          </Box>
          <Button
            size="small"
            onClick={onClose}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          {/* One main action at a time: what's holding it, else the review,
              else the run. */}
          {held ? (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => {
                /* No environment: build one, the same way the agent page does. */
                if (!main && agent) {
                  setBuildOpen(true);
                  return;
                }
                /* Held only because the issue's tool is missing: teach it here. */
                if (main && !plan.building && issueNeeds.length) {
                  teach();
                  return;
                }
                onClose();
                onAdjust();
              }}
            >
              {!main && agent
                ? "Build environment"
                : main && !plan.building && issueNeeds.length
                  ? teachLabel
                  : "Open the full plan"}
            </Button>
          ) : needsReview ? (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={startReview}
            >
              {reviewLabel}
            </Button>
          ) : (
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={waiting || starting}
              onClick={start}
              startIcon={<Iconify icon="solar:play-bold" width={14} />}
            >
              {main && drafts.length > 0
                ? "Reproduce, then run the epoch"
                : "Run Epoch"}
            </Button>
          )}
        </Stack>
        {agent && (
          <BuildEnvironmentDialog
            open={buildOpen}
            onClose={() => setBuildOpen(false)}
            agent={agent}
            defaultVersion={agent.production || agent.latest}
            onBuild={(choice) => {
              setBuildOpen(false);
              onClose();
              /* The environment remembers the issue and brings the person back. */
              navigate(paths.dashboard.simulate.environmentBuild, {
                state: {
                  presetSource: buildPresetFor(agent, choice),
                  fromIssue: issue?.cluster_id || null,
                },
              });
            }}
          />
        )}
      </ClosableDialog>
    </>
  );
}

FixStartDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  issue: PropTypes.object,
  plan: PropTypes.object.isRequired,
  onAdjust: PropTypes.func.isRequired,
  /* (envId) — the environment picked for this epoch. */
  onEnv: PropTypes.func,
};
