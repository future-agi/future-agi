import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Box, Button, InputBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useConnectedAgents, useSimStore } from "../../store";
import { agentSourceOf } from "../../_mock/agentRegistry";
import { observeProjectById } from "../../_mock/connectAgent";
import { rankAgentsFor } from "../../_mock/projectAgentMatch";
import { LogoTile } from "../../environments/LandingBits";
import { observeProjectOf } from "../observeProject";
import { TextLink } from "./connectBits";

const SHOWN = 5;

/**
 * "Is it one of your agents?" — the agents already in Agents, the ones the
 * project's traces point at first, for a traced project no agent records.
 * Picking one saves the project on that agent (one project per agent): an
 * agent that had another project moves to this one, and the row says so
 * before anything is linked.
 */
export default function YourAgentsList({
  project,
  projectName: nameGiven,
  onLinked,
}) {
  const { agents, state } = useConnectedAgents();
  const { commit } = useSimStore();
  const [query, setQuery] = useState("");
  const [all, setAll] = useState(false);
  const [picked, setPicked] = useState(null);
  const projectName = nameGiven || observeProjectById(project)?.name || project;

  const ranked = useMemo(
    () => rankAgentsFor(project, agents, nameGiven),
    [project, agents, nameGiven],
  );
  const q = query.trim().toLowerCase();
  const shown = q
    ? ranked.filter(({ agent }) =>
        String(agent.name || "")
          .toLowerCase()
          .includes(q),
      )
    : all
      ? ranked
      : /* The likely ones only, when the traces point at any. */
        ranked.filter((r) => r.score > 0).slice(0, SHOWN).length
        ? ranked.filter((r) => r.score > 0).slice(0, SHOWN)
        : ranked.slice(0, SHOWN);
  const pickedRow = ranked.find((r) => r.agent.key === picked) || null;
  const pickedFrom = pickedRow
    ? observeProjectOf(pickedRow.agent, state, agents)
    : null;

  if (!ranked.length) return null;

  const link = () => {
    const agent = pickedRow.agent;
    /* One project per agent: the one it had is left with no agent, so it
       doesn't fall back to a seeded or watched link. */
    if (pickedFrom && pickedFrom.project !== project)
      commit({
        type: "linkProjectAgent",
        project: pickedFrom.project,
        agentKey: null,
      });
    /* Saved straight away — the dialog closes over it. */
    commit({ type: "linkProjectAgent", project, agentKey: agent.key });
    onLinked?.(agent, pickedFrom);
  };

  return (
    <Box>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1.5}
        sx={{ px: 2.5, pt: 2, pb: 1 }}
      >
        <Typography
          sx={{ typography: "s1", fontWeight: 500, flex: 1, minWidth: 0 }}
        >
          {`Is it one of your agents? (${ranked.length})`}
        </Typography>
      </Stack>
      <Typography
        sx={{ typography: "s3", color: "text.tertiary", px: 2.5, mb: 1.25 }}
      >
        {`Pick the agent that sends ${projectName}'s traces. The link is saved on the agent, and every issue from ${projectName} can then be fixed.`}
      </Typography>
      {ranked.length > SHOWN && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            mx: 2.5,
            mb: 1,
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
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Search ${ranked.length} agents`}
            inputProps={{
              "aria-label": "Search your agents",
              spellCheck: false,
            }}
            sx={{
              typography: "s2",
              "& input::placeholder": { color: "text.disabled", opacity: 1 },
            }}
          />
        </Stack>
      )}
      <Box
        role="listbox"
        aria-label="Your agents"
        sx={{
          mx: 2.5,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
        }}
      >
        {shown.map(({ agent, reasons }, i) => {
          const source = agentSourceOf(agent);
          const has = observeProjectOf(agent, state, agents);
          const on = agent.key === picked;
          const meta = [
            source.label,
            has
              ? `Linked to ${observeProjectById(has.project)?.name || has.project}`
              : "No project yet",
            reasons[0]?.text,
          ]
            .filter(Boolean)
            .join(" · ");
          return (
            <Stack
              key={agent.key}
              role="option"
              aria-selected={on}
              direction="row"
              alignItems="center"
              spacing={1.25}
              onClick={() => setPicked(on ? null : agent.key)}
              sx={{
                px: 1.25,
                minHeight: 44,
                cursor: "pointer",
                borderTop: i ? "1px solid" : "none",
                borderColor: "divider",
                bgcolor: on ? "action.selected" : "transparent",
                "&:hover": { bgcolor: on ? "action.selected" : "action.hover" },
              }}
            >
              <LogoTile icon={source.icon} size={28} />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 500 }}>
                  {agent.name}
                </Typography>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >
                  {meta}
                </Typography>
              </Box>
              <Iconify
                icon={on ? "solar:check-circle-bold" : "solar:circle-linear"}
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
            {`No agent named “${query.trim()}”.`}
          </Typography>
        )}
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 2.5, pt: 1.25, pb: 2 }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {!q && ranked.length > shown.length && !all && (
            <TextLink onClick={() => setAll(true)}>
              {`Show all ${ranked.length}`}
            </TextLink>
          )}
          {!q && all && ranked.length > SHOWN && (
            <TextLink onClick={() => setAll((x) => !x)}>Show fewer</TextLink>
          )}
          {pickedRow && pickedFrom && pickedFrom.project !== project && (
            <Typography
              sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
            >
              {`${pickedRow.agent.name} is linked to ${observeProjectById(pickedFrom.project)?.name || pickedFrom.project} — this replaces it, and that project's issues will need an agent again.`}
            </Typography>
          )}
        </Box>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!pickedRow}
          onClick={link}
          sx={{ flexShrink: 0 }}
        >
          {pickedRow ? `Link to ${pickedRow.agent.name}` : "Link"}
        </Button>
      </Stack>
    </Box>
  );
}

YourAgentsList.propTypes = {
  /* The Observe project id the picked agent gets. */
  project: PropTypes.string.isRequired,
  /* For a project outside the catalogue (a tracing project by its id). */
  projectName: PropTypes.string,
  /* (agent, previousProject) after the link is saved. */
  onLinked: PropTypes.func,
};
