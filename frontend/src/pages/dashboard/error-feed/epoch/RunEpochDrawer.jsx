import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
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
import { paths } from "src/routes/paths";
import {
  issuePlanOf,
  useCreateIssueEpoch,
  useIssueEpoch,
  useIssueEpochPlan,
  useRunIssueEpoch,
  useStopIssueEpoch,
} from "src/api/errorFeed/epochFromIssue";
import { fixErrorOf } from "src/api/simulate-environments/fixRuns";
import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import {
  isEvidenceCaseDrafted,
  useEvidenceImport,
} from "src/api/simulate-environments/evidenceImports";
import SideDrawer from "src/sections/simulate/environments/components/SideDrawer";
import EvidenceCaseCard from "src/sections/simulate/environments/evidence/EvidenceCaseCard";
import LaunchLimits, {
  Row,
} from "src/sections/simulate/environments/workspace/improvements/LaunchLimits";
import {
  NO_MODELS_NOTE,
  UNTRACED_NOTE,
  costLimitOf,
  isTraced,
  seesModels,
} from "src/sections/simulate/environments/workspace/improvements/launchFormView";

import { RouterLink } from "src/routes/components";
import { fixName } from "src/sections/simulate/environments/workspace/improvements/fixRunView";

import { epochHref } from "./IssueEpochAction";
import { PRE_LAUNCH, epochKey, isEpochLive } from "./issueEpochView";

const AGENT_KIND = { github: "GitHub", archive: "Uploaded" };

// Why the plan picked a trace, in words.
const PICK_REASON = {
  prototype: "Typical of the issue",
  cited: "Cited in the root cause",
  recent: "Recent",
};

const shortId = (id) => String(id ?? "").slice(0, 12);

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

function Header({ title, subtitle }) {
  return (
    <Box sx={{ px: 3, pt: 3, pb: 1, pr: 6 }}>
      <Typography sx={{ typography: "m2", fontWeight: 600 }}>
        {title}
      </Typography>
      {subtitle ? (
        <Typography noWrap sx={{ typography: "s1", color: "text.tertiary" }}>
          {subtitle}
        </Typography>
      ) : null}
    </Box>
  );
}
Header.propTypes = { title: PropTypes.string, subtitle: PropTypes.string };

// No code-agent environment can run this issue's epoch: say why, and offer to build
// one. The build opens in a new tab; this one asks again when the person comes back.
function NoEnvironment({ noFit, onBack }) {
  useEffect(() => {
    window.addEventListener("focus", onBack);
    return () => window.removeEventListener("focus", onBack);
  }, [onBack]);
  return (
    <EmptyState
      variant="no-results"
      icon="solar:box-linear"
      title="No environment can run an epoch for this issue"
      description={
        noFit?.message ||
        "An epoch runs on a code agent, uploaded or from GitHub, in one of its environments. Build one for this issue's agent, then come back here."
      }
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
NoEnvironment.propTypes = {
  noFit: PropTypes.object,
  onBack: PropTypes.func.isRequired,
};

// The first time per project: which environment runs this issue's epochs. The
// plan remembers the choice for the project.
function EnvironmentPicker({ environments, value, onPick }) {
  const [picked, setPicked] = useState(value);
  return (
    <>
      <Box sx={{ px: 3, pb: 2 }}>
        <Section
          title="Where it runs"
          description="Pick the environment that tests this issue's agent. It is used for this project's next epochs too."
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
                      {[env.agent?.name, AGENT_KIND[env.agent?.kind]]
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

function PickRow({ pick, projectId, onSwap }) {
  const reason = PICK_REASON[pick.reason];
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ py: 0.5 }}
      data-testid="epoch-pick"
    >
      <Link
        href={
          projectId
            ? `/dashboard/observe/${projectId}/trace/${encodeURIComponent(pick.trace_id)}`
            : undefined
        }
        target="_blank"
        rel="noopener"
        underline="hover"
        sx={{
          typography: "s2",
          fontFamily: "monospace",
          color: "text.primary",
        }}
      >
        {shortId(pick.trace_id)}
      </Link>
      <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
        {reason || ""}
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
  onSwap: PropTypes.func,
};

// The slim plan: no model call. The traces it samples, the one builder turn that
// drafts them, and the budget the epoch is held to.
function PlanForm({ plan, clusterId, projectId, onChangeEnvironment }) {
  const create = useCreateIssueEpoch();
  const { defaults, limits, environment } = plan;
  const [picks, setPicks] = useState(plan.picks);
  const [spares, setSpares] = useState(plan.alternates);
  const [models, setModels] = useState(defaults.allowed_models ?? []);
  const [latencyMargin, setLatencyMargin] = useState(
    defaults.reply_latency_margin_pct,
  );
  const [costMargin, setCostMargin] = useState(defaults.cost_margin_pct);
  const [costLimit, setCostLimit] = useState("");
  const [budget, setBudget] = useState(defaults.budget_credits);
  const [minutes, setMinutes] = useState(defaults.max_minutes);

  const options = { defaults };
  const lockable = seesModels(options);
  const swap = (index) => {
    const [next, ...rest] = spares;
    setPicks((current) => current.map((p, i) => (i === index ? next : p)));
    setSpares(rest);
  };
  const error = Number.isNaN(costLimitOf(costLimit))
    ? "The cost limit must be a number of dollars above 0."
    : lockable && !models.length
      ? "Allow at least one model."
      : null;

  const draft = () =>
    create.mutate({
      cluster_id: clusterId,
      environment_id: environment.id,
      trace_ids: picks.map((p) => p.trace_id),
      launch: {
        ...(lockable ? { allowed_models: models } : {}),
        reply_latency_margin_pct: latencyMargin,
        cost_margin_pct: costMargin,
        cost_limit_usd: costLimitOf(costLimit),
        budget_credits: budget,
        max_minutes: minutes,
      },
    });

  return (
    <>
      <Box sx={{ px: 3, pb: 2 }} data-testid="epoch-plan">
        <Row k="Runs in">
          <Stack direction="row" alignItems="baseline" spacing={1}>
            <Typography sx={{ typography: "s2" }}>
              {environment.name}
            </Typography>
            {onChangeEnvironment ? (
              <Link
                component="button"
                type="button"
                underline="hover"
                onClick={onChangeEnvironment}
                sx={{ typography: "s3", color: "text.secondary" }}
              >
                Change
              </Link>
            ) : null}
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {[environment.agent?.name, AGENT_KIND[environment.agent?.kind]]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
        </Row>
        <Row k="Traces">
          <Typography sx={{ typography: "s2" }}>
            {`${picks.length} ${picks.length === 1 ? "trace" : "traces"} from this issue, at most one per session. Each becomes a scenario the builder drafts when you start.`}
          </Typography>
          <Box sx={{ mt: 0.5 }}>
            {picks.map((pick, i) => (
              <PickRow
                key={pick.trace_id}
                pick={pick}
                projectId={projectId}
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
        <LaunchLimits
          limits={limits}
          modelsNote={
            lockable ? null : isTraced(options) ? NO_MODELS_NOTE : UNTRACED_NOTE
          }
          models={models}
          onModels={setModels}
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
        />
        {create.error ? (
          <Alert severity="error" sx={{ mt: 1.5 }}>
            {fixErrorOf(create.error).message || "Could not start drafting."}
          </Alert>
        ) : null}
      </Box>
      <Footer
        note={error || "You review the drafted scenarios before anything runs."}
      >
        <LoadingButton
          size="small"
          variant="contained"
          color="primary"
          disabled={!!error || !picks.length}
          loading={create.isPending}
          onClick={draft}
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
  onChangeEnvironment: PropTypes.func,
};

function NewPlan({ clusterId, projectId, open }) {
  const [environmentId, setEnvironmentId] = useState(null);
  const [choosing, setChoosing] = useState(false);
  const { data, isLoading, error, refetch } = useIssueEpochPlan(clusterId, {
    environmentId,
    enabled: open,
  });
  const plan = issuePlanOf(data);
  if (isLoading) {
    return (
      <ContentSkeleton
        variant="rows"
        rows={4}
        label="Reading the issue"
        sx={{ mx: 3 }}
      />
    );
  }
  if (error || !plan?.defaults || !plan?.limits) {
    return (
      <ErrorState
        title={
          fixErrorOf(error).message || "Could not plan an epoch for this issue."
        }
        onRetry={() => refetch()}
        sx={{ mx: 3 }}
      />
    );
  }
  if (!plan.environments.length) {
    return <NoEnvironment noFit={plan.noFit} onBack={refetch} />;
  }
  if (!plan.environment || choosing) {
    return (
      <EnvironmentPicker
        environments={plan.environments}
        value={plan.environment?.id ?? null}
        onPick={(id) => {
          setEnvironmentId(id);
          setChoosing(false);
        }}
      />
    );
  }
  return (
    <PlanForm
      key={plan.environment.id}
      plan={plan}
      clusterId={clusterId}
      projectId={projectId}
      onChangeEnvironment={
        plan.environments.length > 1 ? () => setChoosing(true) : undefined
      }
    />
  );
}
NewPlan.propTypes = {
  clusterId: PropTypes.string.isRequired,
  projectId: PropTypes.string,
  open: PropTypes.bool,
};

const FOOTER_NOTE = {
  drafting: "Drafting goes on if you close this.",
  review: "Nothing runs until you start it.",
  measuring: "The epoch starts on its own when this run ends.",
};

// Drafting, the one review stop, and the run on the current version. The review
// keeps or drops each drafted scenario; Run Epoch saves the kept ones and starts.
function JobSteps({ status }) {
  const key = epochKey(status);
  const { data, isLoading } = useEvidenceImport(
    status.environmentId,
    status.importId,
  );
  const run = useRunIssueEpoch(status.jobId);
  const stop = useStopIssueEpoch(status.jobId);
  const cases = useMemo(() => data?.cases ?? [], [data]);
  const drafted = cases.filter(isEvidenceCaseDrafted);
  const [dropped, setDropped] = useState([]);
  const kept = drafted.filter((one) => !dropped.includes(one.id));
  const failure = run.error || stop.error;

  return (
    <>
      <Box sx={{ px: 3, pb: 2 }} data-testid={`epoch-step-${key}`}>
        {key === "drafting" ? (
          <Alert severity="info" sx={{ mb: 1.5 }}>
            {`Drafting ${cases.length ? `${cases.length} ` : ""}scenarios from the issue's traces. One builder turn, billed as authoring credits.`}
          </Alert>
        ) : null}
        {key === "measuring" ? (
          <Alert severity="info" sx={{ mb: 1.5 }}>
            Running the kept scenarios on the current version. The epoch starts
            when that run ends, and its page opens from this issue.
          </Alert>
        ) : null}
        {key === "review" ? (
          <Typography
            sx={{ typography: "s1", color: "text.secondary", mb: 1.5 }}
          >
            {`${drafted.length} drafted ${drafted.length === 1 ? "scenario" : "scenarios"}. Keep the ones that show the issue. Run Epoch adds the kept ones to the environment's scenarios and runs them on the current version first.`}
          </Typography>
        ) : null}
        {isLoading ? <ContentSkeleton variant="rows" rows={3} /> : null}
        <Stack spacing={1.25}>
          {(key === "review" ? drafted : cases).map((one) => (
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
                  kind={data?.source_kind ?? "session"}
                  simulated={Boolean(data?.simulated)}
                  production={Boolean(data?.production)}
                />
              </Box>
            </Stack>
          ))}
        </Stack>
        {failure ? (
          <Alert severity="error" sx={{ mt: 1.5 }}>
            {fixErrorOf(failure).message || "Could not do that. Try again."}
          </Alert>
        ) : null}
      </Box>
      <Footer note={FOOTER_NOTE[key]}>
        <Button
          size="small"
          onClick={() => stop.mutate()}
          disabled={stop.isPending}
          sx={{ color: "text.secondary" }}
        >
          {key === "measuring" ? "Stop" : "Discard"}
        </Button>
        {key === "review" ? (
          <LoadingButton
            size="small"
            variant="contained"
            color="primary"
            disabled={!kept.length}
            loading={run.isPending}
            onClick={() => run.mutate({ case_ids: kept.map((one) => one.id) })}
            data-testid="epoch-run"
          >
            Run Epoch
          </LoadingButton>
        ) : null}
      </Footer>
    </>
  );
}
JobSteps.propTypes = { status: PropTypes.object.isRequired };

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
  const status = useIssueEpoch(clusterId);
  if (!allowed) return null;
  const key = epochKey(status);
  const preLaunch = PRE_LAUNCH[key] ? key : null;
  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      <Box
        sx={{ display: "flex", flexDirection: "column", minHeight: "100%" }}
        data-testid="run-epoch-drawer"
      >
        <Header
          title={TITLES[preLaunch] || "Run Epoch"}
          subtitle={issueTitle}
        />
        {preLaunch ? <JobSteps status={status} /> : null}
        {!preLaunch && isEpochLive(status) ? (
          <Started fix={status.epoch} />
        ) : null}
        {!preLaunch && !isEpochLive(status) ? (
          <NewPlan clusterId={clusterId} projectId={projectId} open={open} />
        ) : null}
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
