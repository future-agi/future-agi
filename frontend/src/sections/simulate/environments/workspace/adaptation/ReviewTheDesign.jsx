import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Button,
  ButtonBase,
  Collapse,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import StatusBadge from "src/components/status-badge";
import { paths } from "src/routes/paths";
import {
  approveHarnessAdaptation,
  harnessAdaptationDesignKey,
  useHarnessAdaptationDesign,
} from "src/api/harness/releases";
import { cancelHarnessJob } from "src/api/harness/harness";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import ChatSplitPane from "../../components/ChatSplitPane";
import BuilderConsole from "../../buildEnvironment/console/BuilderConsole";

const plural = (n, word) =>
  `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
const humanized = (id) =>
  id.replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase());

const FROM_WORLD = "From the ready world";
// A case named only by its key reads as words ("a-deleted-policy" → "A deleted policy").
const readable = (title) =>
  /\s/.test(title) ? title : humanized(title.replace(/-/g, "_"));

function BriefRow({ label, children }) {
  return (
    <Stack
      direction="row"
      spacing={2}
      sx={{ px: 1.5, py: 1, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Typography
        sx={{
          typography: "s1",
          color: "text.secondary",
          width: 120,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Box sx={{ typography: "s1", minWidth: 0, flex: 1 }}>{children}</Box>
    </Stack>
  );
}
BriefRow.propTypes = { label: PropTypes.string, children: PropTypes.node };

/* One section of the design: a collapsed row with its count, its lines underneath. */
function PlanRow({ label, count, children }) {
  const [open, setOpen] = useState(false);
  return (
    <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
      <ButtonBase
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
        sx={{
          width: 1,
          justifyContent: "flex-start",
          gap: 1,
          py: 1.25,
          px: 0.5,
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify
          icon={
            open
              ? "solar:alt-arrow-down-linear"
              : "solar:alt-arrow-right-linear"
          }
          width={14}
          sx={{ color: "text.secondary" }}
        />
        <Typography sx={{ typography: "m3" }}>{label}</Typography>
        <Typography sx={{ typography: "m3", color: "text.secondary" }}>
          {count}
        </Typography>
      </ButtonBase>
      <Collapse in={open} unmountOnExit>
        <Stack spacing={0.75} sx={{ pl: 3.5, pr: 0.5, pb: 1.5 }}>
          {children}
        </Stack>
      </Collapse>
    </Box>
  );
}
PlanRow.propTypes = {
  label: PropTypes.string.isRequired,
  count: PropTypes.number.isRequired,
  children: PropTypes.node,
};

/* A line of the design, and where it comes from. */
function PlanLine({ children, source = FROM_WORLD, tone }) {
  return (
    <Stack direction="row" spacing={2} alignItems="baseline">
      <Box sx={{ typography: "s1", flex: 1, minWidth: 0, color: tone }}>
        {children}
      </Box>
      {source && (
        <Typography
          sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
        >
          {source}
        </Typography>
      )}
    </Stack>
  );
}
PlanLine.propTypes = {
  children: PropTypes.node,
  source: PropTypes.string,
  tone: PropTypes.string,
};

const mono = { fontFamily: "monospace" };

function Design({ design, onApprove, approving }) {
  const { world, agent, fit, check } = design;
  const tables = Object.entries(world.records).filter(([, n]) => n > 0);
  const conflicts = design.conflicts.conflicts;
  const conflictOf = Object.fromEntries(
    conflicts.map((one) => [one.rule, one]),
  );
  // Once every case is checked its findings are the truth; until then, the pairing's count.
  const unmeasured = new Set(
    check?.unscorable
      ? check.unscorable.map((one) => one.scenario_key)
      : fit.unmeasured.map((one) => one.key),
  );
  const steps = Object.fromEntries(
    (check?.mapped || []).map((one) => [one.agent_tool, one.steps]),
  );
  const measurable = design.scenarios.length - unmeasured.size;
  const approvable = design.state === "awaiting_approval";
  const tip =
    design.state === "checking"
      ? "Checking the mapping on every case — approve once it's done"
      : "";
  return (
    <Box sx={{ height: 1, overflowY: "auto", px: 3, py: 2.5 }}>
      <Stack direction="row" alignItems="flex-start" spacing={2} sx={{ mb: 2 }}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m1" }}>Review the design</Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {`${plural(tables.length, "record")} · ${plural(world.tools.length, "tool")} · ${plural(world.rules.length, "rule")} · ${plural(design.scenarios.length, "scenario")} — nothing is built until you approve.`}
          </Typography>
        </Box>
        <Tooltip arrow title={tip}>
          <span>
            <Button
              variant="contained"
              color="primary"
              size="small"
              disabled={!approvable || approving}
              onClick={onApprove}
              startIcon={
                <Iconify icon="solar:check-circle-linear" width={16} />
              }
            >
              {design.state === "building" || approving
                ? "Building…"
                : "Approve and build"}
            </Button>
          </span>
        </Tooltip>
      </Stack>

      {design.state === "failed" && (
        <ErrorState
          title="The adaptation failed"
          detail={design.failure?.message}
          sx={{ mb: 2 }}
        />
      )}

      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          mb: 2,
        }}
      >
        <Stack direction="row" sx={{ px: 1.5, py: 1 }}>
          <Typography sx={{ typography: "m3", flex: 1 }}>Brief</Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            What the design is built from
          </Typography>
        </Stack>
        <BriefRow label="Description">
          {world.summary ||
            `${world.title}, with ${agent.name}'s own tools in place of the world's`}
        </BriefRow>
        <BriefRow label="Sources">The ready world</BriefRow>
        <BriefRow label="Channel">
          {world.channel === "voice" ? "Voice" : "Chat"}
        </BriefRow>
        <BriefRow label="Agent">{`${agent.name} · ${agent.version}`}</BriefRow>
      </Box>

      <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
        <PlanRow label="Records" count={tables.length}>
          {tables.map(([table, count]) => (
            <PlanLine key={table}>
              {`${humanized(table)} · ${count.toLocaleString()} seeded`}
            </PlanLine>
          ))}
        </PlanRow>
        <PlanRow label="Tools" count={world.tools.length}>
          {fit.rows.map((row) => (
            <PlanLine key={row.world}>
              <Box component="span" sx={mono}>
                {row.world}
              </Box>
              {" ← "}
              {row.agent ? (
                <Box component="span" sx={mono}>
                  {row.agent}
                  {steps[row.agent] &&
                  steps[row.agent].join(" + ") !== row.world
                    ? ` (${steps[row.agent].join(" + ")})`
                    : ""}
                </Box>
              ) : (
                <Box component="span" sx={{ color: "text.secondary" }}>
                  Not available
                </Box>
              )}
            </PlanLine>
          ))}
        </PlanRow>
        <PlanRow label="Rules" count={world.rules.length}>
          {world.rules.map((rule) => (
            <PlanLine key={rule.id}>
              {rule.text}
              {conflictOf[rule.id] && (
                <Stack
                  direction="row"
                  spacing={0.75}
                  alignItems="flex-start"
                  sx={{ mt: 0.25 }}
                >
                  <Iconify
                    icon="solar:danger-triangle-linear"
                    width={14}
                    sx={{
                      color: "status.warning.fg",
                      flexShrink: 0,
                      mt: "2px",
                    }}
                  />
                  <Typography
                    sx={{ typography: "s1", color: "text.secondary" }}
                  >
                    {`${agent.name}'s instructions say: “${conflictOf[rule.id].agent}”`}
                  </Typography>
                </Stack>
              )}
            </PlanLine>
          ))}
        </PlanRow>
        <PlanRow label="People" count={world.people.length}>
          {world.people.map((name) => (
            <PlanLine key={name}>{name}</PlanLine>
          ))}
        </PlanRow>
        <PlanRow label="Scenarios" count={design.scenarios.length}>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {`${measurable} of ${design.scenarios.length} measurable with ${agent.name}. The rest need a tool it doesn't have; they stay listed, never dropped.`}
          </Typography>
          {design.scenarios.map((one) => (
            <PlanLine
              key={one.key}
              tone={unmeasured.has(one.key) ? "text.secondary" : undefined}
              source={unmeasured.has(one.key) ? "Not measurable" : FROM_WORLD}
            >
              {readable(one.title)}
            </PlanLine>
          ))}
        </PlanRow>
      </Box>
    </Box>
  );
}
Design.propTypes = {
  design: PropTypes.object.isRequired,
  onApprove: PropTypes.func.isRequired,
  approving: PropTypes.bool,
};

/**
 * Adapt to my agent — the design (env-world-adapt-design). It opens at once: what the
 * adaptation takes from the ready world, the pairing chosen in the drawer and the cases it
 * measures. The builder chat stays open on the left for corrections; nothing is built until
 * Approve and build, which is open once the mapping has been checked on every case.
 */
export default function ReviewTheDesign({ jobId, chat }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const designQuery = useHarnessAdaptationDesign(jobId);
  const design = designQuery.data;
  const refresh = () =>
    queryClient.invalidateQueries({
      queryKey: harnessAdaptationDesignKey(jobId),
    });
  const approve = useMutation({
    mutationFn: () => approveHarnessAdaptation(jobId),
    onSuccess: refresh,
    onError: (error) =>
      enqueueSnackbar(errorMessage(error), { variant: "error" }),
  });
  const cancel = useMutation({
    mutationFn: () =>
      cancelHarnessJob(jobId, "Cancelled from Review the design"),
    onSuccess: () => navigate(paths.dashboard.simulate.environments.root),
    onError: (error) =>
      enqueueSnackbar(errorMessage(error), { variant: "error" }),
  });
  const status =
    design?.state === "checking" ? "awaiting_approval" : design?.state;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <IconButton
          size="small"
          aria-label="Back to environments"
          onClick={() => navigate(paths.dashboard.simulate.environments.root)}
        >
          <Iconify icon="solar:alt-arrow-left-linear" width={16} />
        </IconButton>
        <Typography noWrap sx={{ typography: "m1", minWidth: 0 }}>
          {design
            ? `${design.world.title} · ${design.agent.name}`
            : "Review the design"}
        </Typography>
        {status && <StatusBadge status={status} />}
        <Stack
          direction="row"
          spacing={0.5}
          alignItems="center"
          sx={{
            px: 0.75,
            py: 0.25,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 0.5,
          }}
        >
          <Iconify
            icon="solar:copy-linear"
            width={12}
            sx={{ color: "text.secondary" }}
          />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            Runs: Live
          </Typography>
        </Stack>
        <Box flex={1} />
        <Button
          variant="outlined"
          color="inherit"
          size="small"
          disabled={
            !design ||
            ["ready", "failed"].includes(design.state) ||
            cancel.isPending
          }
          onClick={() => cancel.mutate()}
          startIcon={<Iconify icon="solar:stop-circle-linear" width={16} />}
        >
          Cancel
        </Button>
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        <ChatSplitPane
          busy={Boolean(chat.running || chat.inFlight || chat.waiting)}
          chat={({ collapse, open, collapseRef }) => (
            <BuilderConsole
              turns={chat.turns}
              running={chat.running}
              onSend={chat.send}
              onStop={chat.stop}
              canStop={chat.inFlight}
              working={chat.working}
              stopping={chat.stopping}
              frozen={chat.frozen}
              frozenReason={chat.frozenReason}
              answerWithOptions={chat.answerWithOptions}
              onCollapse={collapse}
              collapseRef={collapseRef}
              active={open}
            />
          )}
        >
          {designQuery.isError ? (
            <ErrorState
              title="Couldn't load the design"
              detail={errorMessage(designQuery.error)}
              onRetry={() => designQuery.refetch()}
              sx={{ m: 3 }}
            />
          ) : !design ? (
            <ContentSkeleton
              rows={6}
              label="Loading the design"
              sx={{ m: 3 }}
            />
          ) : (
            <Design
              design={design}
              onApprove={() => approve.mutate()}
              approving={approve.isPending}
            />
          )}
        </ChatSplitPane>
      </Box>
    </Box>
  );
}
ReviewTheDesign.propTypes = {
  jobId: PropTypes.string.isRequired,
  // useWorkspaceChat's return: the builder's conversation on this adaptation.
  chat: PropTypes.object.isRequired,
};
