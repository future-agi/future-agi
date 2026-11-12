import React from "react";
import PropTypes from "prop-types";
import { Box, Chip, InputBase, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { chipsFor } from "../iterateModel";
import { ChangeDiff, SmallSelect } from "./changeParts";

const MONO = "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace";

/**
 * v2.1 g4 (change:agent-node:prompt-editor): how a node's prompt changes
 * here (pure): the opened finding's fixes when it has some, else the agent
 * itself (a version saved in Agent Playground runs from the wizard).
 */
export function nodePromptHowTo({ hasFixes }) {
  return hasFixes
    ? "Read-only. To change it, pick one of this finding's fixes above, or edit the agent in Agent Playground."
    : "Read-only. To change this prompt, edit the agent in Agent Playground and run the version you save there.";
}

/**
 * Agent node tab (R2-SPEC §4.3.1): node picker, the node's prompt with a
 * live word diff vs the parent, suggestion chips (fill the editor, never
 * run) and the draft version it saves as. Limits live in the dock `More ▾`.
 */
const AgentNodeChange = ({ dock }) => {
  const { parent, draft, nodes, preset, patch, canEdit, saveName } = dock;
  const agent = parent.config?.agent ?? {};
  const node = nodes.find((n) => n.id === draft.node) ?? nodes[0];
  if (!node)
    return (
      <Typography typography="s2" color="text.secondary">
        This agent has no LLM node to edit.
      </Typography>
    );
  const parentPrompt = node.prompt ?? "";
  const value = draft.prompts?.[node.id] ?? parentPrompt;
  const changed = value !== parentPrompt;
  // v2.1 g4: any agent edit (another node, the limits) saves a version, so
  // its name shows whichever node is open.
  // A draft that already ran was saved then (Rerun as is reruns that
  // version), so no new Saves-as line shows.
  const saves =
    !dock.sameAsRan && (dock.changes ?? []).some((c) => c.axis === "agent");
  const chips = chipsFor(node.id, parentPrompt, preset);
  const setPrompt = (text) =>
    patch((d) => ({ prompts: { ...(d.prompts ?? {}), [node.id]: text } }));

  return (
    <Stack spacing={1}>
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <SmallSelect
          label="Node"
          value={node.id}
          disabled={nodes.length < 2}
          options={nodes.map((n) => ({ value: n.id, label: n.id }))}
          onChange={(id) => patch({ node: id })}
          sx={{ fontFamily: "monospace", minWidth: 160 }}
        />
        {chips.length && preset ? (
          // L7b G3: the chips are the opened finding's fixes (the world
          // scripts each one); the label says so, on one line.
          <Typography
            typography="s3"
            color="text.disabled"
            noWrap
            sx={{ flexShrink: 0 }}
          >
            Fixes for this finding
          </Typography>
        ) : null}
        {chips.map((c) => {
          const highlighted =
            value === c.text || (!changed && preset?.chip === c.id);
          return (
            <CustomTooltip
              key={c.id}
              show
              title={`"${c.text}"`}
              size="small"
              placement="top"
              arrow
            >
              <Chip
                size="small"
                label={c.label}
                disabled={!canEdit}
                icon={<Iconify icon="mdi:creation-outline" width={14} />}
                onClick={() => setPrompt(c.text)}
                variant="outlined"
                sx={(theme) => ({
                  typography: "s3",
                  ...(highlighted && {
                    borderColor: "primary.main",
                    bgcolor: alpha(theme.palette.primary.main, 0.12),
                    color: "primary.main",
                  }),
                })}
              />
            </CustomTooltip>
          );
        })}
      </Stack>
      {/* v2.1 g4 (change:agent-node:prompt-editor): the node's prompt is
          read-only text (no editable field, no counter): it changes through
          the finding's fixes (the chips) or in Agent Playground. */}
      <Box
        data-testid="agent-node-prompt"
        aria-label={`${node.id} prompt`}
        sx={{
          fontFamily: MONO,
          fontSize: 12,
          lineHeight: 1.55,
          p: 1,
          borderRadius: 0.5,
          bgcolor: "background.neutral",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          maxHeight: 160,
          overflowY: "auto",
        }}
      >
        {value}
      </Box>
      <Typography
        typography="s3"
        color="text.secondary"
        data-testid="agent-node-howto"
      >
        {nodePromptHowTo({ hasFixes: Boolean(chips.length && preset) })}
      </Typography>
      {changed ? (
        <ChangeDiff
          from={parentPrompt}
          to={value}
          caption={`vs ${agent.name ?? "agent"} ${agent.version ?? ""}`.trim()}
        />
      ) : null}
      {saves ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          data-testid="agent-save-as"
        >
          <Iconify
            icon="mdi:content-save-outline"
            width={14}
            sx={{ color: "text.disabled" }}
          />
          <Typography typography="s3" color="text.secondary">
            {`Saves as ${agent.name ?? "Agent"}`}
          </Typography>
          <InputBase
            value={saveName}
            disabled={!canEdit}
            onChange={(e) => patch({ saveName: e.target.value })}
            inputProps={{ "aria-label": "Version name", size: 6 }}
            sx={{
              typography: "s3",
              fontWeight: 600,
              px: 0.5,
              borderBottom: "1px dashed",
              borderColor: "divider",
              "& input": { p: 0 },
            }}
          />
          <Typography typography="s3" color="text.secondary">
            · draft, not published
          </Typography>
        </Stack>
      ) : null}
    </Stack>
  );
};

AgentNodeChange.propTypes = { dock: PropTypes.object.isRequired };

export default AgentNodeChange;
