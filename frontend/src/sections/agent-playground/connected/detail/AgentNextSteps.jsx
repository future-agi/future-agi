import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import {
  Autocomplete,
  Box,
  Button,
  Drawer,
  IconButton,
  Link,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import {
  useAgentEnvironments,
  useLinkObserveProject,
  useObserveSetupCode,
  useSetObservability,
} from "src/api/agents/agentDetail";
import { fetchAllObserveProjects } from "src/api/project/observe-project-list";
import { useAuthContext } from "src/auth/hooks";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { paths } from "src/routes/paths";
import InstructionCodeCopy from "src/sections/project/NewProject/InstructionCodeCopy";
import InstructionTitle from "src/sections/project/NewProject/InstructionTitle";
import ObserveInstruments from "src/sections/project/NewProject/ObserveInstuments";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { sourceLabel } from "../connectedAgentLabels";
import { agentDetailPath } from "./agentDetailTabs";
import { LineSkeleton } from "./bits";

const LANGUAGES = [
  { label: "Python", value: "python", key: "Python" },
  { label: "TypeScript", value: "typescript", key: "TypeScript" },
];

export const observeProjectPath = (projectId) =>
  `${paths.dashboard.observe}/${projectId}/llm-tracing`;

const snippetOf = (code) =>
  typeof code === "string"
    ? code.replace(/^\n+/, "").replace(/\n+$/, "")
    : "Code not available";

// The Observe drawer's sections: install, keys, telemetry and the
// instrumentation list, all in the language picked once at the top.
function SetupCode() {
  const [language, setLanguage] = useState("python");
  const { data, isLoading, isSuccess, error } = useObserveSetupCode();
  const key = LANGUAGES.find((l) => l.value === language).key;
  const code = (section) => snippetOf(data?.[section]?.[key]);

  if (isLoading) {
    return (
      <Stack spacing={1.5}>
        <LineSkeleton width={180} data-testid="setup-code-skeleton" />
        <LineSkeleton width="60%" />
        <LineSkeleton width="40%" />
      </Stack>
    );
  }
  if (!isSuccess || !data) {
    return (
      <Typography sx={{ typography: "s2_1", color: "text.secondary" }}>
        {error?.message || "Couldn't load the setup code"}
      </Typography>
    );
  }
  return (
    <Stack spacing={3}>
      <ToggleButtonGroup
        exclusive
        size="small"
        value={language}
        onChange={(_, value) => value && setLanguage(value)}
        aria-label="Language"
        sx={{ alignSelf: "flex-start" }}
      >
        {LANGUAGES.map((l) => (
          <ToggleButton key={l.value} value={l.value} sx={{ px: 1.5 }}>
            {l.label}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
      <Stack spacing={1.5}>
        <InstructionTitle
          title="Install dependencies"
          description="For more instructions, check out our "
          url="https://docs.futureagi.com/docs/observe"
          urltext="Docs"
        />
        <InstructionCodeCopy
          text={code("installation_guide")}
          language={language}
        />
      </Stack>
      <Stack spacing={1.5}>
        <InstructionTitle
          title="Load API keys"
          description="Load your Future AGI API keys."
        />
        <InstructionCodeCopy text={code("keys")} language={language} />
      </Stack>
      <Stack spacing={1.5}>
        <InstructionTitle
          title="Set up telemetry"
          description="Register your app with the project its traces should land in, before any other code runs."
        />
        <InstructionCodeCopy
          text={code("project_add_code")}
          language={language}
        />
      </Stack>
      <Stack spacing={1.5}>
        <InstructionTitle
          title="Set up instrumentation"
          description="Add tracing for the libraries your agent uses."
        />
        <ObserveInstruments
          data={data.instruments}
          isLoading={false}
          isSuccess
          error={null}
          languageTab={language}
          onLanguageChange={setLanguage}
        />
      </Stack>
    </Stack>
  );
}

/**
 * Connect production traces, for a code agent: the setup code, then the
 * Observe project its traces land in. We don't detect traces; linking the
 * project is the user saying they flow. The observability switch, which only
 * exports simulation spans, is left as it is.
 */
export function ConnectTracesDrawer({ agent, open, onClose, canEdit }) {
  const { enqueueSnackbar } = useSnackbar();
  const linked = agent.observeProject;
  const [picked, setPicked] = useState(linked);
  const link = useLinkObserveProject(agent.id);
  const projects = useQuery({
    queryKey: ["project-list-observe"],
    queryFn: ({ signal }) => fetchAllObserveProjects({ signal }),
    retry: false,
    enabled: open,
  });
  const options = useMemo(
    () => (projects.data ?? []).map((p) => ({ id: p.id, name: p.name })),
    [projects.data],
  );

  const close = () => {
    setPicked(linked);
    onClose();
  };
  const onLink = () =>
    link.mutate(
      { projectId: picked.id },
      {
        onSuccess: () => {
          enqueueSnackbar(`Linked to ${picked.name}`, { variant: "success" });
          onClose();
        },
        onError: (e) =>
          enqueueSnackbar(errorMessage(e) || "Couldn't link that project", {
            variant: "error",
          }),
      },
    );

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={close}
      PaperProps={{ sx: { width: { xs: "100%", md: 640 } } }}
      TransitionProps={{ onEnter: () => setPicked(linked) }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 3,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s1_2", fontWeight: 600, flex: 1 }}>
          Connect production traces
        </Typography>
        <IconButton size="small" onClick={close} aria-label="Close">
          <Iconify icon="solar:close-circle-linear" width={20} />
        </IconButton>
      </Stack>
      <Box sx={{ px: 3, py: 3, flex: 1, overflowY: "auto" }}>
        {open && <SetupCode />}
      </Box>
      <Stack
        spacing={1}
        sx={{
          px: 3,
          py: 2,
          borderTop: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
        }}
      >
        <Typography
          component="label"
          htmlFor="production-project"
          sx={{ typography: "s2_1", fontWeight: 500 }}
        >
          Observe project
        </Typography>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Autocomplete
            id="production-project"
            size="small"
            options={options}
            value={picked}
            loading={projects.isLoading}
            disabled={!canEdit}
            onChange={(_, value) => setPicked(value)}
            getOptionLabel={(o) => o.name}
            isOptionEqualToValue={(a, b) => a.id === b?.id}
            noOptionsText={
              projects.isError
                ? "Couldn't load projects"
                : "No Observe projects"
            }
            sx={{ flex: 1 }}
            renderInput={(params) => (
              <TextField {...params} placeholder="Choose a project" />
            )}
          />
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={
              !picked || picked.id === linked?.id || !canEdit || link.isPending
            }
            onClick={onLink}
          >
            Link project
          </Button>
        </Stack>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          Once your app sends traces, pick the project you registered it with.
        </Typography>
      </Stack>
    </Drawer>
  );
}

ConnectTracesDrawer.propTypes = {
  agent: PropTypes.object.isRequired,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  canEdit: PropTypes.bool.isRequired,
};

function PathCell({
  eyebrow,
  title,
  body,
  unlocks,
  done,
  action,
  secondary,
  testId,
}) {
  return (
    <Stack
      spacing={0.5}
      sx={{ px: 2, py: 1.5, minWidth: 0 }}
      data-testid={testId}
    >
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Typography
          sx={{ typography: "s2", fontWeight: 500, color: "text.secondary" }}
        >
          {eyebrow}
        </Typography>
        {done && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.25}
            sx={{ color: "text.secondary" }}
          >
            <Iconify icon="solar:check-circle-linear" width={14} />
            <Typography sx={{ typography: "s2", color: "inherit" }}>
              Done
            </Typography>
          </Stack>
        )}
      </Stack>
      <Typography sx={{ typography: "s2_1", fontWeight: 500 }}>
        {title}
      </Typography>
      <Typography sx={{ typography: "s2_1", color: "text.secondary" }}>
        {body}
      </Typography>
      {unlocks && (
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {unlocks}
        </Typography>
      )}
      {action && (
        <Stack direction="row" spacing={1} sx={{ pt: 1 }}>
          <Tooltip arrow title={action.tooltip || ""}>
            <span>
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                disabled={!!action.disabled}
                onClick={action.onClick}
              >
                {action.label}
              </Button>
            </span>
          </Tooltip>
          {secondary && (
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              disabled={!!secondary.disabled}
              onClick={secondary.onClick}
            >
              {secondary.label}
            </Button>
          )}
        </Stack>
      )}
    </Stack>
  );
}

PathCell.propTypes = {
  eyebrow: PropTypes.string,
  title: PropTypes.node,
  body: PropTypes.node,
  unlocks: PropTypes.string,
  done: PropTypes.bool,
  action: PropTypes.object,
  secondary: PropTypes.object,
  testId: PropTypes.string,
};

const CONFIRM = {
  unlink: {
    title: (name) => `Unlink ${name}?`,
    body: "Next steps stops showing where this agent's production traces land. The project and its traces stay in Observe, and simulation traces are not affected.",
    action: "Unlink",
  },
  turnOff: {
    title: () => "Turn off observability?",
    body: "We stop pulling this agent's calls into Observe. Calls already pulled stay there.",
    action: "Turn off",
  },
};

/**
 * Next steps, from the designer's agent page: get the agent's traces into an
 * Observe project (production) and build an environment (simulation), side
 * by side. A code agent links the project its production traces land in; a
 * hosted agent's calls are pulled into a project made when observability is
 * turned on. Building an environment from here is coming soon.
 */
export default function AgentNextSteps({ agent }) {
  const navigate = useNavigate();
  const { search } = useLocation();
  const { enqueueSnackbar } = useSnackbar();
  const { role } = useAuthContext();
  const canEdit = !!RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role];
  const [connecting, setConnecting] = useState(false);
  const [confirming, setConfirming] = useState(null);
  const link = useLinkObserveProject(agent.id);
  const observability = useSetObservability(agent.id);
  const environments = useAgentEnvironments(agent.id);

  const isCode = agent.agentKind === "source";
  const project = agent.observeProject;
  const tracesDone = !!project;
  const provider = agent.source ?? agent.provider;
  const envs = environments.data ?? [];
  const envDone = agent.environmentCount > 0;
  const busy = link.isPending || observability.isPending;

  const fail = (fallback) => (e) =>
    enqueueSnackbar(errorMessage(e) || fallback, { variant: "error" });
  const confirm = () => {
    const done = () => setConfirming(null);
    if (confirming === "unlink") {
      link.mutate(
        { projectId: null },
        {
          onSuccess: () => {
            enqueueSnackbar(`Unlinked from ${project.name}`, {
              variant: "success",
            });
            done();
          },
          onError: fail("Couldn't unlink the project"),
        },
      );
    } else {
      observability.mutate(
        { enabled: false },
        {
          onSuccess: () => {
            enqueueSnackbar("Observability is off", { variant: "success" });
            done();
          },
          onError: fail("Couldn't turn observability off"),
        },
      );
    }
  };
  const turnOn = () =>
    observability.mutate(
      { enabled: true },
      {
        onSuccess: () =>
          enqueueSnackbar("Observability is on", { variant: "success" }),
        onError: fail("Couldn't turn observability on"),
      },
    );

  const openInObserve = project && (
    <Link
      href={observeProjectPath(project.id)}
      target="_blank"
      rel="noopener"
      underline="hover"
      sx={{ fontWeight: 500 }}
    >
      Open in Observe
    </Link>
  );

  let traces;
  if (tracesDone) {
    traces = {
      title: project.name,
      body: (
        <>
          {isCode
            ? "Observe project · Error Feed watches its real calls"
            : `Observe project · its ${sourceLabel(provider)} calls are pulled in, and Error Feed watches them`}
          {" · "}
          {openInObserve}
        </>
      ),
      action: isCode
        ? {
            label: "Change project",
            disabled: !canEdit || busy,
            onClick: () => setConnecting(true),
          }
        : {
            label: "Turn off",
            disabled: !canEdit || busy,
            onClick: () => setConfirming("turnOff"),
          },
      secondary: isCode
        ? {
            label: "Unlink",
            disabled: !canEdit || busy,
            onClick: () => setConfirming("unlink"),
          }
        : null,
    };
  } else if (isCode) {
    traces = {
      title: "Get its traces",
      body: "Choose the Observe project its production calls land in.",
      unlocks:
        "Error Feed finds issues in real calls, and Fix can repair them.",
      action: {
        label: "Connect traces",
        disabled: !canEdit,
        onClick: () => setConnecting(true),
      },
    };
  } else if (!provider) {
    traces = {
      title: "Enable observability",
      body: "Add a provider to this agent first, so there are calls to pull in.",
      unlocks:
        "Error Feed finds issues in real calls, and Fix can repair them.",
    };
  } else {
    traces = {
      title: "Enable observability",
      body: `Its ${sourceLabel(provider)} calls are pulled into an Observe project made for it.`,
      unlocks:
        "Error Feed finds issues in real calls, and Fix can repair them.",
      action: {
        label: "Turn on observability",
        disabled: !canEdit || busy,
        onClick: turnOn,
      },
    };
  }

  const environment = envDone
    ? {
        title: envs.length
          ? `${envs[0].name}${envs.length > 1 ? ` +${envs.length - 1} more` : ""}`
          : `${agent.environmentCount} environment${agent.environmentCount === 1 ? "" : "s"}`,
        body: "Environment · scenarios run against simulated tools, data and callers",
        action: {
          label: "View environments",
          onClick: () =>
            navigate(`${agentDetailPath(agent.id, "environments")}${search}`),
        },
      }
    : {
        title: "Build an environment",
        body: "Simulated tools, data and callers to run scenarios against.",
        unlocks:
          "Test changes, and let improvements try fixes before they ship.",
        action: {
          label: "Build environment",
          disabled: true,
          tooltip: "Coming soon",
        },
      };

  const copy = confirming && CONFIRM[confirming];

  return (
    <Box
      component="section"
      aria-label="Next steps"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s2_1", fontWeight: 500 }}>
          Next steps
        </Typography>
        {!(tracesDone && envDone) && (
          <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
            Do either first; the other is here when you need it.
          </Typography>
        )}
      </Stack>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
          "& > *": {
            borderColor: "divider",
            borderStyle: "solid",
            borderWidth: 0,
          },
          "& > *:not(:first-of-type)": {
            borderLeftWidth: { md: 1 },
            borderTopWidth: { xs: 1, md: 0 },
          },
        }}
      >
        <PathCell
          eyebrow="In production"
          done={tracesDone}
          testId="next-step-traces"
          {...traces}
        />
        <PathCell
          eyebrow="In simulation"
          done={envDone}
          testId="next-step-environment"
          {...environment}
        />
      </Box>
      {isCode && (
        <ConnectTracesDrawer
          agent={agent}
          open={connecting}
          onClose={() => setConnecting(false)}
          canEdit={canEdit}
        />
      )}
      <ConfirmDialog
        open={!!copy}
        onClose={() => (busy ? null : setConfirming(null))}
        closeDisabled={busy}
        title={copy ? copy.title(project?.name) : ""}
        content={
          <Typography sx={{ typography: "s2_1", color: "text.secondary" }}>
            {copy?.body}
          </Typography>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            disabled={busy}
            onClick={confirm}
          >
            {copy?.action}
          </Button>
        }
      />
    </Box>
  );
}

AgentNextSteps.propTypes = { agent: PropTypes.object.isRequired };
