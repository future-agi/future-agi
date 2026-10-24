import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box, Chip, Stack, Switch, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SURFACES } from "../_mock/optimizationRuns";
import {
  AREA_SURFACE,
  areaPatch,
  areaStatesOf,
  configAreasFor,
} from "./configAreas";
import { QuietTag } from "./AgentBits";
import { Hint } from "./connect/replicationBits";
import { SPEECH_PROVIDERS, speechProviderOf } from "./agentProfile";
import { MONO } from "./agentViewUtils";

/* Sub-rules shown under their area — what inside it may change. */
const RULE_TEXT = {
  knowledge: {
    text: "Content only as a suggestion",
    hint: "Retrieval settings may change. Knowledge content changes only as a suggestion — someone applies it.",
  },
  variables: {
    text: "Defaults only",
    hint: "Which variables exist and how each conversation fills them stay as they are.",
  },
  structure: {
    text: "Always with a written rationale",
    hint: "A structural change — splitting a member, adding a step or a node — ships with a written explanation of why, and gets stricter review.",
  },
};
/* Code — its own switch, listed with the areas. */
const CODE_SURFACES = ["code"];
const CODE_COVERS = {
  code: "Checks, parsers, routing — ships as a draft pull request",
};

/**
 * What an improvement may change (PRD "Agents, defined"): the switches this
 * agent can use, then one line for what it can't — grouped by why — and one
 * for what's never changed. Each area switch writes `limits.areas` and keeps
 * `limits.allowed` in step, which is what the improvement engine reads; code
 * writes `limits.allowed` directly.
 *
 * Sub-rules sit under their area: who speaks first (Opening), adding or
 * removing tools (code agents only), knowledge content as a suggestion,
 * variables' defaults only — and, under Speech in and Speech out, the
 * approved list of speech providers (`limits.approvedSpeech`; the ones the
 * agent uses today are always on it).
 *   limits.areaRules = { whoSpeaksFirst: bool, toolsAddRemove: bool }
 */
export default function AgentConfigAreasCard({
  agent,
  limits,
  can,
  onChange,
  speech,
  children,
}) {
  const areas = useMemo(() => configAreasFor(agent), [agent]);
  const states = useMemo(() => areaStatesOf(limits, can), [limits, can]);
  const rules = limits.areaRules || {};
  const setRule = (patch) => onChange({ areaRules: { ...rules, ...patch } });
  const approved = Array.isArray(limits.approvedSpeech)
    ? limits.approvedSpeech
    : [];
  const today = {
    speechIn: [speechProviderOf(speech?.stt)].filter(Boolean),
    speechOut: [speechProviderOf(speech?.tts)].filter(Boolean),
  };
  const toggleSpeech = (name) => {
    const cur = new Set([...approved, ...today.speechIn, ...today.speechOut]);
    if (approved.includes(name)) cur.delete(name);
    else cur.add(name);
    onChange({ approvedSpeech: [...cur] });
  };
  const toggleSurface = (id, on) =>
    onChange({
      allowed: on
        ? [...new Set([...limits.allowed, id])]
        : limits.allowed.filter((x) => x !== id),
    });
  const codeAgent = !!can?.code?.ok;

  /* Switchable here, out of reach for this agent (by reason), never. */
  const codeRows = SURFACES.filter((x) => CODE_SURFACES.includes(x.id));
  const open = [];
  const blocked = new Map();
  const never = [];
  areas.forEach((a) => {
    if (a.allow === "never") {
      never.push(a);
      return;
    }
    const surface = AREA_SURFACE[a.id];
    if (surface && can?.[surface]?.ok === false) {
      const why = can[surface].why;
      blocked.set(why, [...(blocked.get(why) || []), a.label]);
      return;
    }
    open.push({ kind: "area", a });
  });
  codeRows.forEach((x) => {
    if (can?.[x.id]?.ok) open.push({ kind: "code", x });
    else {
      const why = can?.[x.id]?.why;
      blocked.set(why, [...(blocked.get(why) || []), x.label]);
    }
  });

  const subRule = (a, on) => {
    if (a.id === "opening") {
      return (
        <SubRow
          text="Who speaks first may change"
          hint="Off: the lines may change, the order stays. On: a proposed change may also switch who opens the conversation."
        >
          <Switch
            size="small"
            checked={on && !!rules.whoSpeaksFirst}
            disabled={!on}
            onChange={(e) => setRule({ whoSpeaksFirst: e.target.checked })}
            inputProps={{ "aria-label": "Who speaks first may change" }}
          />
        </SubRow>
      );
    }
    if (a.id === "tools") {
      return (
        <SubRow
          text="Adding or removing tools"
          hint={
            codeAgent
              ? "Adding or removing a tool is a code change — allowed here because the agent's code is connected."
              : "Descriptions and parameters only. Adding or removing a tool needs the agent's code."
          }
        >
          <Tooltip
            arrow
            title={
              codeAgent
                ? ""
                : "Code agents only — connect the agent's repository"
            }
          >
            <span>
              <Switch
                size="small"
                checked={on && codeAgent && !!rules.toolsAddRemove}
                disabled={!on || !codeAgent}
                onChange={(e) => setRule({ toolsAddRemove: e.target.checked })}
                inputProps={{ "aria-label": "Adding or removing tools" }}
              />
            </span>
          </Tooltip>
        </SubRow>
      );
    }
    if (RULE_TEXT[a.id])
      return <SubRow text={RULE_TEXT[a.id].text} hint={RULE_TEXT[a.id].hint} />;
    if ((a.id === "speechIn" || a.id === "speechOut") && on) {
      const list = SPEECH_PROVIDERS[a.id];
      return (
        <Stack
          direction="row"
          alignItems="center"
          flexWrap="wrap"
          gap={0.75}
          sx={{ pl: 4, pr: 2, pb: 1, mt: -0.25 }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mr: 0.25 }}
          >
            {a.id === "speechIn" ? "Approved transcribers" : "Approved voices"}
          </Typography>
          {list.map((name) => {
            const current = today[a.id].includes(name);
            const sel = current || approved.includes(name);
            return (
              <Tooltip
                key={name}
                arrow
                title={current ? "In use today — always approved" : ""}
              >
                <Chip
                  size="small"
                  label={name}
                  clickable={!current}
                  onClick={current ? undefined : () => toggleSpeech(name)}
                  icon={
                    sel ? (
                      <Iconify
                        icon={
                          current
                            ? "solar:lock-keyhole-minimalistic-linear"
                            : "solar:check-linear"
                        }
                        width={14}
                      />
                    ) : undefined
                  }
                  sx={{
                    fontFamily: MONO,
                    color: "text.primary",
                    "& .MuiChip-icon": { color: "text.primary" },
                    bgcolor: "transparent",
                    border: "1px solid",
                    borderColor: sel ? "text.primary" : "divider",
                  }}
                />
              </Tooltip>
            );
          })}
        </Stack>
      );
    }
    return null;
  };

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
        minWidth: 0,
      }}
    >
      {open.map((row, i) => {
        if (row.kind === "code") {
          const { x } = row;
          return (
            <Box
              key={x.id}
              sx={{
                borderTop: i ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              <AreaRow
                label={x.label}
                covers={CODE_COVERS[x.id]}
                hint={`${x.example}. Ships as: ${x.delivered}.`}
              >
                <Switch
                  size="small"
                  checked={limits.allowed.includes(x.id)}
                  onChange={(e) => toggleSurface(x.id, e.target.checked)}
                  inputProps={{ "aria-label": `${x.label} may change` }}
                />
              </AreaRow>
            </Box>
          );
        }
        const { a } = row;
        const on = !!states[a.id];
        return (
          <Box
            key={a.id}
            sx={{ borderTop: i ? "1px solid" : "none", borderColor: "divider" }}
          >
            <AreaRow
              label={a.label}
              covers={a.covers}
              hint={a.note}
              voice={a.applies === "voice"}
            >
              <Switch
                size="small"
                checked={on}
                onChange={(e) =>
                  onChange(areaPatch(limits, a.id, e.target.checked, can))
                }
                inputProps={{ "aria-label": `${a.label} may change` }}
              />
            </AreaRow>
            {subRule(a, on)}
          </Box>
        );
      })}

      {(blocked.size > 0 || never.length > 0) && (
        <Box
          sx={{
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
            px: 2,
            py: 1,
          }}
        >
          <Stack spacing={0.625}>
            {[...blocked.entries()].map(([why, labels]) => (
              <OutLine
                key={why}
                icon="solar:link-broken-linear"
                labels={labels}
                why={why}
              />
            ))}
            {never.length > 0 && (
              <OutLine
                icon="solar:lock-keyhole-minimalistic-linear"
                labels={never.map((a) => a.label)}
                why="Never changed"
                hint="Analysis acts as a judge, and the improver may not edit its judges. Account holds credentials, numbers and compliance settings."
              />
            )}
          </Stack>
        </Box>
      )}
      {children}
    </Box>
  );
}
AgentConfigAreasCard.propTypes = {
  agent: PropTypes.object.isRequired,
  limits: PropTypes.object.isRequired,
  can: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  speech: PropTypes.object,
  children: PropTypes.node,
};

/* One area: name, what it covers on one line (the rest behind ⓘ), its switch. */
function AreaRow({ label, covers, hint, voice, children }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{ px: 2, py: 0.5, minHeight: 36 }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ width: { xs: "auto", md: 190 }, flexShrink: 0, minWidth: 0 }}
      >
        <Typography noWrap sx={{ typography: "s2_1", fontWeight: 500 }}>
          {label}
        </Typography>
        {voice && <QuietTag>Voice</QuietTag>}
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ flex: 1, minWidth: 0, display: { xs: "none", md: "flex" } }}
      >
        <Typography
          noWrap
          title={covers}
          sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}
        >
          {covers}
        </Typography>
        <Hint text={hint} />
      </Stack>
      <Box sx={{ flexShrink: 0, ml: "auto" }}>{children}</Box>
    </Stack>
  );
}
AreaRow.propTypes = {
  label: PropTypes.node,
  covers: PropTypes.string,
  hint: PropTypes.node,
  voice: PropTypes.bool,
  children: PropTypes.node,
};

/* A rule inside an area — indented under it, with a switch or nothing. */
function SubRow({ text, hint, children }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ pl: 4, pr: 2, pb: 0.75, mt: -0.375 }}
    >
      <Iconify
        icon="solar:alt-arrow-right-linear"
        width={12}
        sx={{ color: "text.disabled", flexShrink: 0 }}
      />
      <Typography
        noWrap
        sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}
      >
        {text}
      </Typography>
      <Hint text={hint} />
      <Box sx={{ flex: 1 }} />
      {children}
    </Stack>
  );
}
SubRow.propTypes = {
  text: PropTypes.node,
  hint: PropTypes.node,
  children: PropTypes.node,
};

/* What this agent can't change — the areas, and why, on one line. */
function OutLine({ icon, labels, why, hint }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.875}
      sx={{ minWidth: 0 }}
    >
      <Iconify
        icon={icon}
        width={14}
        sx={{ color: "text.disabled", flexShrink: 0 }}
      />
      <Typography
        noWrap
        title={`${labels.join(", ")} — ${why}`}
        sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}
      >
        <Box component="span" sx={{ color: "text.secondary", fontWeight: 600 }}>
          {labels.join(", ")}
        </Box>
        {` · ${why}`}
      </Typography>
      <Hint text={hint} />
    </Stack>
  );
}
OutLine.propTypes = {
  icon: PropTypes.string,
  labels: PropTypes.array,
  why: PropTypes.string,
  hint: PropTypes.node,
};
