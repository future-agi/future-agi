import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  InputAdornment,
  Link,
  Radio,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import LoadingButton from "@mui/lab/LoadingButton";

import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import { useSnackbar } from "src/components/snackbar";
import { RouterLink } from "src/routes/components";
import { useRouter } from "src/routes/hooks";
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
import SideDrawer from "src/sections/simulate/environments/components/SideDrawer";
import EvidenceCaseCard from "src/sections/simulate/environments/evidence/EvidenceCaseCard";
import { observeHref } from "src/sections/simulate/environments/evidence/evidence.constants";
import LaunchLimits, {
  Row,
  SurfacesRow,
} from "src/sections/simulate/environments/workspace/improvements/LaunchLimits";
import {
  blockerAction,
  blockerHint,
  shipHeadline,
} from "src/sections/simulate/environments/workspace/improvements/epochBlockers";
import { fixName } from "src/sections/simulate/environments/workspace/improvements/fixRunView";
import { costLimitOf } from "src/sections/simulate/environments/workspace/improvements/launchFormView";

import {
  PRE_LAUNCH,
  epochHref,
  epochKey,
  epochLabel,
  epochSentence,
  isEpochLive,
} from "./issueEpochView";

const AGENT_SOURCE = { github: "GitHub", archive: "Uploaded" };
const AGENT_TYPE = { voice: "Voice", chat: "Chat" };
// Why the plan picked a trace, in words.
const PICK_REASON = {
  example: "Typical of the issue",
  cited: "Cited in the root cause",
  recent: "Recent",
};
const UNIT_NOUN = { call: "call", session: "session", trace: "trace" };
// Without traces from a run yet, the epoch locks to the models its baseline calls.
const MODELS_NOTE =
  "Locked to the models the current version calls, read from its run before the epoch starts.";
// No job on the issue yet: a new plan is for "none".
const NO_JOB = "none";

const plural = (n, noun) => `${n} ${noun}${n === 1 ? "" : "s"}`;
// Above this many environments the picker gets a search box.
const SEARCH_FROM = 8;

// A step's heading and the sentence under it.
function StepIntro({ title, children }) {
  return (
    <Box sx={{ mb: 1.5 }}>
      {title ? (
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          {title}
        </Typography>
      ) : null}
      {children ? (
        <Typography
          sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
        >
          {children}
        </Typography>
      ) : null}
    </Box>
  );
}
StepIntro.propTypes = { title: PropTypes.node, children: PropTypes.node };

// A small list heading: 11/500, tertiary, sentence case.
function GroupLabel({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 500,
        color: "text.tertiary",
        mt: 1.5,
        mb: 0.75,
        "&:first-of-type": { mt: 0 },
      }}
    >
      {children}
    </Typography>
  );
}
GroupLabel.propTypes = { children: PropTypes.node };

// A bordered key-value sheet: the plan's rows read as one block.
const sheetSx = {
  border: 1,
  borderColor: "divider",
  borderRadius: 1,
  px: 1.5,
  py: 0.5,
  "& > *:first-of-type": { pt: 1 },
};
const scenariosHref = (envId) =>
  paths.dashboard.simulate.environments.workspaceTab(envId, "scenarios");
// A refusal's body: its code, message and any fields it names, at the top level
// (the app's axios spreads it onto the error; fixErrorOf reads the same).
const refusalOf = (error) => error?.response?.data ?? error ?? {};

// What the review can keep: drafted cases, and repeats of a scenario the suite
// still holds. A repeat whose original is gone names none, and can't run.
const isKeepable = (one) =>
  one.status === "drafted" ||
  (one.status === "duplicate" && !!one.duplicate_of);

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
// opens in a new tab; the list is read again when the person comes back to this one.
function NoEnvironment() {
  return (
    <Box sx={{ px: 2, pt: 2, pb: 2 }} data-testid="epoch-no-environment">
      <StepIntro title="No environment can run an epoch yet">
        An epoch runs on a code agent, uploaded or from GitHub, in one of its
        environments. Build one for this issue&apos;s agent, then come back
        here.
      </StepIntro>
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
    </Box>
  );
}

// The first time per project: which environment runs this issue's epochs. The
// plan remembers the choice for the project's next ones. One bordered row per
// environment, grouped by the agent's channel.
// The picker's tabs: every environment, then one per agent type present.
const TYPE_ORDER = ["Chat", "Voice", "Other"];
const typeOf = (env) => AGENT_TYPE[env.agent_type] || "Other";

function EnvironmentPicker({ environments, value, onPick }) {
  const [picked, setPicked] = useState(value);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState("All");
  const types = TYPE_ORDER.filter((t) =>
    environments.some((env) => typeOf(env) === t),
  );
  const q = query.trim().toLowerCase();
  const inTab =
    tab === "All"
      ? environments
      : environments.filter((env) => typeOf(env) === tab);
  const shown = q
    ? inTab.filter((env) => env.name.toLowerCase().includes(q))
    : inTab;
  const meta = (env) =>
    [
      AGENT_SOURCE[env.source_kind],
      tab === "All" ? AGENT_TYPE[env.agent_type] : null,
    ]
      .filter(Boolean)
      .join(" · ");
  return (
    <>
      <Box sx={{ px: 2, pt: 2, pb: 2 }}>
        <StepIntro title="Where it runs">
          Pick the environment that tests this issue&apos;s agent. This
          project&apos;s next epochs use it too.
        </StepIntro>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
          {environments.length > SEARCH_FROM ? (
            <TextField
              size="small"
              placeholder="Search environments"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              inputProps={{ "aria-label": "Search environments" }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify
                      icon="solar:magnifer-linear"
                      width={16}
                      sx={{ color: "text.disabled" }}
                    />
                  </InputAdornment>
                ),
              }}
              sx={{ flex: 1, minWidth: 0 }}
            />
          ) : null}
          {types.length > 1 ? (
            <ToggleButtonGroup
              exclusive
              size="small"
              value={tab}
              onChange={(_, next) => next && setTab(next)}
              aria-label="Agent type"
              sx={{ flexShrink: 0 }}
            >
              {["All", ...types].map((t) => (
                <ToggleButton key={t} value={t}>
                  {t}
                  <Box
                    component="span"
                    sx={{ ml: 0.5, color: "text.disabled" }}
                  >
                    {t === "All"
                      ? environments.length
                      : environments.filter((env) => typeOf(env) === t).length}
                  </Box>
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          ) : null}
        </Stack>
        <Box
          data-testid="epoch-environments"
          role="radiogroup"
          aria-label="Environments"
          sx={
            shown.length
              ? {
                  border: 1,
                  borderColor: "divider",
                  borderRadius: 1,
                  overflow: "hidden",
                }
              : null
          }
        >
          {shown.map((env, i) => {
            const selected = picked === env.id;
            return (
              <Box
                key={env.id}
                component="label"
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1.25,
                  px: 1.25,
                  minHeight: 40,
                  borderTop: i ? 1 : 0,
                  borderColor: "divider",
                  cursor: "pointer",
                  bgcolor: selected
                    ? (t) => alpha(t.palette.primary.main, 0.06)
                    : "transparent",
                  "&:hover": selected ? {} : { bgcolor: "action.hover" },
                }}
              >
                <Radio
                  size="small"
                  checked={selected}
                  onChange={() => setPicked(env.id)}
                  inputProps={{ "aria-label": env.name }}
                  sx={{ p: 0 }}
                />
                <Typography
                  noWrap
                  sx={{
                    typography: "s1",
                    flex: 1,
                    minWidth: 0,
                    fontWeight: selected ? 500 : 400,
                  }}
                >
                  {env.name}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flexShrink: 0,
                  }}
                >
                  {meta(env)}
                </Typography>
              </Box>
            );
          })}
          {!shown.length ? (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {q
                ? `No environment matches "${query.trim()}".`
                : "No environment of this type."}
            </Typography>
          ) : null}
        </Box>
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
      sx={{
        px: 1.25,
        py: 0.5,
        minHeight: 36,
        borderTop: 1,
        borderColor: "divider",
        "&:first-of-type": { borderTop: 0 },
      }}
      data-testid="epoch-pick"
    >
      <Link
        href={observeHref(projectId, pick.unit, pick) ?? undefined}
        target="_blank"
        rel="noopener"
        underline="hover"
        sx={{ typography: "code_sm", color: "text.primary" }}
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

// Why nothing can be drafted here now, and the way out: a page that fixes it, or
// another environment when this one's agent can't be changed.
function BlockedAlert({ blocked, environmentId, onChangeEnvironment }) {
  const next = blockerAction(blocked.code, environmentId);
  const hint = blockerHint(blocked.code);
  let action = null;
  if (next) {
    action = (
      <Button
        color="inherit"
        size="small"
        component={RouterLink}
        href={next.href}
        data-testid="epoch-blocker-action"
      >
        {next.label}
      </Button>
    );
  } else if (blocked.code === "fix_agent_not_editable") {
    action = (
      <Button color="inherit" size="small" onClick={onChangeEnvironment}>
        Pick another environment
      </Button>
    );
  }
  return (
    <Alert
      severity="warning"
      sx={{ mt: 1.5 }}
      action={action}
      data-testid="epoch-blocked"
    >
      {hint ? `${blocked.message} ${hint}` : blocked.message}
    </Alert>
  );
}
BlockedAlert.propTypes = {
  blocked: PropTypes.object.isRequired,
  environmentId: PropTypes.string.isRequired,
  onChangeEnvironment: PropTypes.func.isRequired,
};

// How the winner would ship, and what a pull request still needs.
function ShipsAsRow({ ship }) {
  const next = ship.blocked ? blockerAction(ship.blocked.code) : null;
  return (
    <Row k="Ships as">
      <Typography sx={{ typography: "s2" }}>{shipHeadline(ship)}</Typography>
      {ship.blocked ? (
        <Typography
          sx={{ typography: "s3", color: "text.secondary" }}
          data-testid="epoch-ship-blocked"
        >
          {ship.blocked.message}{" "}
          {next ? (
            <Link component={RouterLink} href={next.href} underline="hover">
              {next.label}
            </Link>
          ) : null}
        </Typography>
      ) : null}
    </Row>
  );
}
ShipsAsRow.propTypes = { ship: PropTypes.object.isRequired };

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
      <Box sx={{ px: 2, pt: 2, pb: 2 }} data-testid="epoch-plan">
        <StepIntro title="The plan">
          Nothing runs yet. Drafting turns these traces into scenarios you
          review next.
        </StepIntro>
        <Box sx={sheetSx}>
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
              {picks.length
                ? `${plural(picks.length, unit)} from this issue, picked to vary. Each becomes a scenario the builder drafts when you start.`
                : "No trace of this issue can be drafted in this environment."}
            </Typography>
            <Box
              sx={{
                mt: 0.75,
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
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
              {`You set the epoch's budget and limits after the review, before anything runs. It stops at your budget, ${plan.defaults.budget_credits.toLocaleString("en-US")} credits by default.`}
            </Typography>
          </Row>
          {plan.ship ? <ShipsAsRow ship={plan.ship} /> : null}
        </Box>
        {!blocked && !picks.length ? (
          <Alert
            severity="info"
            sx={{ mt: 1.5 }}
            action={
              <Button
                color="inherit"
                size="small"
                onClick={onChangeEnvironment}
              >
                Pick another environment
              </Button>
            }
          >
            Its traces may have expired, or another import in this environment
            is still reviewing them.
          </Alert>
        ) : null}
        {blocked ? (
          <BlockedAlert
            blocked={blocked}
            environmentId={environment.id}
            onChangeEnvironment={onChangeEnvironment}
          />
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
          // Once created, the issue's new job takes the drawer over.
          disabled={!!blocked || !picks.length || create.isSuccess}
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
  projectId: PropTypes.string.isRequired,
  onChangeEnvironment: PropTypes.func.isRequired,
};

function NewPlan({ clusterId, projectId }) {
  const [environmentId, setEnvironmentId] = useState(null);
  const [choosing, setChoosing] = useState(false);
  const plan = useIssueEpochPlan(
    { clusterId, projectId, environmentId },
    { enabled: true },
  );
  const unpicked = !!plan.data && !plan.data.environment;
  // The plan says whether any of the caller's environments can run an epoch.
  const none = unpicked && plan.data.blocked?.code === "environment_none";
  const picking = choosing || (unpicked && !none);
  const environments = useCodeAgentEnvironments({ enabled: picking });
  if (none && !choosing) return <NoEnvironment />;
  if (picking) {
    if (environments.isLoading) {
      return (
        <Box sx={{ px: 2, pt: 2 }}>
          <ContentSkeleton variant="rows" rows={3} />
        </Box>
      );
    }
    // A failed read is not an empty list: building an environment would not help.
    if (environments.isError) {
      return (
        <Box sx={{ mx: 2, mt: 2 }}>
          <ErrorState
            title="Could not load your environments."
            onRetry={() => environments.refetch()}
          />
        </Box>
      );
    }
    if (!environments.data?.length) return <NoEnvironment />;
    return (
      <EnvironmentPicker
        environments={environments.data}
        value={environmentId ?? plan.data?.environment?.id ?? null}
        onPick={(id) => {
          setEnvironmentId(id);
          setChoosing(false);
        }}
      />
    );
  }
  if (plan.isLoading) {
    return (
      <Box sx={{ px: 2, pt: 2 }}>
        <ContentSkeleton variant="rows" rows={4} label="Reading the issue" />
      </Box>
    );
  }
  if (plan.error || !plan.data) {
    // The picked environment may not take evidence from this issue's project: offer
    // another one beside the retry, which can't succeed then.
    return (
      <Stack alignItems="center" spacing={1} sx={{ mx: 2, mt: 2 }}>
        <ErrorState
          title={
            fixErrorOf(plan.error).message ||
            "Could not plan an epoch for this issue."
          }
          onRetry={() => plan.refetch()}
        />
        <Button
          size="small"
          variant="outlined"
          onClick={() => setChoosing(true)}
        >
          Pick another environment
        </Button>
      </Stack>
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
  projectId: PropTypes.string.isRequired,
};

// What the epoch is held to, set at the one review stop: what it may change, its
// reply-time and cost limits, and its budget. Sent with Run Epoch.
function useLaunchForm(defaults) {
  const [surfaces, setSurfaces] = useState(() =>
    defaults.surfaces.filter((s) => s.allowed).map((s) => s.id),
  );
  const [latencyMargin, setLatencyMargin] = useState(
    defaults.reply_latency_margin_pct,
  );
  const [costMargin, setCostMargin] = useState(defaults.cost_margin_pct);
  const [costLimit, setCostLimit] = useState("");
  const [budget, setBudget] = useState(defaults.budget_credits);
  const [minutes, setMinutes] = useState(null);
  const limit = costLimitOf(costLimit);
  const error = !surfaces.length
    ? "Pick at least one part it may change."
    : Number.isNaN(limit)
      ? "The cost limit must be a number of dollars above 0."
      : null;
  return {
    error,
    // Left out, the time limit is sized from the suite and the cost has no limit.
    body: {
      surfaces,
      locked_paths: [],
      budget_credits: budget,
      ...(minutes == null ? {} : { max_minutes: minutes }),
      reply_latency_margin_pct: latencyMargin,
      cost_margin_pct: costMargin,
      ...(limit == null ? {} : { cost_limit_usd: limit }),
    },
    fields: {
      surfaces,
      setSurfaces,
      latencyMargin,
      setLatencyMargin,
      costMargin,
      setCostMargin,
      costLimit,
      setCostLimit,
      budget,
      setBudget,
      minutes,
      setMinutes,
    },
  };
}

function CaseList({ evidence, cases, dropped, onToggle }) {
  return (
    <Stack spacing={1.25}>
      {cases.map((one) => (
        <Stack key={one.id} direction="row" spacing={1} alignItems="flex-start">
          {onToggle ? (
            <Checkbox
              size="small"
              checked={!dropped.includes(one.id)}
              onChange={() => onToggle(one.id)}
              inputProps={{ "aria-label": "Keep this scenario" }}
              sx={{ mt: 1 }}
            />
          ) : null}
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <EvidenceCaseCard
              item={one}
              kind={evidence.source_kind}
              simulated={Boolean(evidence.simulated)}
              production={Boolean(evidence.production)}
              observeHref={
                observeHref(evidence.project_id, evidence.source_kind, one) ??
                undefined
              }
            />
          </Box>
        </Stack>
      ))}
    </Stack>
  );
}
CaseList.propTypes = {
  evidence: PropTypes.object.isRequired,
  cases: PropTypes.array.isRequired,
  dropped: PropTypes.array,
  onToggle: PropTypes.func,
};

function runFailure(error, environmentId) {
  if (!error) return null;
  const failure = fixErrorOf(error);
  if (failure.code !== "issue_scenarios_limit") {
    const hint = blockerHint(failure.code);
    const next = blockerAction(failure.code, environmentId, refusalOf(error));
    return (
      <Alert
        severity="error"
        action={
          next ? (
            <Button
              color="inherit"
              size="small"
              component={RouterLink}
              href={next.href}
            >
              {next.label}
            </Button>
          ) : null
        }
      >
        {[failure.message || "Could not start.", hint]
          .filter(Boolean)
          .join(" ")}
      </Alert>
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

// The one review stop: keep or drop each drafted scenario, set what the epoch is held
// to, then Run Epoch saves the kept ones and starts it.
function ReviewStep({ status }) {
  const evidence = status.evidenceImport;
  const keepable = evidence.cases.filter(isKeepable);
  const [dropped, setDropped] = useState([]);
  const kept = keepable.filter((one) => !dropped.includes(one.id));
  const launch = useLaunchForm(status.defaults);
  const f = launch.fields;
  const run = useRunIssueEpoch(status.jobId);
  const stop = useStopIssueEpoch(status.jobId);
  const toggle = (id) =>
    setDropped((current) =>
      current.includes(id) ? current.filter((x) => x !== id) : [...current, id],
    );
  return (
    <>
      <Box sx={{ px: 2, pt: 2, pb: 2 }} data-testid="epoch-step-review">
        <StepIntro>
          {`${plural(keepable.length, "scenario")} to review. Keep the ones that show the issue. Run Epoch adds the kept ones to the environment's scenarios and runs them on the current version first.`}
        </StepIntro>
        <GroupLabel>{`Scenarios · ${kept.length} of ${keepable.length} kept`}</GroupLabel>
        <CaseList
          evidence={evidence}
          cases={keepable}
          dropped={dropped}
          onToggle={toggle}
        />
        <GroupLabel>What the epoch is held to</GroupLabel>
        <Box sx={sheetSx} data-testid="epoch-launch-form">
          <SurfacesRow
            surfaces={status.defaults.surfaces}
            value={f.surfaces}
            onChange={f.setSurfaces}
          />
          <LaunchLimits
            limits={status.defaults}
            modelsNote={MODELS_NOTE}
            models={[]}
            onModels={() => {}}
            latencyMargin={f.latencyMargin}
            onLatencyMargin={f.setLatencyMargin}
            costMargin={f.costMargin}
            onCostMargin={f.setCostMargin}
            costLimit={f.costLimit}
            onCostLimit={f.setCostLimit}
            budget={f.budget}
            onBudget={f.setBudget}
            minutes={f.minutes}
            onMinutes={f.setMinutes}
            minutesFromSuite
          />
        </Box>
        <Box sx={{ mt: 1.5 }}>
          {runFailure(run.error, status.environmentId)}
          {stop.error ? (
            <Alert severity="error">
              {fixErrorOf(stop.error).message || "Could not discard it."}
            </Alert>
          ) : null}
        </Box>
      </Box>
      <Footer note={launch.error || "Nothing runs until you start it."}>
        <Button
          size="small"
          onClick={() => stop.mutate()}
          disabled={stop.isPending}
          sx={{ color: "text.secondary" }}
        >
          Discard
        </Button>
        <LoadingButton
          size="small"
          variant="contained"
          color="primary"
          disabled={!kept.length || !!launch.error}
          loading={run.isPending}
          onClick={() =>
            run.mutate({ case_ids: kept.map((one) => one.id), ...launch.body })
          }
          data-testid="epoch-run"
        >
          Run Epoch
        </LoadingButton>
      </Footer>
    </>
  );
}
ReviewStep.propTypes = { status: PropTypes.object.isRequired };

const PROGRESS_NOTE = {
  drafting: "Drafting goes on if you close this.",
  blocked: "Each scenario says why it can't run.",
  measuring: "The epoch starts on its own when this run ends.",
};

// Drafting, a blocked draft and the run on the current version: what is happening,
// and the one way out of each.
function JobProgress({ status, onPlan }) {
  const key = epochKey(status);
  const evidence = status.evidenceImport;
  const stop = useStopIssueEpoch(status.jobId);
  const baseline = status.baseline;
  return (
    <>
      <Box sx={{ px: 2, pt: 2, pb: 2 }} data-testid={`epoch-step-${key}`}>
        <Alert
          severity={key === "blocked" ? "warning" : "info"}
          sx={{ mb: 1.5 }}
        >
          {key === "measuring" && baseline
            ? `Ran ${baseline.completed_count} of ${plural(baseline.scenarios, "scenario")} on the current version, ${baseline.failed_count} failing so far. The epoch starts when this run ends.`
            : epochSentence(status)}
        </Alert>
        <GroupLabel>{`Scenarios · ${evidence.cases.length}`}</GroupLabel>
        <CaseList evidence={evidence} cases={evidence.cases} />
        {stop.error ? (
          <Alert severity="error" sx={{ mt: 1.5 }}>
            {fixErrorOf(stop.error).message || "Could not stop it."}
          </Alert>
        ) : null}
      </Box>
      <Footer note={PROGRESS_NOTE[key]}>
        {key === "blocked" ? (
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={onPlan}
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
      </Footer>
    </>
  );
}
JobProgress.propTypes = {
  status: PropTypes.object.isRequired,
  onPlan: PropTypes.func.isRequired,
};

// The job ended before any epoch, with the drawer open: say how, and plan again
// only on a click.
function Ended({ status, onPlan }) {
  return (
    <Box sx={{ px: 2, pt: 2, pb: 2 }} data-testid="epoch-ended">
      <StepIntro title={epochLabel(status)}>{epochSentence(status)}</StepIntro>
      <Button size="small" variant="contained" color="primary" onClick={onPlan}>
        Run Epoch again
      </Button>
    </Box>
  );
}
Ended.propTypes = {
  status: PropTypes.object.isRequired,
  onPlan: PropTypes.func.isRequired,
};

const TITLES = {
  drafting: "Drafting scenarios",
  review: "Review the drafted scenarios",
  blocked: "None of the drafts can run",
  measuring: "Measuring the current version",
};

// A new plan only follows a click: opening Run Epoch on an issue whose job ended (or
// has none), Run Epoch again, or Plan again. It is for the job the click was on.
const planningFor = (status) => {
  const key = epochKey(status);
  return PRE_LAUNCH[key] || isEpochLive(status)
    ? null
    : status?.jobId ?? NO_JOB;
};

// Mounted while the drawer is open, so what it opened on decides the first view.
function DrawerBody({ status, clusterId, projectId, issueTitle, onClose }) {
  const [planFor, setPlanFor] = useState(() => planningFor(status));
  const jobId = status?.jobId ?? NO_JOB;
  const key = epochKey(status);
  const planning = planFor === jobId;
  const plan = () => setPlanFor(jobId);
  // Once the job launched its epoch, the epoch's own page takes over: the drawer
  // closes and a toast links there. It never navigates on its own, since the
  // launch follows a background read, not a click. A job that ended before any
  // epoch keeps its ending here, with Run Epoch again.
  const handoff = !planning && !!status?.epoch;
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  const router = useRouter();
  const handedOff = useRef(null);
  useEffect(() => {
    if (!handoff || handedOff.current === status.epoch.id) return;
    const fix = status.epoch;
    handedOff.current = fix.id;
    const live = isEpochLive(status);
    enqueueSnackbar(live ? `${fixName(fix)} started.` : epochSentence(status), {
      variant: live ? "success" : "info",
      action: (id) => (
        <Button
          size="small"
          color="inherit"
          onClick={() => {
            closeSnackbar(id);
            router.push(epochHref(fix));
          }}
        >
          {`View ${fixName(fix)}`}
        </Button>
      ),
    });
    onClose();
  }, [handoff, status, enqueueSnackbar, closeSnackbar, router, onClose]);
  let body;
  if (handoff) {
    body = null;
  } else if (planning) {
    body = <NewPlan clusterId={clusterId} projectId={projectId} />;
  } else if (PRE_LAUNCH[key]) {
    // The job's own read carries its drafts and defaults; the list's summary doesn't.
    if (!status.evidenceImport || !status.defaults) {
      body = (
        <Box sx={{ px: 2, pt: 2 }}>
          <ContentSkeleton variant="rows" rows={3} />
        </Box>
      );
    } else if (key === "review") {
      body = <ReviewStep key={status.jobId} status={status} />;
    } else {
      body = <JobProgress key={status.jobId} status={status} onPlan={plan} />;
    }
  } else {
    body = <Ended status={status} onPlan={plan} />;
  }
  return (
    <Box
      sx={{ display: "flex", flexDirection: "column", minHeight: "100%" }}
      data-testid="run-epoch-drawer"
    >
      <Box
        sx={{
          px: 2,
          pt: 2,
          pb: 1.5,
          pr: 6,
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>
          {(!planning && TITLES[key]) || "Run Epoch"}
        </Typography>
        {issueTitle ? (
          <Typography
            noWrap
            sx={{ typography: "s2", color: "text.tertiary", mt: 0.25 }}
          >
            {issueTitle}
          </Typography>
        ) : null}
      </Box>
      {body}
    </Box>
  );
}
DrawerBody.propTypes = {
  status: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  clusterId: PropTypes.string.isRequired,
  projectId: PropTypes.string.isRequired,
  issueTitle: PropTypes.string,
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
  const { status, known } = useIssueEpoch({ clusterId, projectId });
  if (!allowed || !known || !projectId) return null;
  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      <DrawerBody
        status={status}
        clusterId={clusterId}
        projectId={projectId}
        issueTitle={issueTitle}
        onClose={onClose}
      />
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
