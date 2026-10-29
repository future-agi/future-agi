import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Dialog,
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
import { ConstraintPicker } from "../improvements/ImprovementConstraints";
import BuildEnvironmentDialog from "../agents/BuildEnvironmentDialog";
import { useRebuildForAgent } from "../workspace/useRebuildForAgent";
import { latestEnvVersion, toolFit } from "../_mock/toolFit";
import { currentAgentVersion } from "../_mock/versions";
import { buildPresetFor } from "../agents/agentViewUtils";
import {
  envFitsIssue,
  issueFixNav,
  issueMechanismOf,
  issueRunFor,
  scenarioGroupsOf,
} from "./issueFix";

/*
  Fix, confirmed in place: what it tests, where, and what it may spend —
  then Start fix lands on the fix running. Run scenarios only runs the
  issue's scenarios as a normal run instead (via the run plan), and Edit plan opens the
  whole plan to edit.
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

/* Fix again: what to change before this one, by how the last one ended —
   the same fix on the same rules and scenarios would likely end the same way. */
const LAST_NEXT = {
  "no-fix":
    "Every change it tried fixed too little or broke something. Loosen one of the agent's rules or edit the scenarios first — or hand it to your coding agent.",
  "not-reproduced":
    "The issue didn't show up on the agent as it was — it may already be gone. Add scenarios from newer traces first, or keep watching it.",
  "over-budget":
    "It stopped at its budget before trying a change. Pick a bigger budget below.",
  reopened:
    "It was released and the issue came back. This fix starts from the agent as it runs now.",
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
  /* Folded to a summary when nothing needs a decision; Show details opens it. */
  const [details, setDetails] = useState(false);
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
            scenarios: drafts.length,
            attempts: runRepeats,
            evals: (mainState?.evals || []).map(resolveEval).filter(Boolean),
            runLocation: mainState?.agent?.values?.runLocation || null,
          })
        : null,
    [main, mainState, drafts.length, runRepeats],
  );
  const runPrice =
    runEstimate && !runEstimate.platform.unknown && !runEstimate.blocked.length
      ? runEstimate.platform.high
      : null;
  useEffect(() => {
    if (open) {
      setBudget(null);
      setShowAll(false);
      setDetails(false);
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
      ? `${main.name} is still building — the fix can start once it's done.`
      : issueNeeds.length
        ? `${main.name} can't answer ${issueNeeds.join(", ")} yet — teach it first.`
        : !drafts.length
          ? "No scenarios could be written from this issue's traces."
          : null;

  /* Nothing to decide: an environment that fits the issue's channel, every
     tool answered, consent already given, nothing holding it. The summary
     says what will happen; the rest is one click away. */
  const compact =
    !details &&
    !!main &&
    !!estimate &&
    !held &&
    !plan.consentNeeded &&
    !toolGap.length &&
    envFitsIssue(main, issue);

  const start = () => {
    if (starting) return;
    setStarting(true);
    if (plan.consentNeeded && consent) setAgentValues(agent.key, consent);
    const at = plan.startFix(spend);
    onClose();
    /* Lands on the fix's first run, playing live; its banner opens the fix. */
    if (at?.liveRunId)
      navigate(paths.dashboard.simulate.simulationRun(at.envId, at.liveRunId));
    else if (at) navigate(...issueFixNav(at, issueId));
  };
  const reproduceOnly = () => {
    if (plan.consentNeeded && consent) setAgentValues(agent.key, consent);
    onClose();
    const only = drafts.map((d) => d.id);
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
    const run = issueRunFor(main, mainState, issue, drafts, {
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

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <Box sx={{ px: 2.5, pt: 2.5, pb: 1 }}>
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          {`Fix ${issueId}`}
        </Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
          {issue?.title || issue?.error_name || ""}
        </Typography>
      </Box>
      <Box sx={{ px: 2.5 }}>
        {lastStatus && (
          <Row k="Last fix">
            <Typography sx={{ typography: "s2" }}>
              {[
                `${lastStatus.label}${lastStatus.detail ? ` · ${lastStatus.detail}` : ""}`,
                lastTrials &&
                  `${lastTrials} ${lastTrials === 1 ? "trial" : "trials"} tried`,
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
                View last fix
              </Link>
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
            >
              {LAST_NEXT[lastStatus.key]}
            </Typography>
          </Row>
        )}
        {compact ? (
          <>
            <Row k="Agent">
              <Typography sx={{ typography: "s2" }}>{agent?.name}</Typography>
            </Row>
            <Row k="Runs in">
              <Typography sx={{ typography: "s2" }}>{main.name}</Typography>
            </Row>
            <Row k="Scenarios">
              <Typography sx={{ typography: "s2" }}>
                {`${drafts.length} ${drafts.length === 1 ? "scenario" : "scenarios"} from ${traceTotal} failing traces${plan.capped ? ` — the ${drafts.length} most common of ${plan.allDrafts.length}` : ""} · reproduced first, some held back to prove the fix`}
              </Typography>
            </Row>
            <Row k="Budget">
              <Typography sx={{ typography: "s2" }}>
                {`Up to ${estimate.runs} trials · ${creditsLabel(estimate.likely)} · ${estimate.minutes} min, within ${creditsLabel(spend)}`}
              </Typography>
              {liveConsent && (
                <Typography
                  sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
                >
                  {`Live tests: consent given ${new Date(liveConsent.at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}`}
                </Typography>
              )}
            </Row>
            <Link
              component="button"
              type="button"
              underline="hover"
              onClick={() => setDetails(true)}
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.secondary",
                mt: 0.5,
              }}
            >
              Show details
            </Link>
          </>
        ) : (
          <>
            <Row k="Agent">
              <Typography sx={{ typography: "s2" }}>{agent?.name}</Typography>
            </Row>
            <Row k="Runs in">
              {envs.length > 1 ? (
                /* Several: pick one for this fix — the agent's main stays as it is. */
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
                  {`A fix tests its changes in an environment. Build one from ${agent.name} — a few minutes — and you come back here to start.`}
                </Typography>
              )}
              {main && !envFitsIssue(main, issue) && (
                <Typography
                  sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
                >
                  {`This issue happened on ${issue?.modality === "voice" ? "voice calls" : "chat"}, and ${main.name} doesn't test that channel, so it may not show up there.`}
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
                      ? `The issue goes through ${issueNeeds.join(", ")}, which ${main.name} can't answer yet — so the fix couldn't prove anything. `
                      : `${toolGap.length} of the agent's tools aren't in ${main.name} (${toolGap.slice(0, 2).join(", ")}${toolGap.length > 2 ? "…" : ""}). The issue doesn't use them, but scenarios that do won't count in the check that nothing breaks. `}
                    <Link
                      component="button"
                      type="button"
                      underline="hover"
                      onClick={teach}
                      sx={{
                        typography: "s3",
                        fontWeight: 600,
                        verticalAlign: "baseline",
                      }}
                    >
                      {`${teachLabel} · a few seconds`}
                    </Link>
                  </Typography>
                </Stack>
              )}
            </Row>
            <Row k="Scenarios">
              <Typography sx={{ typography: "s2" }}>
                {`${drafts.length} ${drafts.length === 1 ? "scenario" : "scenarios"} from ${traceTotal} failing traces${plan.capped ? ` — the ${drafts.length} most common of ${plan.allDrafts.length}` : ""} · reproduced first, some held back to prove the fix`}
              </Typography>
              {/* The run skips what can't stage here, so the fix leaves it out too. */}
              {main && (plan.cantRun || []).length > 0 && (
                <Typography
                  sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
                >
                  {`${plan.cantRun.length} more ${plan.cantRun.length === 1 ? "can't" : "can't"} run on ${main.name} yet, so ${plan.cantRun.length === 1 ? "it's" : "they're"} left out: ${String(
                    plan.cantRun[0].why || "",
                  )
                    .replace(/\.$/, "")
                    .toLowerCase()}.`}
                </Typography>
              )}
              {/* Names, compactly: scenarios when there are few, the situations
              they share when there are many — three, then the rest in a
              short scrolling list, so the buttons never move off screen. */}
              {listed.length > 0 && (
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
              {listed.length > 3 && (
                <Link
                  component="button"
                  type="button"
                  underline="hover"
                  onClick={() => setShowAll((v) => !v)}
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.secondary",
                  }}
                >
                  {showAll
                    ? "Show fewer"
                    : `Show all ${listed.length} ${grouped ? "situations" : "scenarios"}`}
                </Link>
              )}
              {plan.capped && (
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
                >
                  {`Pick others in Edit plan — ${plan.allDrafts.length - drafts.length} more aren't in this fix.`}
                </Typography>
              )}
            </Row>
            {/* A live agent that already consented: said, so nothing looks missing. */}
            {liveConsent && (
              <Row k="Live tests">
                <Typography sx={{ typography: "s2" }}>
                  {`Consent given ${new Date(liveConsent.at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })} · ${liveConsent.replayFlag ? "tools stubbed on the replay flag" : "tools safe to call"}`}
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
                  {`Up to ${estimate.runs} trials · ${creditsLabel(estimate.likely)} · ${estimate.minutes} min. Stops at the limit with the best it found.`}
                </Typography>
              </Row>
            )}
            {main && estimate && (
              <Box
                sx={{
                  pt: 1,
                  pb: 0.5,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                <CostPartsTable
                  parts={estimate.parts}
                  totalLabel="Up to"
                  totalUsd={estimate.likely}
                />
              </Box>
            )}
            {/* A live agent: consent is given here, then Start fix — never a page away. */}
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
          {/* Why Start is off, said where the person is looking — never a
              silently disabled button. */}
          {plan.ready && waiting && !held ? (
            <Typography sx={{ typography: "s3", color: "text.primary" }}>
              Choose how its tools are handled to start — this agent runs live.
            </Typography>
          ) : (
            plan.ready && (
              <Tooltip
                arrow
                title={`Runs the issue's ${drafts.length} ${drafts.length === 1 ? "scenario" : "scenarios"} now, ${runRepeats} times each — no fix is tried. They join ${main?.name || "the environment"} only if you add them from the run.`}
              >
                <Button
                  size="small"
                  onClick={reproduceOnly}
                  sx={{ color: "text.secondary", ml: -1 }}
                >
                  {`Run scenarios only${runPrice != null ? ` · ≈ ${Math.round(runPrice).toLocaleString()} credits` : ""}`}
                </Button>
              </Tooltip>
            )
          )}
        </Box>
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Tooltip
          arrow
          title={
            held
              ? ""
              : "Change the diagnosis, scenarios, budget or rules before starting."
          }
        >
          <Button
            size="small"
            variant={held ? "contained" : "outlined"}
            color={held ? "primary" : "inherit"}
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
            sx={held ? undefined : { borderColor: "divider" }}
          >
            {!main && agent
              ? "Build environment"
              : main && !plan.building && issueNeeds.length
                ? teachLabel
                : held
                  ? "Open the full plan"
                  : "Edit plan"}
          </Button>
        </Tooltip>
        {!held && (
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={waiting || starting}
            onClick={start}
            startIcon={<Iconify icon="solar:play-bold" width={14} />}
          >
            Start fix
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
    </Dialog>
  );
}
FixStartDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  issue: PropTypes.object,
  plan: PropTypes.object.isRequired,
  onAdjust: PropTypes.func.isRequired,
  /* (envId) — the environment picked for this fix. */
  onEnv: PropTypes.func,
};
