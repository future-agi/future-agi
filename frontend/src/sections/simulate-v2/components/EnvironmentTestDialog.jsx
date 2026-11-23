import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  DialogActions,
  DialogContent,
  DialogTitle,
  InputBase,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { SimStoreProvider, useConnectedAgents } from "../store";
import { observeEnvsOf } from "../_mock/runningNow";
import { observeImportHref, selectionNoun } from "../_mock/observeSelection";
import { projectLinkOf } from "../_mock/projectLinks";
import { agentIdOf } from "../_mock/agentRegistry";
import { TextLink } from "../agents/connect/connectBits";
import YourAgentsList from "../agents/connect/YourAgentsList";
import ClosableDialog from "./ClosableDialog";

const SEARCH_FROM = 6;

/*
  Observe's "Create environment test", for any project — traces, sessions or
  a voice project's calls. With one environment testing the project's agent
  it goes straight to that environment's review of imported evidence. With
  several it asks which. With none it says what's missing — an agent linked
  to the project, or an environment for that agent — and gets you there.
*/
function EnvironmentTestInner({
  open,
  onClose,
  project,
  projectName,
  selection,
}) {
  const navigate = useNavigate();
  const { agents, state } = useConnectedAgents();
  const ready = !!state?.hydrated;
  const targets = useMemo(
    () =>
      observeEnvsOf(state).filter(
        (t) => String(t.projectId) === String(project),
      ),
    [state, project],
  );
  const link = projectLinkOf(project, state, agents);
  const [picked, setPicked] = useState(null);
  const [query, setQuery] = useState("");
  const [linkedNow, setLinkedNow] = useState(false);

  const n = selection?.all
    ? selection.total ?? selection.ids?.length ?? 0
    : selection?.ids?.length || 0;
  const noun = selectionNoun(selection?.kind || "traces", n);
  const count = `${selection?.all && selection.total == null ? "All" : n} ${noun}`;
  const name = projectName || "This project";

  const go = (t) => {
    onClose();
    navigate(
      observeImportHref(t.envId, {
        ...selection,
        project: t.projectName || projectName || null,
      }),
    );
  };

  /* One environment: straight to its review, no question asked. */
  const tried = useRef(false);
  useEffect(() => {
    if (!open) {
      tried.current = false;
      setPicked(null);
      setQuery("");
      setLinkedNow(false);
      return;
    }
    if (!ready || tried.current) return;
    tried.current = true;
    if (targets.length === 1) go(targets[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, ready]);

  const envAgentName = (envId) => {
    const a = state?.byEnv?.[envId]?.agent;
    return a?.values?.name || a?.name || null;
  };
  const q = query.trim().toLowerCase();
  const shown = q
    ? targets.filter((t) =>
        `${t.envName} ${envAgentName(t.envId) || ""}`.toLowerCase().includes(q),
      )
    : targets;
  const chosen =
    targets.find((t) => t.envId === picked) ||
    (targets.length === 1 ? targets[0] : null);

  /* Nothing to ask while the store loads, or when the one environment is
     already being opened. */
  if (!open || !ready || (targets.length === 1 && !linkedNow)) return null;

  const view = targets.length
    ? "pick"
    : link.status === "linked"
      ? "no-env"
      : "no-agent";

  const connectNew = () => {
    const back = `${window.location.pathname}${window.location.search}`;
    onClose();
    navigate(
      `${paths.dashboard.agents}/connect?${new URLSearchParams({
        project,
        projectName: projectName || "",
        return: back,
      })}`,
    );
  };

  return (
    <ClosableDialog
      open
      onClose={onClose}
      maxWidth={false}
      fullWidth
      PaperProps={{ sx: { maxWidth: view === "no-agent" ? 720 : 560 } }}
    >
      <DialogTitle sx={{ typography: "m2", fontWeight: 600 }}>
        Create environment test
      </DialogTitle>

      {view === "pick" && (
        <DialogContent sx={{ pb: 1 }}>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {`${count} from ${name} open for review in the environment you pick.`}
          </Typography>
          {targets.length >= SEARCH_FROM && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                mt: 1.5,
                px: 1.25,
                height: 32,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              <Iconify
                icon="solar:magnifer-linear"
                width={14}
                sx={{ color: "text.disabled", flexShrink: 0 }}
              />
              <InputBase
                fullWidth
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={`Search ${targets.length} environments`}
                inputProps={{ "aria-label": "Search environments" }}
                sx={{
                  typography: "s2",
                  "& input::placeholder": {
                    color: "text.disabled",
                    opacity: 1,
                  },
                }}
              />
            </Stack>
          )}
          <Box
            role="listbox"
            aria-label="Environments"
            sx={{
              mt: 1.5,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              overflow: "hidden",
            }}
          >
            {shown.map((t, i) => {
              const on = chosen?.envId === t.envId;
              const agent = envAgentName(t.envId);
              return (
                <Stack
                  key={t.envId}
                  role="option"
                  aria-selected={on}
                  direction="row"
                  alignItems="center"
                  spacing={1.25}
                  onClick={() => setPicked(t.envId)}
                  sx={{
                    px: 1.25,
                    minHeight: 44,
                    cursor: "pointer",
                    borderTop: i ? "1px solid" : "none",
                    borderColor: "divider",
                    bgcolor: on ? "action.selected" : "transparent",
                    "&:hover": {
                      bgcolor: on ? "action.selected" : "action.hover",
                    },
                  }}
                >
                  <Iconify
                    icon="solar:globus-linear"
                    width={16}
                    sx={{ color: "text.secondary", flexShrink: 0 }}
                  />
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography
                      noWrap
                      sx={{ typography: "s2", fontWeight: 500 }}
                    >
                      {t.envName}
                    </Typography>
                    {agent && (
                      <Typography
                        noWrap
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {`Tests ${agent}`}
                      </Typography>
                    )}
                  </Box>
                  <Iconify
                    icon={
                      on ? "solar:check-circle-bold" : "solar:circle-linear"
                    }
                    width={16}
                    sx={{
                      color: on ? "text.primary" : "text.disabled",
                      flexShrink: 0,
                    }}
                  />
                </Stack>
              );
            })}
            {!shown.length && (
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.tertiary",
                  px: 1.25,
                  py: 1.25,
                }}
              >
                No environment matches.
              </Typography>
            )}
          </Box>
        </DialogContent>
      )}

      {view === "no-env" && (
        <DialogContent sx={{ pb: 1 }}>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {`${link.agent?.name || "The linked agent"} has no environment yet. Build one from the agent, then send these ${noun} to it.`}
          </Typography>
        </DialogContent>
      )}

      {view === "no-agent" && (
        <>
          <DialogContent sx={{ pb: 0 }}>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {`${name} isn't linked to an agent yet. Link the agent that sends its ${selectionNoun(selection?.kind || "traces", 2)}; they're tested in its environment.`}
            </Typography>
          </DialogContent>
          <Box sx={{ mx: 0.5 }}>
            <YourAgentsList
              project={project}
              projectName={projectName}
              onLinked={() => setLinkedNow(true)}
            />
          </Box>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ px: 3, pt: 1.5 }}
          >
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Not in Agents yet?
            </Typography>
            <TextLink onClick={connectNew}>Connect it</TextLink>
          </Stack>
        </>
      )}

      <DialogActions sx={{ px: 3, pb: 2, pt: 2 }}>
        <Button onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        {view === "pick" && (
          <Button
            variant="contained"
            color="primary"
            disabled={!chosen}
            onClick={() => go(chosen)}
          >
            {`Review ${count}`}
          </Button>
        )}
        {view === "no-env" && link.agentKey && (
          <Button
            variant="contained"
            color="primary"
            onClick={() => {
              onClose();
              navigate(
                `${paths.dashboard.agents}/connected/${agentIdOf(link.agentKey)}`,
              );
            }}
          >
            Open agent
          </Button>
        )}
      </DialogActions>
    </ClosableDialog>
  );
}
EnvironmentTestInner.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  project: PropTypes.string,
  projectName: PropTypes.string,
  selection: PropTypes.shape({
    kind: PropTypes.oneOf(["traces", "sessions", "calls"]),
    ids: PropTypes.array,
    all: PropTypes.bool,
    total: PropTypes.number,
  }),
};

/** Usable from Observe — it brings the simulation store with it. */
export default function EnvironmentTestDialog(props) {
  if (!props.open || !props.project) return null;
  return (
    <SimStoreProvider>
      <EnvironmentTestInner {...props} />
    </SimStoreProvider>
  );
}
EnvironmentTestDialog.propTypes = {
  open: PropTypes.bool,
  project: PropTypes.string,
};
