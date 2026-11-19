import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Link,
  Radio,
  Stack,
  Typography,
} from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";

import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import {
  useCodeAgentEnvironments,
  useCreateIssueEpoch,
  useIssueEpoch,
  useIssueEpochPlan,
  useRunIssueEpoch,
  useStopIssueEpoch,
} from "src/api/errorFeed/epochFromIssue";
import { fixErrorOf } from "src/api/simulate-environments/fixRuns";
import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import { sessionTracesLink } from "src/sections/projects/Insights/insightsLinks";
import SideDrawer from "src/sections/simulate/environments/components/SideDrawer";
import EvidenceCaseCard from "src/sections/simulate/environments/evidence/EvidenceCaseCard";
import LaunchLimits, {
  Row,
  SurfacesRow,
} from "src/sections/simulate/environments/workspace/improvements/LaunchLimits";
import { fixName } from "src/sections/simulate/environments/workspace/improvements/fixRunView";
import { costLimitOf } from "src/sections/simulate/environments/workspace/improvements/launchFormView";

import { PRE_LAUNCH, epochHref, epochKey, isEpochLive } from "./issueEpochView";

const AGENT_SOURCE = { github: "GitHub", archive: "Uploaded" };
const AGENT_TYPE = { voice: "Voice", chat: "Chat" };
// Why the plan picked a trace, in words.
const PICK_REASON = {
  example: "Typical of the issue",
  cited: "Cited in the root cause",
  recent: "Recent",
};
const UNIT_NOUN = { call: "call", session: "session", trace: "trace" };
// The review keeps drafted scenarios, and ones the environment already holds.
const KEEPABLE = new Set(["drafted", "duplicate"]);
// Without traces from a run yet, the epoch locks to the models its baseline calls.
const MODELS_NOTE =
  "Locked to the models the current version calls, read from its run before the epoch starts.";

const plural = (n, noun) => `${n} ${noun}${n === 1 ? "" : "s"}`;
// A refusal's body: its code, message and any fields it names, at the top level
// (the app's axios spreads it onto the error; fixErrorOf reads the same).
const refusalOf = (error) => error?.response?.data ?? error ?? {};
const scenariosHref = (envId) =>
  paths.dashboard.simulate.environments.workspaceTab(envId, "scenarios");

function pickHref(projectId, pick) {
  if (!projectId || !pick.source_id) return undefined;
  if (pick.unit === "session") {
    return sessionTracesLink(projectId, pick.source_id, pick.started_at);
  }
  return `/dashboard/observe/${projectId}/trace/${encodeURIComponent(pick.source_id)}`;
}

function Footer({ note, children }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        position: "sticky",
        bottom: 0,
        mt: "auto",
        px: 2,
        py: 1.5,
        borderTop: 1,
        borderColor: "divider",
        bgcolor: "background.paper",
        zIndex: 1,
      }}
    >
      <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
        {note}
      </Typography>
      {children}
    </Stack>
  );
}
Footer.propTypes = { note: PropTypes.node, children: PropTypes.node };

// No code-agent environment exists yet: say why, and offer to build one. The build
// opens in a new tab; this one asks again when the person comes back to it.
function NoEnvironment({ onBack }) {
  useEffect(() => {
    window.addEventListener("focus", onBack);
    return () => window.removeEventListener("focus", onBack);
  }, [onBack]);
  return (
    <EmptyState
      variant="no-results"
      icon="solar:box-linear"
      title="No environment can run an epoch yet"
      description="An epoch runs on a code agent, uploaded or from GitHub, in one of its environments. Build one for this issue's agent, then come back here."
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          href={paths.dashboard.simulate.environments.root}
          target="_blank"
          rel="noopener"
          data-testid="epoch-build-environment"
        >
          Build an environment
        </Button>
      }
    />
  );
}
NoEnvironment.propTypes = { onBack: PropTypes.func.isRequired };

// The first time per project: which environment runs this issue's epochs. The
// plan remembers the choice for the project's next ones.
function EnvironmentPicker({ environments, value, onPick }) {
  const [picked, setPicked] = useState(value);
  return (
    <>
      <Box sx={{ px: 3, pb: 2 }}>
        <Section
          title="Where it runs"
          description="Pick the environment that tests this issue's agent. This project's next epochs use it too."
        >
          <Stack data-testid="epoch-environments">
            {environments.map((env) => (
              <FormControlLabel
                key={env.id}
                value={env.id}
                control={
                  <Radio
                    size="small"
                    checked={picked === env.id}
                    onChange={() => setPicked(env.id)}
                  />
                }
                label={
                  <Box sx={{ py: 0.5 }}>
                    <Typography sx={{ typography: "s1" }}>
                      {env.name}
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {[
                        AGENT_SOURCE[env.source_kind],
                        AGENT_TYPE[env.agent_type],
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </Typography>
                  </Box>
                }
                sx={{ mx: 0, alignItems: "flex-start" }}
              />
            ))}
          </Stack>
        </Section>
      </Box>
      <Footer note="Only environments of code agents are listed.">
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!picked}
          onClick={() => onPick(picked)}
        >
          Continue
        </Button>
      </Footer>
    </>
  );
}
EnvironmentPicker.propTypes = {
  environments: PropTypes.array.isRequired,
  value: PropTypes.string,
  onPick: PropTypes.func.isRequired,
};

function PickRow({ pick, projectId, taken, onSwap }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ py: 0.5 }}
      data-testid="epoch-pick"
    >
      <Link
        href={pickHref(projectId, pick)}
        target="_blank"
        rel="noopener"
        underline="hover"
        sx={{
          typography: "s2",
          fontFamily: "monospace",
          color: "text.primary",
        }}
      >
        {String(pick.source_id ?? "").slice(0, 12)}
      </Link>
      <Typography
        sx={{
          typography: "s3",
          color: taken ? "text.primary" : "text.tertiary",
          flex: 1,
        }}
      >
        {taken ? "In review for another epoch" : PICK_REASON[pick.reason] || ""}
      </Typography>
      {onSwap ? (
        <Button
          size="small"
          variant="text"
          onClick={onSwap}
          sx={{ color: "text.secondary", minWidth: 0 }}
        >
          Swap
        </Button>
      ) : null}
    </Stack>
  );
}
PickRow.propTypes = {
  pick: PropTypes.object.isRequired,
  projectId: PropTypes.string,
  taken: PropTypes.bool,
  onSwap: PropTypes.func,
};

// The slim plan: no model call. The traces it samples and why, the one builder turn
// that drafts them, and where the epoch's budget is set.
function PlanForm({ plan, clusterId, projectId, onChangeEnvironment }) {
  const create = useCreateIssueEpoch();
  const { environment, blocked, sample } = plan;
  const [picks, setPicks] = useState(sample.picks);
  const [spares, setSpares] = useState(sample.alternates);
  const failure = create.error ? fixErrorOf(create.error) : null;
  const taken = new Set(
    failure?.code === "issue_epoch_picks_in_review"
      ? refusalOf(create.error).member_ids ?? []
      : [],
  );
  const swap = (index) => {
    const [next, ...rest] = spares;
    setPicks((current) => current.map((p, i) => (i === index ? next : p)));
    setSpares(rest);
  };
  const unit = UNIT_NOUN[sample.unit] || "trace";
  const held = plan.issue_scenarios;

  return (
    <>
      <Box sx={{ px: 3, pb: 2 }} data-testid="epoch-plan">
        <Row k="Runs in">
          <Stack direction="row" alignItems="baseline" spacing={1}>
            <Typography sx={{ typography: "s2" }}>
              {environment.name}
            </Typography>
            <Link
              component="button"
              type="button"
              underline="hover"
              onClick={onChangeEnvironment}
              sx={{ typography: "s3", color: "text.secondary" }}
            >
              Change
            </Link>
          </Stack>
          {held ? (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {`Holds ${held.held} of at most ${held.limit} scenarios from issues.`}
            </Typography>
          ) : null}
        </Row>
        <Row k="Traces">
          <Typography sx={{ typography: "s2" }}>
            {`${plural(picks.length, unit)} from this issue, picked to vary. Each becomes a scenario the builder drafts when you start.`}
          </Typography>
          <Box sx={{ mt: 0.5 }}>
            {picks.map((pick, i) => (
              <PickRow
                key={pick.member_id}
                pick={pick}
                projectId={projectId}
                taken={taken.has(pick.member_id)}
                onSwap={spares.length ? () => swap(i) : undefined}
              />
            ))}
          </Box>
        </Row>
        <Row k="Drafting">
          <Typography sx={{ typography: "s2" }}>
            One builder turn, billed as authoring credits.
          </Typography>
        </Row>
        <Row k="Budget">
          <Typography sx={{ typography: "s2" }}>
            {`You set the epoch's budget and limits after the review, before anything runs. It stops at your budget, ${(plan.defaults?.budget_credits ?? 0).toLocaleString("en-US")} credits by default.`}
          </Typography>
        </Row>
        {blocked ? (
          <Alert
            severity="warning"
            sx={{ mt: 1.5 }}
            action={
              blocked.code === "fix_draft_open" ? (
                <Button
                  color="inherit"
                  size="small"
                  component={RouterLink}
                  href={scenariosHref(environment.id)}
                >
                  Review unsaved changes
                </Button>
              ) : null
            }
          >
            {blocked.message}
          </Alert>
        ) : null}
        {failure ? (
          <Alert severity="error" sx={{ mt: 1.5 }}>
            {failure.message || "Could not start drafting."}
          </Alert>
        ) : null}
      </Box>
      <Footer note="You review the drafted scenarios before anything runs.">
        <LoadingButton
          size="small"
          variant="contained"
          color="primary"
          disabled={!!blocked || !picks.length}
          loading={create.isPending}
          onClick={() =>
            create.mutate({
              cluster_id: clusterId,
              project_id: projectId,
              environment_id: environment.id,
              member_ids: picks.map((p) => p.member_id),
            })
          }
          data-testid="epoch-draft-scenarios"
        >
          Draft scenarios
        </LoadingButton>
      </Footer>
    </>
  );
}
PlanForm.propTypes = {
  plan: PropTypes.object.isRequired,
  clusterId: PropTypes.string.isRequired,
  projectId: PropTypes.string,
  onChangeEnvironment: PropTypes.func.isRequired,
};

function NewPlan({ clusterId, projectId, open }) {
  const [environmentId, setEnvironmentId] = useState(null);
  const [choosing, setChoosing] = useState(false);
  const plan = useIssueEpochPlan(
    { clusterId, projectId, environmentId },
    { enabled: open },
  );
  const picking = !!plan.data && (!plan.data.environment || choosing);
  const environments = useCodeAgentEnvironments({ enabled: open && picking });
  if (plan.isLoading || (picking && environments.isLoading)) {
    return (
      <ContentSkeleton
        variant="rows"
        rows={4}
        label="Reading the issue"
        sx={{ mx: 3 }}
      />
    );
  }
  if (plan.error || !plan.data) {
    return (
      <ErrorState
        title={
          fixErrorOf(plan.error).message ||
          "Could not plan an epoch for this issue."
        }
        onRetry={() => plan.refetch()}
        sx={{ mx: 3 }}
      />
    );
  }
  if (picking) {
    if (!environments.data?.length) {
      return <NoEnvironment onBack={environments.refetch} />;
    }
    return (
      <EnvironmentPicker
        environments={environments.data}
        value={plan.data.environment?.id ?? null}
        onPick={(id) => {
          setEnvironmentId(id);
          setChoosing(false);
        }}
      />
    );
  }
  return (
    <PlanForm
      key={plan.data.environment.id}
      plan={plan.data}
      clusterId={clusterId}
      projectId={projectId}
      onChangeEnvironment={() => setChoosing(true)}
    />
  );
}
NewPlan.propTypes = {
  clusterId: PropTypes.string.isRequired,
  projectId: PropTypes.string,
  open: PropTypes.bool,
};

// What the epoch is held to, set at the one review stop: what it may change, its
// reply-time and cost limits, and its budget. Sent with Run Epoch.
function useLaunchForm(defaults) {
  const [surfaces, setSurfaces] = useState(() =>
    (defaults?.surfaces ?? []).filter((s) => s.allowed).map((s) => s.id),
  );
  const [latencyMargin, setLatencyMargin] = useState(
    defaults?.reply_latency_margin_pct ?? 0,
  );
  const [costMargin, setCostMargin] = useState(defaults?.cost_margin_pct ?? 0);
  const [costLimit, setCostLimit] = useState("");
  const [budget, setBudget] = useState(defaults?.budget_credits ?? 0);
  const [minutes, setMinutes] = useState(null);
  const limit = costLimitOf(costLimit);
  const error = !surfaces.length
    ? "Pick at least one part it may change."
    : Number.isNaN(limit)
      ? "The cost limit must be a number of dollars above 0."
      : null;
  // Left out, the time limit is sized from the suite and the cost has no limit.
  return {
    error,
    body: {
      surfaces,
      locked_paths: [],
      budget_credits: budget,
      ...(minutes == null ? {} : { max_minutes: minutes }),
      reply_latency_margin_pct: latencyMargin,
      cost_margin_pct: costMargin,
      ...(limit == null ? {} : { cost_limit_usd: limit }),
    },
    rows: (
      <>
        <SurfacesRow
          surfaces={defaults?.surfaces ?? []}
          value={surfaces}
          onChange={setSurfaces}
        />
        <LaunchLimits
          limits={defaults ?? {}}
          modelsNote={MODELS_NOTE}
          models={[]}
          onModels={() => {}}
          latencyMargin={latencyMargin}
          onLatencyMargin={setLatencyMargin}
          costMargin={costMargin}
          onCostMargin={setCostMargin}
          costLimit={costLimit}
          onCostLimit={setCostLimit}
          budget={budget}
          onBudget={setBudget}
          minutes={minutes}
          onMinutes={setMinutes}
          minutesFromSuite
        />
      </>
    ),
  };
}

const FOOTER_NOTE = {
  drafting: "Drafting goes on if you close this.",
  review: "Nothing runs until you start it.",
  blocked: "Each scenario says why it can't run.",
  measuring: "The epoch starts on its own when this run ends.",
};

function runFailure(error, environmentId) {
  if (!error) return null;
  const failure = fixErrorOf(error);
  if (failure.code !== "issue_scenarios_limit") {
    return (
      <Alert severity="error">{failure.message || "Could not start."}</Alert>
    );
  }
  const { held, limit } = refusalOf(error);
  return (
    <Alert
      severity="warning"
      action={
        <Button
          color="inherit"
          size="small"
          component={RouterLink}
          href={scenariosHref(environmentId)}
        >
          Open scenarios
        </Button>
      }
    >
      {`This environment already holds ${held} of at most ${limit} scenarios from issues. Remove some on its Scenarios tab, then run the epoch.`}
    </Alert>
  );
}

// Drafting, the one review stop, and the run on the current version. The review
// keeps or drops each drafted scenario; Run Epoch saves the kept ones and starts.
// A blocked draft only shows why; the person plans again.
function JobSteps({ status, onReplan }) {
  const key = epochKey(status);
  const evidence = status.evidenceImport;
  const cases = evidence?.cases ?? [];
  const keepable = cases.filter((one) => KEEPABLE.has(one.status));
  const [dropped, setDropped] = useState([]);
  const kept = keepable.filter((one) => !dropped.includes(one.id));
  const launch = useLaunchForm(status.defaults);
  const run = useRunIssueEpoch(status.jobId);
  const stop = useStopIssueEpoch(status.jobId);
  const baseline = status.baseline;
  const shown = key === "review" ? keepable : cases;

  return (
    <>
      <Box sx={{ px: 3, pb: 2 }} data-testid={`epoch-step-${key}`}>
        {key === "drafting" ? (
          <Alert severity="info" sx={{ mb: 1.5 }}>
            {`Drafting ${plural(cases.length, "scenario")} from the issue's traces. One builder turn, billed as authoring credits.`}
          </Alert>
        ) : null}
        {key === "measuring" ? (
          <Alert severity="info" sx={{ mb: 1.5 }}>
            {baseline
              ? `Ran ${baseline.completed_count} of ${plural(baseline.scenarios, "scenario")} on the current version, ${baseline.failed_count} failing so far. The epoch starts when this run ends.`
              : "Running the kept scenarios on the current version. The epoch starts when this run ends."}
          </Alert>
        ) : null}
        {key === "review" ? (
          <Typography
            sx={{ typography: "s1", color: "text.secondary", mb: 1.5 }}
          >
            {`${plural(keepable.length, "scenario")} to review. Keep the ones that show the issue. Run Epoch adds the kept ones to the environment's scenarios and runs them on the current version first.`}
          </Typography>
        ) : null}
        <Stack spacing={1.25}>
          {shown.map((one) => (
            <Stack
              key={one.id}
              direction="row"
              spacing={1}
              alignItems="flex-start"
            >
              {key === "review" ? (
                <Checkbox
                  size="small"
                  checked={!dropped.includes(one.id)}
                  onChange={() =>
                    setDropped((current) =>
                      current.includes(one.id)
                        ? current.filter((x) => x !== one.id)
                        : [...current, one.id],
                    )
                  }
                  inputProps={{ "aria-label": "Keep this scenario" }}
                  sx={{ mt: 1 }}
                />
              ) : null}
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <EvidenceCaseCard
                  item={one}
                  kind={evidence?.source_kind}
                  simulated={Boolean(evidence?.simulated)}
                  production={Boolean(evidence?.production)}
                  observeHref={pickHref(evidence?.project_id, {
                    ...one,
                    unit: evidence?.source_kind,
                  })}
                />
              </Box>
            </Stack>
          ))}
        </Stack>
        {key === "review" ? (
          <Box sx={{ mt: 2 }} data-testid="epoch-launch-form">
            {launch.rows}
          </Box>
        ) : null}
        <Box sx={{ mt: 1.5 }}>
          {runFailure(run.error, status.environmentId)}
          {stop.error ? (
            <Alert severity="error">
              {fixErrorOf(stop.error).message || "Could not stop it."}
            </Alert>
          ) : null}
        </Box>
      </Box>
      <Footer
        note={
          launch.error && key === "review" ? launch.error : FOOTER_NOTE[key]
        }
      >
        {key === "blocked" ? (
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={onReplan}
          >
            Plan again
          </Button>
        ) : (
          <Button
            size="small"
            onClick={() => stop.mutate()}
            disabled={stop.isPending}
            sx={{ color: "text.secondary" }}
          >
            {key === "measuring" ? "Stop" : "Discard"}
          </Button>
        )}
        {key === "review" ? (
          <LoadingButton
            size="small"
            variant="contained"
            color="primary"
            disabled={!kept.length || !!launch.error}
            loading={run.isPending}
            onClick={() =>
              run.mutate({
                case_ids: kept.map((one) => one.id),
                ...launch.body,
              })
            }
            data-testid="epoch-run"
          >
            Run Epoch
          </LoadingButton>
        ) : null}
      </Footer>
    </>
  );
}
JobSteps.propTypes = {
  status: PropTypes.object.isRequired,
  onReplan: PropTypes.func.isRequired,
};

// The epoch started while the drawer was open: say so, and open it from here.
function Started({ fix }) {
  return (
    <EmptyState
      variant="no-results"
      icon="solar:magic-stick-3-linear"
      title={`${fixName(fix)} started`}
      description="It searches for a change on its own. Its page shows each step, and this issue shows how it ends."
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          component={RouterLink}
          href={epochHref(fix)}
        >
          {`View ${fixName(fix)}`}
        </Button>
      }
    />
  );
}
Started.propTypes = { fix: PropTypes.object.isRequired };

const TITLES = {
  drafting: "Drafting scenarios",
  review: "Review the drafted scenarios",
  blocked: "None of the drafts can run",
  measuring: "Measuring the current version",
};

// Run Epoch from an issue, in one drawer: the environment the first time, the slim
// plan, then drafting, the one review stop and the run that starts the epoch.
export default function RunEpochDrawer({
  open,
  onClose,
  clusterId,
  projectId,
  issueTitle,
}) {
  const { allowed } = useFeatureAllowed(CAPABILITY.FIX_MY_AGENT);
  const status = useIssueEpoch({ clusterId, projectId });
  // A blocked job is not open: planning again starts a new one beside it.
  const [replanFor, setReplanFor] = useState(null);
  if (!allowed) return null;
  const key = epochKey(status);
  const replanning = key === "blocked" && replanFor === status.jobId;
  const preLaunch = PRE_LAUNCH[key] && !replanning ? key : null;
  let body = (
    <NewPlan clusterId={clusterId} projectId={projectId} open={open} />
  );
  if (preLaunch) {
    body = (
      <JobSteps
        key={status.jobId}
        status={status}
        onReplan={() => setReplanFor(status.jobId)}
      />
    );
  } else if (isEpochLive(status)) {
    body = <Started fix={status.epoch} />;
  }
  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      <Box
        sx={{ display: "flex", flexDirection: "column", minHeight: "100%" }}
        data-testid="run-epoch-drawer"
      >
        <Box sx={{ px: 3, pt: 3, pb: 1, pr: 6 }}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>
            {TITLES[preLaunch] || "Run Epoch"}
          </Typography>
          {issueTitle ? (
            <Typography
              noWrap
              sx={{ typography: "s1", color: "text.tertiary" }}
            >
              {issueTitle}
            </Typography>
          ) : null}
        </Box>
        {body}
      </Box>
    </SideDrawer>
  );
}

RunEpochDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  clusterId: PropTypes.string.isRequired,
  projectId: PropTypes.string,
  issueTitle: PropTypes.string,
};
