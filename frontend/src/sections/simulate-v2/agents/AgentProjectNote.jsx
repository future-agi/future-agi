import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Switch, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useConnectedAgents, useSimStore } from "../store";
import { knownProjects } from "../_mock/projectLinks";
import { observeSlugOf, resolveObserve } from "../_mock/connectAgent";
import {
  lacksProject,
  observabilityOnPatch,
  observeProjectOf,
} from "./observeProject";
import { PROVIDER_LABELS, sourceKindOf } from "../_mock/agentRegistry";
import { ObserveProjectStep } from "./connect/ConnectAboutStep";

/**
 * An agent with no tracing project, about to get an environment: link one
 * first, right here. Agents connected before every connect linked a project
 * are the only ones that get here. One click creates a new project named
 * after the agent (it waits for its first trace — the build doesn't); Pick
 * another links the project it already sends to.
 */
export default function AgentProjectNote({ agent: given }) {
  const { agents, state, setAgentValues } = useConnectedAgents();
  /* The agent as saved now — the caller may hold the copy it was opened with. */
  const agent = agents.find((a) => a.key === given?.key) || given;
  const { dispatch } = useSimStore();
  const [picking, setPicking] = useState(false);
  const [choice, setChoice] = useState(null);
  /* Linked here a moment ago: said in one quiet line, so the note doesn't just vanish. */
  const [linkedHere, setLinkedHere] = useState(null);
  if (!lacksProject(agent, state, agents)) {
    const p = observeProjectOf(agent, state, agents);
    /* A hosted agent's real calls are one switch away — offered where its
       environment is built, since without them scenarios come from the
       agent only. */
    if (
      sourceKindOf(agent) === "platform" &&
      !agent.values?.observability?.on
    ) {
      const provider =
        PROVIDER_LABELS[agent.values?.provider] || "its platform";
      return (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          data-testid="agent-project-pull"
          sx={{
            px: 1.5,
            py: 1.1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          <Iconify
            icon="solar:graph-new-linear"
            width={16}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {`Pull its ${provider} calls into ${p?.name || "its project"}`}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Off: scenarios come from the agent only. On: its real calls come
              in for Error Feed and Add → From production.
            </Typography>
          </Box>
          <Switch
            checked={false}
            onChange={() => {
              setAgentValues(agent.key, observabilityOnPatch(agent, p.project));
              setLinkedHere(p.project);
            }}
            inputProps={{ "aria-label": "Turn on observability" }}
          />
        </Stack>
      );
    }
    if (!linkedHere) return null;
    return (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        data-testid="agent-project-linked"
        sx={{
          px: 1.5,
          py: 1.1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        <Iconify
          icon="solar:check-circle-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>
            Tracing project
          </Box>
          {` · Traces go to ${p?.name || linkedHere}.`}
          {agent.values?.observePending &&
            " It waits for its first trace — the build doesn't."}
        </Typography>
      </Stack>
    );
  }
  const taken = new Set(knownProjects(state, agents).map((p) => p.id));
  const base = observeSlugOf(agent.name);
  let fresh = base;
  for (let n = 2; taken.has(fresh); n += 1) fresh = `${base}-${n}`;

  const link = (raw) => {
    const chosen = resolveObserve(raw, null, agent.name);
    const id = chosen.mode === "create" && !raw?.id ? fresh : chosen.id;
    dispatch({ type: "linkProjectAgent", project: id, agentKey: agent.key });
    const at = new Date().toISOString();
    setAgentValues(
      agent.key,
      chosen.mode === "create"
        ? {
            observePending: { project: id, since: at, via: "manual" },
            observeChosenAt: null,
          }
        : { observeChosenAt: at, observePending: null },
    );
    setPicking(false);
    setLinkedHere(id);
  };

  return (
    <Box
      data-testid="agent-project-note"
      sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={1.25}
        sx={{ px: 1.5, py: 1.1 }}
      >
        <Iconify
          icon="solar:graph-new-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {`${agent.name} has no tracing project`}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Link one first: it ties the agent, its real calls and its
            environments together. It needn&apos;t have traces yet.
          </Typography>
          {!picking && (
            <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
              <Button
                variant="contained"
                color="primary"
                size="small"
                onClick={() => link({ mode: "create", id: fresh })}
              >
                {`Create ${fresh}`}
              </Button>
              <Button
                variant="outlined"
                size="small"
                onClick={() => {
                  setChoice({ mode: "existing", id: null });
                  setPicking(true);
                }}
              >
                Pick existing
              </Button>
            </Stack>
          )}
        </Box>
      </Stack>
      {picking && (
        <Box
          sx={{
            px: 1.5,
            pt: 1.25,
            pb: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <ObserveProjectStep
            agentName={agent.name}
            agentKey={agent.key}
            value={choice}
            onChange={setChoice}
          />
          <Stack
            direction="row"
            justifyContent="flex-end"
            spacing={1}
            sx={{ mt: 1.25 }}
          >
            <Button
              size="small"
              onClick={() => setPicking(false)}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={choice?.mode === "existing" && !choice.id}
              onClick={() => link(choice)}
            >
              {choice?.mode === "existing" ? "Link project" : "Create project"}
            </Button>
          </Stack>
        </Box>
      )}
    </Box>
  );
}

AgentProjectNote.propTypes = { agent: PropTypes.object };
