import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Chip, Stack, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useConnectedAgents, useSimStore } from "../../store";
import {
  LANGUAGE_OPTIONS,
  observeProjectById,
  observeSlugOf,
  resolveObserve,
} from "../../_mock/connectAgent";
import { knownProjects, projectLinkOf } from "../../_mock/projectLinks";
import { pendingProjectIds } from "../observeProject";
import {
  ChipCard,
  Field,
  SegmentedToggle,
} from "../../environments/StartEnvironment";
import { HintLine } from "./replicationBits";
import { WhereOption } from "./connectBits";

const MONO = "ui-monospace, Menlo, monospace";
const selectedBg = "action.selected";

/* A quiet tag — "From the issue", "Phase 2" — the label does the work. */
function Tag({ children }) {
  return (
    <Chip
      size="small"
      variant="outlined"
      label={children}
      sx={{ flexShrink: 0 }}
    />
  );
}
Tag.propTypes = { children: PropTypes.node };

/**
 * Observe — where the agent's production traces land, before its
 * constraints. Pick a project that already receives them, or create one
 * named after the agent. Linking is per project: Error Feed groups failures
 * by project, and Fix finds the agent for every issue from it. A project the
 * "Connect a new agent" link (in Fix) came from is picked already.
 */
export function ObserveProjectStep({
  agentName,
  agentKey,
  value,
  onChange,
  prefillProject,
  issue,
}) {
  const { state } = useSimStore();
  const { agents } = useConnectedAgents();
  const [query, setQuery] = useState("");
  const chosen = resolveObserve(value, prefillProject, agentName);
  /* Two answers, side by side: an existing project (even before one is
     picked from the list) or a new one. */
  const creating = value?.mode !== "existing" && chosen.mode === "create";
  /* Back to "existing" returns to the project picked there before. */
  const [lastPicked, setLastPicked] = useState(
    chosen.mode === "existing" ? chosen.id : null,
  );
  /* A new project's default name — never one Observe already has. */
  const taken = new Set(knownProjects(state, agents).map((p) => p.id));
  const freeSlug = (base) => {
    let n = 1;
    let id = base;
    while (taken.has(id)) {
      n += 1;
      id = `${base}-${n}`;
    }
    return id;
  };
  const createId = creating ? chosen.id : freeSlug(observeSlugOf(agentName));
  /* What each project traces now — a project has one agent, so picking one
     that has another moves it here. This agent's own link reads as such. */
  const linkOf = (id) => projectLinkOf(id, state, agents);
  const prefillUnknown = prefillProject && !observeProjectById(prefillProject);
  /* Grouped by who the project traces: this agent's own (and a suggestion)
     first, then projects with no agent — the likeliest home — then ones
     another agent owns, which taking moves. The order is fixed when the list
     opens, so a click never moves the row under the cursor. */
  const [firstPicked] = useState(chosen.mode === "existing" ? chosen.id : null);
  const groupOf = (p) => {
    const link = linkOf(p.id);
    if (
      p.id === firstPicked ||
      p.id === prefillProject ||
      (link.status === "linked" && agentKey && link.agent?.key === agentKey)
    )
      return "this";
    return link.status === "linked" ? "other" : "free";
  };
  /* Another agent's new project with no trace yet isn't in Observe — not offered. */
  const waiting = pendingProjectIds(state, agents);
  const known = knownProjects(state, agents).filter(
    (p) => !waiting.has(p.id) || p.link.agentKey === agentKey,
  );
  const projects = prefillUnknown
    ? [
        {
          id: prefillProject,
          /* A tracing project opened from its own page carries its name. */
          name:
            new URLSearchParams(window.location.search).get("projectName") ||
            prefillProject,
          traces: new URLSearchParams(window.location.search).get("projectName")
            ? "From Tracing"
            : "From the Error Feed",
          issues: null,
        },
        ...known,
      ]
    : known;
  const q = query.trim().toLowerCase();
  const shown = q
    ? projects.filter((p) => `${p.name} ${p.id}`.toLowerCase().includes(q))
    : projects;
  const groups = [
    {
      id: "this",
      label: null,
      rows: shown.filter((p) => groupOf(p) === "this"),
    },
    {
      id: "free",
      label: "No agent yet",
      rows: shown.filter((p) => groupOf(p) === "free"),
    },
    {
      id: "other",
      label: "Traces another agent — taking one moves it here",
      rows: shown.filter((p) => groupOf(p) === "other"),
    },
  ].filter((g) => g.rows.length);

  return (
    <Stack spacing={1.5}>
      <Typography
        component="div"
        sx={{ typography: "s2", color: "text.secondary" }}
      >
        <HintLine hint="Fixes for this project's Error Feed issues find the agent through this link.">
          Where this agent&apos;s production traces land.
        </HintLine>
      </Typography>

      <Box
        sx={{
          display: "grid",
          gap: 1,
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
        }}
        role="radiogroup"
        aria-label="Which project"
      >
        <WhereOption
          icon="solar:graph-new-linear"
          label="Use an existing project"
          on={!creating}
          short={`${projects.length} project${projects.length === 1 ? "" : "s"} in Observe — pick the one its traces land in`}
          hint="For an agent that already sends traces. Error Feed and Fix use the project from now on."
          onClick={() =>
            creating && onChange({ mode: "existing", id: lastPicked })
          }
        />
        <WhereOption
          icon="solar:add-circle-linear"
          label="Create a new project"
          on={creating}
          short="Its traces don't land anywhere yet — they start once tracing is on"
          hint="Name it here. It appears in Observe with its first trace."
          onClick={() =>
            !creating && onChange({ mode: "create", id: createId })
          }
        />
      </Box>

      {creating ? (
        <TextField
          size="small"
          fullWidth
          autoFocus
          value={createId}
          onChange={(e) => onChange({ mode: "create", id: e.target.value })}
          placeholder="project-name"
          helperText="The project's name in Observe"
          sx={{
            "& .MuiInputBase-input": { typography: "s2", fontFamily: MONO },
          }}
        />
      ) : (
        <Box>
          {projects.length > 5 && (
            <TextField
              size="small"
              fullWidth
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search projects"
              InputProps={{
                startAdornment: (
                  <Iconify
                    icon="solar:magnifier-linear"
                    width={14}
                    sx={{ color: "text.disabled", mr: 0.75 }}
                  />
                ),
              }}
              sx={{ mb: 0.75, "& .MuiInputBase-input": { typography: "s2" } }}
            />
          )}
          <Stack
            spacing={0.75}
            role="radiogroup"
            aria-label="Observe project"
            sx={{ maxHeight: 300, overflowY: "auto", pr: 0.5 }}
          >
            {groups.map((g) => (
              <Stack key={g.id} spacing={0.75}>
                {g.label && (
                  <Typography
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      color: "text.subtitle",
                      pt: 0.5,
                      px: 0.25,
                    }}
                  >{`${g.label} · ${g.rows.length}`}</Typography>
                )}
                {g.rows.map((p) => {
                  const link = linkOf(p.id);
                  const mine =
                    link.status === "linked" &&
                    agentKey &&
                    link.agent?.key === agentKey;
                  const owner =
                    link.status === "linked" && !mine
                      ? link.agent?.name || "another agent"
                      : null;
                  return (
                    <ProjectRow
                      key={p.id}
                      on={
                        !creating &&
                        chosen.mode === "existing" &&
                        chosen.id === p.id
                      }
                      onClick={() => {
                        setLastPicked(p.id);
                        onChange({ mode: "existing", id: p.id });
                      }}
                      icon="solar:graph-new-linear"
                      title={p.name}
                      mono={p.id}
                      meta={[
                        owner && `Traces ${owner}`,
                        p.traces,
                        p.issues != null &&
                          `${p.issues} open issue${p.issues === 1 ? "" : "s"} in Error Feed`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                      tag={
                        p.id === prefillProject
                          ? issue
                            ? `From ${issue}`
                            : "From the issue"
                          : mine
                            ? waiting.has(p.id)
                              ? "Waiting for first trace"
                              : "Linked now"
                            : null
                      }
                    />
                  );
                })}
              </Stack>
            ))}
            {!groups.some((g) => g.id !== "this") && !q && (
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", px: 0.5 }}
              >
                Every project in Observe traces another agent — create a new one
                for this agent.
              </Typography>
            )}
            {!shown.length && (
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", px: 0.5 }}
              >
                No project matches.
              </Typography>
            )}
          </Stack>
        </Box>
      )}
    </Stack>
  );
}
ObserveProjectStep.propTypes = {
  agentName: PropTypes.string,
  /* The agent being linked — its own current project reads "Linked now". */
  agentKey: PropTypes.string,
  /* { mode: "existing" | "create", id } — null until something is picked. */
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  prefillProject: PropTypes.string,
  issue: PropTypes.string,
};

function ProjectRow({ on, onClick, icon, title, mono, meta, tag }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      role="radio"
      aria-checked={on}
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      sx={{
        px: 1.5,
        py: 1,
        borderRadius: 1,
        cursor: "pointer",
        border: "1px solid",
        borderColor: on ? "text.primary" : "divider",
        bgcolor: on ? selectedBg : "transparent",
      }}
    >
      <Iconify
        icon={icon}
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Box flex={1} minWidth={0}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ minWidth: 0 }}
        >
          <Typography
            noWrap
            sx={{ typography: "s2", fontWeight: 600, minWidth: 0 }}
          >
            {title}
          </Typography>
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.subtitle",
              fontFamily: MONO,
              minWidth: 0,
            }}
          >
            {mono}
          </Typography>
          {tag && <Tag>{tag}</Tag>}
        </Stack>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
          {meta}
        </Typography>
      </Box>
      {on && (
        <Iconify
          icon="solar:check-circle-bold"
          width={16}
          sx={{ color: "text.primary", flexShrink: 0 }}
        />
      )}
    </Stack>
  );
}
ProjectRow.propTypes = {
  on: PropTypes.bool,
  onClick: PropTypes.func,
  icon: PropTypes.string,
  title: PropTypes.node,
  mono: PropTypes.string,
  meta: PropTypes.node,
  tag: PropTypes.node,
};

/**
 * A hosted agent's Observe project needs no step: the key pulls its calls,
 * so it's linked the moment it's connected — said, with the project's name.
 */
export function ObserveLinkedRow({ project, on }) {
  const p = observeProjectById(project);
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{
        px: 1.75,
        py: 1.125,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Iconify
        icon="solar:graph-new-linear"
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Box flex={1} minWidth={0}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ minWidth: 0 }}
        >
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
            Observe project
          </Typography>
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.subtitle",
              fontFamily: MONO,
              minWidth: 0,
            }}
          >
            {project}
          </Typography>
        </Stack>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
          {on
            ? `${p ? p.name : "Created with the agent"} · its Error Feed issues are found and fixed here`
            : "Not linked — link a project on the agent's Connections tab."}
        </Typography>
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ color: on ? "text.primary" : "text.subtitle", flexShrink: 0 }}
      >
        <Iconify
          icon={on ? "solar:link-circle-linear" : "solar:link-broken-linear"}
          width={14}
        />
        <Typography noWrap sx={{ typography: "s3", fontWeight: 600 }}>
          {on ? "Linked" : "Not linked"}
        </Typography>
      </Stack>
    </Stack>
  );
}
ObserveLinkedRow.propTypes = { project: PropTypes.string, on: PropTypes.bool };

/* One labelled row of the About table — label left, controls right. */
function AboutRow({ label, hint, first, children }) {
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      spacing={{ xs: 0.5, sm: 2 }}
      alignItems={{ sm: "flex-start" }}
      sx={{
        px: 1.75,
        py: 1.25,
        borderTop: first ? "none" : "1px solid",
        borderColor: "divider",
      }}
    >
      <Box sx={{ width: { sm: 140 }, flexShrink: 0, pt: "6px" }}>
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          {label}
        </Typography>
      </Box>
      <Box flex={1} minWidth={0}>
        {children}
        {hint && (
          <Typography
            component="div"
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}
          >
            {hint}
          </Typography>
        )}
      </Box>
    </Stack>
  );
}
AboutRow.propTypes = {
  label: PropTypes.string,
  hint: PropTypes.node,
  first: PropTypes.bool,
  children: PropTypes.node,
};

function NumberInput({ value, onChange, suffix, label }) {
  return (
    <TextField
      size="small"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 4))}
      inputProps={{ inputMode: "numeric", "aria-label": label }}
      InputProps={{
        endAdornment: (
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              ml: 0.5,
              whiteSpace: "nowrap",
            }}
          >
            {suffix}
          </Typography>
        ),
      }}
      sx={{
        width: 170,
        "& .MuiInputBase-input": {
          typography: "s2",
          fontVariantNumeric: "tabular-nums",
        },
      }}
    />
  );
}
NumberInput.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onChange: PropTypes.func,
  suffix: PropTypes.string,
  label: PropTypes.string,
};

const CHANNEL_OPTIONS = [
  { value: "voice", label: "Voice" },
  { value: "chat", label: "Chat" },
  { value: "both", label: "Both" },
];

/**
 * Who the agent is and how it's reached: owner (you, unless changed), the
 * languages it speaks, a line on what it does; voice, chat or both; and the
 * reach for each channel — voice: direction, who speaks first, conversations
 * at once; chat: who writes first, turn limit. `reachLocked` hides what an
 * earlier step already asked (a hosted voice agent's direction and opening).
 */
export function AgentAboutFields({
  value,
  onChange,
  defaultChannel = "chat",
  reachLocked,
}) {
  const channels = value.channels?.length ? value.channels : [defaultChannel];
  const channelValue = channels.length > 1 ? "both" : channels[0];
  const voice = channels.includes("voice");
  const chat = channels.includes("chat");
  const reach = value.reach || {};
  const set = (patch) => onChange({ ...value, ...patch });
  const setReach = (patch) => set({ reach: { ...reach, ...patch } });
  const toggleLanguage = (code) => {
    const list = value.languages || [];
    const next = list.includes(code)
      ? list.filter((c) => c !== code)
      : [...list, code];
    /* An agent speaks at least one language. */
    if (next.length) set({ languages: next });
  };

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <AboutRow
        label="Owner"
        first
        hint="Asked about its issues and improvements."
      >
        <Box sx={{ maxWidth: 360 }}>
          <Field
            value={value.owner || ""}
            onChange={(v) => set({ owner: v })}
            placeholder="Name or team"
          />
        </Box>
      </AboutRow>
      <AboutRow
        label="What it does"
        hint="One or two lines, shown on the agent page."
      >
        <Field
          value={value.description || ""}
          onChange={(v) => set({ description: v.slice(0, 280) })}
          placeholder="e.g. Takes return and refund calls for the US store; hands chargebacks to a person."
          multiline
          minRows={2}
        />
      </AboutRow>
      <AboutRow
        label="Languages"
        hint={
          <HintLine hint="Scenarios are written in these.">
            A reply in another language counts as a failure.
          </HintLine>
        }
      >
        <Stack direction="row" spacing={0.75} useFlexGap flexWrap="wrap">
          {LANGUAGE_OPTIONS.map((l) => (
            <ChipCard
              key={l.code}
              label={l.label}
              on={(value.languages || []).includes(l.code)}
              onClick={() => toggleLanguage(l.code)}
            />
          ))}
        </Stack>
      </AboutRow>
      <AboutRow
        label="Channels"
        hint={
          channels.length > 1
            ? "We'll build an environment per channel."
            : "Takes the other channel too? Choose Both."
        }
      >
        <SegmentedToggle
          value={channelValue}
          onChange={(v) =>
            set({ channels: v === "both" ? ["voice", "chat"] : [v] })
          }
          options={CHANNEL_OPTIONS}
        />
      </AboutRow>
      {voice && (
        <AboutRow
          label={chat ? "Reach · voice" : "Reach"}
          hint={
            reachLocked
              ? "Direction, opening and number were set on the Agent step."
              : null
          }
        >
          <Stack spacing={1.25}>
            {!reachLocked && (
              <Stack
                direction={{ xs: "column", md: "row" }}
                spacing={{ xs: 1.25, md: 3 }}
              >
                <Box>
                  <Typography
                    sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}
                  >
                    Direction
                  </Typography>
                  <SegmentedToggle
                    value={reach.direction || "inbound"}
                    onChange={(v) => setReach({ direction: v })}
                    options={[
                      { value: "inbound", label: "Takes calls" },
                      { value: "outbound", label: "Places calls" },
                    ]}
                  />
                </Box>
                <Box>
                  <Typography
                    sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}
                  >
                    Who speaks first
                  </Typography>
                  <SegmentedToggle
                    value={reach.whoSpeaksFirst || "agent"}
                    onChange={(v) => setReach({ whoSpeaksFirst: v })}
                    options={[
                      { value: "agent", label: "The agent" },
                      { value: "caller", label: "The caller" },
                    ]}
                  />
                </Box>
              </Stack>
            )}
            <Box>
              <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
                Conversations at once
              </Typography>
              <NumberInput
                label="Maximum conversations at once"
                value={reach.maxConcurrent}
                onChange={(v) => setReach({ maxConcurrent: v })}
                suffix="at most"
              />
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}
              >
                Simulations never place more calls than this at once.
              </Typography>
            </Box>
          </Stack>
        </AboutRow>
      )}
      {chat && (
        <AboutRow label={voice ? "Reach · chat" : "Reach"}>
          <Stack
            direction={{ xs: "column", md: "row" }}
            spacing={{ xs: 1.25, md: 3 }}
          >
            <Box>
              <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
                Who writes first
              </Typography>
              <SegmentedToggle
                value={reach.whoWritesFirst || "user"}
                onChange={(v) => setReach({ whoWritesFirst: v })}
                options={[
                  { value: "user", label: "The user" },
                  { value: "agent", label: "The agent" },
                ]}
              />
            </Box>
            <Box>
              <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
                Turn limit
              </Typography>
              <NumberInput
                label="Turn limit"
                value={reach.turnLimit}
                onChange={(v) => setReach({ turnLimit: v })}
                suffix="turns"
              />
            </Box>
          </Stack>
        </AboutRow>
      )}
    </Box>
  );
}
AgentAboutFields.propTypes = {
  /* { owner, languages, description, channels, reach } — see emptyProfile(). */
  value: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  defaultChannel: PropTypes.oneOf(["voice", "chat"]),
  reachLocked: PropTypes.bool,
};
