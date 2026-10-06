import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Collapse, Divider } from "@mui/material";

import Iconify from "src/components/iconify";

import { MODALITY } from "../../agentTypes";
import { AGENT_SHAPE } from "./agents.shapes";
import { SOURCE_ACCENT, ACTIVE_ACCENT, AGENT_CARD_COPY } from "./agentCards.constants";
import { VersionChip, RolePill } from "./agentPrimitives";
import HeaderActions from "./HeaderActions";
import AgentDetail from "./AgentDetail";
import { deriveAgentName, deriveTypeLine } from "./agentVersion.helpers";

// A single agent row. Shape is the same for source and additional; a small
// ENV SOURCE / ACTIVE FOR RUNS chip is what marks the role — no louder border,
// since the chip already identifies it. Clicking the header toggles the detail;
// the action icons stopPropagation so they don't also toggle.
export default function AgentCard({
  agent, isActive, expanded, onToggleExpand,
  onSetActive, onPromote, onRemove,
  onAddVersion, onSetActiveVersion,
}) {
  const type = MODALITY[agent.typeId];
  // Primary label is the agent's own name (repo name, platform agentId,
  // endpoint hostname). The type ("Voice agent · platform") is secondary.
  const name = deriveAgentName(agent, type);
  const typeLine = deriveTypeLine(agent, type);
  const versions = agent.versions || [];
  const activeVersionLabel = versions.find((v) => v.id === agent.activeVersionId)?.label || "v1";

  const toggleFromChevron = (e) => { e.stopPropagation(); onToggleExpand(); };
  const onChevronKeyDown = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      e.stopPropagation();
      onToggleExpand();
    }
  };

  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider",
      borderRadius: 1.5, overflow: "hidden",
      bgcolor: "background.paper",
    }}>
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        onClick={onToggleExpand}
        sx={{ px: 2, py: 1.75, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
      >
        <Box sx={{
          width: 34, height: 34, borderRadius: 1, flexShrink: 0,
          display: "grid", placeItems: "center",
          bgcolor: (t) => alpha(type?.color || t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.1),
          color: type?.color || "text.secondary",
        }}>
          <Iconify icon={type?.icon || "solar:cpu-bolt-linear"} width={18} />
        </Box>

        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.75} flexWrap="wrap" rowGap={0.5}>
            <Typography noWrap sx={{
              typography: "s1", fontWeight: "fontWeightSemiBold", color: "text.primary",
              fontFamily: "ui-monospace, Menlo, monospace",
            }}>
              {name}
            </Typography>
            <VersionChip label={activeVersionLabel} multi={versions.length > 1} />
            {agent.isSource && <RolePill label={AGENT_CARD_COPY.envSource} tint={SOURCE_ACCENT} />}
            {!agent.isSource && isActive && <RolePill label={AGENT_CARD_COPY.activeForRuns} tint={ACTIVE_ACCENT} />}
          </Stack>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", mt: 0.125 }}>
            {typeLine}
            {versions.length > 1 && (
              <Box component="span" sx={{ ml: 1, color: "text.disabled" }}>
                · {versions.length} versions
              </Box>
            )}
            {!agent.isSource && (agent.note || "").toLowerCase().includes("previous source") && (
              <Box component="span" sx={{ ml: 1, color: "text.disabled" }}>
                {" "}{AGENT_CARD_COPY.previouslySource}
              </Box>
            )}
          </Typography>
        </Box>

        <HeaderActions
          agent={agent}
          isActive={isActive}
          onSetActive={onSetActive}
          onPromote={onPromote}
          onRemove={onRemove}
        />

        <Box
          role="button" tabIndex={0}
          aria-expanded={expanded}
          aria-label={expanded ? AGENT_CARD_COPY.collapse : AGENT_CARD_COPY.expand}
          onClick={toggleFromChevron}
          onKeyDown={onChevronKeyDown}
          sx={{
            width: 26, height: 26, borderRadius: 1, flexShrink: 0,
            display: "grid", placeItems: "center", cursor: "pointer",
            color: "text.subtitle",
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Iconify
            icon={expanded ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"}
            width={14}
          />
        </Box>
      </Stack>

      <Collapse in={expanded} unmountOnExit>
        <Divider />
        <AgentDetail
          agent={agent}
          type={type}
          onAddVersion={onAddVersion}
          onSetActiveVersion={onSetActiveVersion}
        />
      </Collapse>
    </Box>
  );
}
AgentCard.propTypes = {
  agent: AGENT_SHAPE,
  isActive: PropTypes.bool, expanded: PropTypes.bool,
  onToggleExpand: PropTypes.func, onSetActive: PropTypes.func,
  onPromote: PropTypes.func, onRemove: PropTypes.func,
  onAddVersion: PropTypes.func, onSetActiveVersion: PropTypes.func,
};
