import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  ButtonBase,
  Menu,
  MenuItem,
  Stack,
  Tab,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import ContentSkeleton from "src/components/content-skeleton";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { harnessIdempotencyKey } from "src/api/harness/harness";
import {
  createHarnessAdaptation,
  useHarnessReleaseFit,
} from "src/api/harness/releases";
import { harnessJobQuery } from "src/api/simulate-environments/environment";
import { useAgentDefinitionVersions } from "src/api/agent-definition/agent-definition-version";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { BTN_SX, INPUT_SX, T12, T13 } from "../drawerStyles";
import { modalityOfWorld } from "../worlds";
import AgentPicker, { useWorldAgents } from "./AgentPicker";
import { StartCard } from "./startBits";
import WorldFitReport from "./WorldFitReport";
import { fitVerdict, pairedRows, pairingOf } from "./worldFit";

const footSx = {
  px: 2,
  py: 1.5,
  borderTop: "1px solid",
  borderColor: "divider",
};

// How many of a list the summary shows before "N more".
const SHOWN = 4;

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function Row({ label, children }) {
  return (
    <Stack
      direction="row"
      spacing={2}
      sx={{
        px: 2,
        py: 1.25,
        borderTop: "1px solid",
        borderColor: "divider",
        "&:first-of-type": { borderTop: "none" },
      }}
    >
      <Typography
        sx={{ ...T13, color: "text.secondary", width: 104, flexShrink: 0 }}
      >
        {label}
      </Typography>
      <Box sx={{ ...T13, minWidth: 0, flex: 1 }}>{children}</Box>
    </Stack>
  );
}
Row.propTypes = { label: PropTypes.string, children: PropTypes.node };

const firstOf = (items, more) =>
  items.length > SHOWN
    ? `${items.slice(0, SHOWN).join(", ")} and ${items.length - SHOWN} more`
    : items.join(", ") || more;

/* World: what the environment is, in one line each, and its sections in full on request. */
function WorldTab({ world, onUse }) {
  const [details, setDetails] = useState(false);
  const tables = Object.entries(world.records).filter(([, n]) => n > 0);
  return (
    <Stack spacing={2}>
      <Box>
        {world.tagline && (
          <Typography sx={{ ...T13 }}>{world.tagline}</Typography>
        )}
        <Typography sx={{ ...T12, color: "text.secondary", mt: 0.5 }}>
          {`Future AGI catalogue · release ${world.digest.slice(7, 19)} · ${world.suite.name}`}
        </Typography>
        {world.exampleRequests[0] && (
          <Typography
            sx={{
              ...T13,
              mt: 1.25,
              pl: 1.5,
              borderLeft: "2px solid",
              borderColor: "divider",
              color: "text.secondary",
            }}
          >
            {`"${world.exampleRequests[0]}"`}
          </Typography>
        )}
      </Box>

      <StartCard title="In this world">
        <Row label="Records">
          {firstOf(
            tables.map(([t, n]) => `${t.replace(/_/g, " ")} ${n}`),
            "None seeded",
          )}
        </Row>
        <Row label="Tools">
          {firstOf(
            world.toolList.map((t) => t.name),
            "None",
          )}
        </Row>
        <Row label="Rules">
          {`${plural(world.rules.length, "rule")} for the agent, each graded`}
        </Row>
        {world.apps.length > 0 && (
          <Row label="Apps">
            {world.apps
              .map((a) => `${a.label} (${plural(a.records, "record")})`)
              .join(" · ")}
          </Row>
        )}
        <Row label="Scenarios">
          {`${world.scenarioCount} in the base suite, each proved on this world`}
        </Row>
      </StartCard>

      <Button
        size="small"
        color="inherit"
        onClick={() => setDetails((d) => !d)}
        endIcon={
          <Iconify
            icon={
              details
                ? "solar:alt-arrow-up-linear"
                : "solar:alt-arrow-down-linear"
            }
            width={14}
          />
        }
        sx={{ ...BTN_SX, alignSelf: "flex-start", color: "text.secondary" }}
      >
        {details ? "Hide details" : "Show details"}
      </Button>

      {details && (
        <Stack spacing={2}>
          <StartCard
            title="Seeded records"
            subtitle="Synthetic. The world never touches production."
          >
            {tables.map(([table, count]) => (
              <Row key={table} label={table.replace(/_/g, " ")}>
                {count.toLocaleString()}
              </Row>
            ))}
          </StartCard>
          <StartCard title="Tools">
            {world.toolList.map((tool) => (
              <Row
                key={tool.name}
                label={tool.mutates_state ? "Changes" : "Reads"}
              >
                <Typography
                  component="span"
                  sx={{ ...T13, fontFamily: "monospace" }}
                >
                  {tool.name}
                </Typography>
                {tool.description && (
                  <Typography sx={{ ...T12, color: "text.secondary" }}>
                    {tool.description}
                  </Typography>
                )}
              </Row>
            ))}
          </StartCard>
          <StartCard title="Rules the agent is graded on">
            {world.rules.map((rule) => (
              <Row key={rule.id} label={rule.id.replace(/_/g, " ")}>
                {rule.text}
              </Row>
            ))}
          </StartCard>
          {world.apps.map((app) => (
            <StartCard
              key={app.capability}
              title={app.label}
              subtitle={app.server}
            >
              <Row label="Tools">{app.tools.join(", ")}</Row>
              <Row label="Records">{app.records.toLocaleString()}</Row>
            </StartCard>
          ))}
        </Stack>
      )}

      <Stack direction="row" justifyContent="flex-end" sx={{ pt: 0.5 }}>
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={onUse}
          endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
          sx={BTN_SX}
        >
          Use with my agent
        </Button>
      </Stack>
    </Stack>
  );
}
WorldTab.propTypes = {
  world: PropTypes.object.isRequired,
  onUse: PropTypes.func.isRequired,
};

/* The version adapted: the agent's active (production) version, else its latest. */
function useAdaptedVersion(agentId) {
  const versions = useAgentDefinitionVersions({ selectedAgentId: agentId });
  return useMemo(() => {
    const rows = (versions.data?.pages || []).flatMap((p) => p?.results || []);
    return {
      version:
        rows.find((v) => v.is_active || v.status === "active") ||
        rows[0] ||
        null,
      isLoading: Boolean(agentId) && versions.isLoading,
      error: versions.error,
      refetch: versions.refetch,
    };
  }, [
    agentId,
    versions.data,
    versions.isLoading,
    versions.error,
    versions.refetch,
  ]);
}

/* How the agent fits the suite, and the last fit read for this suite and version: a pairing
   that can't be counted keeps its table. */
function useFit(choice) {
  const query = useHarnessReleaseFit(choice);
  const key = `${choice.suiteId}:${choice.agentVersionId}`;
  const [last, setLast] = useState({ key: null, fit: null });
  useEffect(() => {
    if (query.data) setLast({ key, fit: query.data });
  }, [key, query.data]);
  const kept = last.key === key ? last.fit : null;
  return {
    fit: choice.agentVersionId ? query.data || kept : null,
    // False while the fit shown counts another pairing than the one asked for.
    counted: Boolean(query.data && !query.isPlaceholderData),
    isFetching: Boolean(query.isFetching),
    error: query.isError ? query.error : null,
    retry: query.refetch,
  };
}

const suiteLabel = (suite) =>
  suite.name.endsWith("_base") ? "Base suite" : suite.name;

// What running the measurable cases waits for, in the order the adaptation does it.
const PREPARING = [
  { key: "reading", label: "Reading your agent" },
  {
    key: "mapping",
    label: "Mapping arguments and checking them on every case",
  },
  { key: "starting", label: "Starting the run" },
];
const STEP_OF_STATE = { queued: 0, provisioning: 0, running: 1 };

// A run starts just after its adaptation completes; how long it is waited for, and how often.
const RUN_WAIT_MS = 30000;
const RUN_POLL_MS = 2000;
const ENDED = ["failed", "canceled"];

/* Run the measurable cases: the adaptation is drafted and checked without a session, then its
   run opens. The environment page is never shown. */
function Preparing({ jobId, onFailed, onClose }) {
  const navigate = useNavigate();
  const jobQuery = harnessJobQuery(jobId);
  const { data, error, isError, refetch } = useQuery({
    ...jobQuery,
    // The poll stops when the job ends; a completed one is read on until its run shows.
    refetchInterval: (query) =>
      jobQuery.refetchInterval?.(query) ||
      (query.state.data?.status?.state === "completed" ? RUN_POLL_MS : false),
  });
  const result = data?.job?.metadata?.adaptation_result;
  const status = data?.status;
  const completed = status?.state === "completed";
  const run = result?.run;
  const refused = result?.compose?.message;
  const compositionId = result?.composition_id;
  const failure = status?.failure?.message;
  const ended = ENDED.includes(status?.state);
  const current = STEP_OF_STATE[status?.state] ?? (result ? 2 : 0);
  useEffect(() => {
    if (run) {
      navigate(
        paths.dashboard.simulate.environments.execution(
          compositionId,
          run.run_test_id,
          run.test_execution_id,
        ),
      );
    } else if (ended) {
      onFailed(failure || "Preparing your run failed");
    } else if (completed && refused) {
      onFailed(`Your run can't start: ${refused}`);
    }
  }, [
    run,
    ended,
    failure,
    completed,
    refused,
    compositionId,
    navigate,
    onFailed,
  ]);
  const overdue = completed && !run && !refused;
  useEffect(() => {
    if (!overdue) return undefined;
    const timer = setTimeout(
      () =>
        onFailed(
          compositionId
            ? "Your environment is ready, but its run didn't start. Open it to run the cases."
            : "Preparing finished without a run. Try again.",
          compositionId,
        ),
      RUN_WAIT_MS,
    );
    return () => clearTimeout(timer);
  }, [overdue, compositionId, onFailed]);
  return (
    <Stack spacing={0.75}>
      <Stack spacing={0.75} role="status" aria-label="Preparing your run">
        <Typography sx={{ ...T13, fontWeight: 600 }}>
          Preparing your run
        </Typography>
        {PREPARING.map((step, index) => (
          <Stack key={step.key} direction="row" spacing={1} alignItems="center">
            <Iconify
              icon={
                index < current
                  ? "solar:check-circle-linear"
                  : index === current
                    ? "solar:refresh-circle-linear"
                    : "solar:record-circle-linear"
              }
              width={16}
              sx={{
                color: index < current ? "status.success.fg" : "text.tertiary",
              }}
            />
            <Typography
              sx={{
                ...T12,
                color: index === current ? "text.primary" : "text.secondary",
              }}
            >
              {step.label}
            </Typography>
          </Stack>
        ))}
      </Stack>
      {isError && (
        <Alert
          severity="error"
          sx={{ ...T12 }}
          action={
            <Button size="small" color="inherit" onClick={() => refetch()}>
              Retry
            </Button>
          }
        >
          {`Couldn't read how your run is going: ${errorMessage(error)}`}
        </Alert>
      )}
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography sx={{ ...T12, color: "text.secondary" }}>
          This keeps going if you close it. You can find it in My environments.
        </Typography>
        <Button size="small" color="inherit" onClick={onClose} sx={BTN_SX}>
          Close
        </Button>
      </Stack>
    </Stack>
  );
}
Preparing.propTypes = {
  jobId: PropTypes.string.isRequired,
  // (message, the environment it made if any)
  onFailed: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};

/*
  Use with my agent (env-world-use-agent, env-world-agent-picked, env-world-no-match): pick a
  connected agent and see how it fits, the world's tools paired with its own and the cases that
  pairing measures. Run the measurable cases straight away, or Adapt to my agent: its design
  opens at once, and nothing is built until it's approved.
*/
function UseTab({ world, onBusyChange, onOtherWorld }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const modality = modalityOfWorld(world);
  const { agents, isLoading } = useWorldAgents(modality);
  const [agentId, setAgentId] = useState(null);
  const agent = agents.find((a) => a.id === agentId) || null;
  const {
    version,
    isLoading: versionLoading,
    error: versionsError,
    refetch: refetchVersions,
  } = useAdaptedVersion(agent?.sample ? null : agentId);
  const suites = world.release?.suites?.length
    ? world.release.suites
    : [world.suite];
  const [suiteId, setSuiteId] = useState(world.suite.id);
  const suite = suites.find((one) => one.id === suiteId) || suites[0];
  const [suiteAnchor, setSuiteAnchor] = useState(null);
  // The pairing the person changed for this version (null: the suggestion).
  const [edited, setEdited] = useState({ versionId: null, pairing: null });
  const versionId = agent && !agent.sample ? version?.id || null : null;
  const pairing = edited.versionId === versionId ? edited.pairing : null;
  const fitQuery = useFit({
    suiteId: suite.id,
    agentVersionId: versionId,
    pairing,
  });
  // The rows show the person's pairing at once; the count follows it.
  const fit = fitQuery.fit && {
    ...fitQuery.fit,
    rows: pairedRows(fitQuery.fit, pairing),
  };
  // The run being prepared.
  const [job, setJob] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  // { message, compositionId }: what went wrong, and the environment it still made.
  const [error, setError] = useState(null);
  // One key per choice, so a retried click cannot start a second adaptation. An adaptation that
  // ended without its run, or a key already spent, takes a new one.
  const [attempt, setAttempt] = useState(0);
  const keys = useMemo(
    () => ({ review: harnessIdempotencyKey(), run: harnessIdempotencyKey() }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [versionId, suiteId, pairing, attempt],
  );
  // Leaving the tab never leaves the drawer locked.
  const reportBusy = useRef(onBusyChange);
  useEffect(() => {
    reportBusy.current = onBusyChange;
  }, [onBusyChange]);
  useEffect(() => () => reportBusy.current?.(false), []);

  const choose = (id) => {
    setAgentId(id);
    setEdited({ versionId: null, pairing: null });
    setError(null);
  };
  const pair = (worldTool, agentTool) =>
    setEdited((was) => {
      const before =
        (was.versionId === versionId && was.pairing) || pairingOf(fit.rows);
      // The agent tool leaves its old pair; the world tool takes it.
      const next = Object.fromEntries(
        Object.entries(before).map(([world, agent]) => [
          world,
          agent === agentTool ? null : agent,
        ]),
      );
      next[worldTool] = agentTool;
      return { versionId, pairing: next };
    });
  const stopped = useCallback(
    (failure) => {
      setJob(null);
      setSubmitting(false);
      setError(failure);
      onBusyChange?.(false);
    },
    [onBusyChange],
  );
  const runFailed = useCallback(
    (message, compositionId = null) => {
      setAttempt((n) => n + 1);
      stopped({ message, compositionId });
    },
    [stopped],
  );

  const start = async (mode) => {
    setSubmitting(true);
    onBusyChange?.(true);
    setError(null);
    try {
      const created = await createHarnessAdaptation({
        suite_id: suite.id,
        agent_version_id: version.id,
        name: `${world.name} · ${agent.name}`,
        idempotency_key: keys[mode],
        pairing: pairingOf(fit.rows),
        mode,
      });
      queryClient.invalidateQueries({ queryKey: ["harness-jobs"] });
      if (mode === "review") {
        onBusyChange?.(false);
        navigate(
          paths.dashboard.simulate.environments.detail(created.job.job_id),
        );
      } else {
        setJob({ id: created.job.job_id });
      }
    } catch (requestError) {
      if (requestError?.error === "idempotency_conflict")
        setAttempt((n) => n + 1);
      stopped({ message: errorMessage(requestError) });
    }
  };

  const { verdict, none, measurable } =
    fit && fitQuery.counted ? fitVerdict(fit) : {};
  const blocked = !agent
    ? null
    : agent.sample
      ? {
          text: "A sample agent can't be used. Connect your agent from Build environment's Connect your agent, or from Agents",
        }
      : versionsError && !version
        ? {
            error: `Couldn't read the agent's versions: ${errorMessage(versionsError)}`,
            retry: refetchVersions,
          }
        : versionLoading
          ? { loading: "Reading the agent's versions" }
          : !version
            ? { text: "This agent has no version to use yet" }
            : fitQuery.error
              ? { error: errorMessage(fitQuery.error), retry: fitQuery.retry }
              : !fit
                ? { loading: "Reading your agent's tools" }
                : null;
  const waiting =
    !fit ||
    !fitQuery.counted ||
    fitQuery.isFetching ||
    Boolean(blocked) ||
    submitting;
  const runTip = none
    ? "Nothing can be measured yet — pair a world tool, or choose another world"
    : "";

  return (
    <StartCard title="Use it with your agent">
      <Stack sx={{ p: 2 }} spacing={2}>
        <AgentPicker
          agents={agents}
          isLoading={isLoading}
          value={agentId}
          onChange={choose}
          modality={modality}
          disabled={submitting}
          subtitle={`Your ${modality === "voice" ? "voice" : "chat"} agents — this world is for ${modality === "voice" ? "voice" : "chat"} agents.`}
        />
        {fit && (
          <WorldFitReport
            agentName={agent.name}
            fit={fit}
            counting={!fitQuery.counted}
            disabled={submitting}
            onPair={pair}
            onOtherWorld={onOtherWorld}
          />
        )}
        {blocked?.text && (
          <Typography sx={{ ...T12, color: "text.secondary" }}>
            {blocked.text}
          </Typography>
        )}
        {blocked?.loading && (
          <ContentSkeleton rows={3} label={blocked.loading} />
        )}
        {blocked?.error && (
          <Alert
            severity="error"
            sx={{ ...T12 }}
            action={
              <Button
                size="small"
                color="inherit"
                onClick={() => blocked.retry()}
              >
                Retry
              </Button>
            }
          >
            {blocked.error}
          </Alert>
        )}
        <Stack
          spacing={0.75}
          sx={{ pt: 1.5, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Typography sx={{ ...T12, color: "text.tertiary" }}>
            Review before you adopt
          </Typography>
          <Line
            label="Copied"
            value={`${world.recordCount.toLocaleString()} seeded rows · ${plural(suite.scenario_count, "scenario")} · release ${world.releaseName}`}
          />
          <Line
            label="Visibility"
            value="Private to this workspace — the release stays unchanged"
          />
        </Stack>
        {error && (
          <Alert
            severity={error.compositionId ? "warning" : "error"}
            sx={{ ...T12 }}
            action={
              error.compositionId && (
                <Button
                  size="small"
                  color="inherit"
                  onClick={() =>
                    navigate(
                      paths.dashboard.simulate.environments.detail(
                        error.compositionId,
                      ),
                    )
                  }
                >
                  Open environment
                </Button>
              )
            }
          >
            {error.message}
          </Alert>
        )}
        {job && (
          <Preparing
            jobId={job.id}
            onFailed={runFailed}
            onClose={() => stopped(null)}
          />
        )}
      </Stack>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={footSx}>
        <Typography sx={{ ...T12, color: "text.secondary" }}>
          <Box component="span" sx={{ color: "text.primary", fontWeight: 500 }}>
            {plural(suite.scenario_count, "scenario")}
          </Box>
          {` · ${suiteLabel(suite)} · `}
          <ButtonBase
            disabled={submitting}
            onClick={(event) => setSuiteAnchor(event.currentTarget)}
            sx={{
              ...T12,
              fontWeight: 500,
              color: "text.primary",
              textDecoration: "underline",
              textUnderlineOffset: 3,
              verticalAlign: "baseline",
            }}
          >
            Change
          </ButtonBase>
        </Typography>
        <Menu
          anchorEl={suiteAnchor}
          open={Boolean(suiteAnchor)}
          onClose={() => setSuiteAnchor(null)}
        >
          {suites.map((one) => (
            <MenuItem
              key={one.id}
              selected={one.id === suite.id}
              onClick={() => {
                setSuiteId(one.id);
                setSuiteAnchor(null);
              }}
              sx={{ ...T13, gap: 2 }}
            >
              <Box component="span" sx={{ flex: 1 }}>
                {suiteLabel(one)}
              </Box>
              <Box
                component="span"
                sx={{
                  color: "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {one.scenario_count}
              </Box>
            </MenuItem>
          ))}
        </Menu>
        <Box flex={1} />
        <Button
          variant="outlined"
          size="small"
          color="inherit"
          disabled={waiting}
          onClick={() => start("review")}
          sx={{ ...BTN_SX, whiteSpace: "nowrap" }}
        >
          Adapt to my agent
        </Button>
        <Tooltip arrow title={runTip}>
          <span>
            <Button
              variant="contained"
              size="small"
              color="primary"
              disabled={waiting || none}
              onClick={() => start("run")}
              startIcon={<Iconify icon="solar:play-bold" width={14} />}
              sx={{ ...BTN_SX, whiteSpace: "nowrap" }}
            >
              {verdict === "partial"
                ? `Run the ${plural(measurable, "measurable case")}`
                : "Run the same cases"}
            </Button>
          </span>
        </Tooltip>
      </Stack>
    </StartCard>
  );
}
UseTab.propTypes = {
  world: PropTypes.object.isRequired,
  onBusyChange: PropTypes.func,
  onOtherWorld: PropTypes.func,
};

function Line({ label, value }) {
  return (
    <Stack direction="row" spacing={2}>
      <Typography
        sx={{ ...T12, color: "text.secondary", width: 96, flexShrink: 0 }}
      >
        {label}
      </Typography>
      <Typography sx={{ ...T12, fontWeight: 600, color: "text.primary" }}>
        {value}
      </Typography>
    </Stack>
  );
}
Line.propTypes = { label: PropTypes.string, value: PropTypes.node };

/* Customize: a private copy built from this world with the change asked for. */
function CustomizeTab({ world }) {
  const [change, setChange] = useState("");
  return (
    <StartCard
      title="Customize a private copy"
      subtitle="Your own editable copy. Its scores won't compare with this release."
      action={<UiOnlyFlag feature="customizing a ready environment" />}
    >
      <Stack sx={{ p: 2 }} spacing={1.5}>
        <TextField
          multiline
          minRows={3}
          fullWidth
          size="small"
          value={change}
          onChange={(e) => setChange(e.target.value)}
          placeholder={`What should change in ${world.name}? For example: refunds over $500 need a supervisor.`}
          sx={INPUT_SX}
        />
        <Typography sx={{ ...T12, color: "text.secondary" }}>
          {
            "The builder starts from this world's records, tools and rules, adds your change, and shows the design for you to approve before it builds."
          }
        </Typography>
      </Stack>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={footSx}>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled
          startIcon={<Iconify icon="solar:pen-new-square-linear" width={14} />}
          sx={BTN_SX}
        >
          Build my copy
        </Button>
        <Typography sx={{ ...T12, color: "text.secondary" }}>
          Building a copy from a ready environment isn&apos;t connected yet.
        </Typography>
      </Stack>
    </StartCard>
  );
}
CustomizeTab.propTypes = { world: PropTypes.object.isRequired };

/**
 * A ready environment, opened from its card: World, Use with my agent and Customize.
 * RL access comes with locked releases and splits (plan T30).
 */
export default function ReadyEnvironmentPanel({
  item,
  onBusyChange,
  onOtherWorld,
}) {
  const { world } = item;
  const [tab, setTab] = useState(item.initialTab || "world");
  const [busy, setBusy] = useState(false);
  const busyChanged = useCallback(
    (next) => {
      setBusy(next);
      onBusyChange?.(next);
    },
    [onBusyChange],
  );
  return (
    <Stack spacing={2} sx={{ pb: 2.5 }}>
      <Tabs value={tab} onChange={(_, v) => setTab(v)}>
        <Tab value="world" label="World" disabled={busy} />
        <Tab value="start" label="Use with my agent" disabled={busy} />
        <Tab value="customize" label="Customize" disabled={busy} />
      </Tabs>
      {tab === "world" && (
        <WorldTab world={world} onUse={() => setTab("start")} />
      )}
      {tab === "start" && (
        <UseTab
          world={world}
          onBusyChange={busyChanged}
          onOtherWorld={onOtherWorld}
        />
      )}
      {tab === "customize" && <CustomizeTab world={world} />}
    </Stack>
  );
}
ReadyEnvironmentPanel.propTypes = {
  item: PropTypes.object.isRequired,
  onBusyChange: PropTypes.func,
  // Back to the ready environments, to pick another.
  onOtherWorld: PropTypes.func,
};
