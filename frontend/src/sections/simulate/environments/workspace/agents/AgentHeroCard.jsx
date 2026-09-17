import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button } from "@mui/material";

import Iconify from "src/components/iconify";

import { MODALITY } from "../../agentTypes";
import { AGENT_SHAPE } from "./agents.shapes";
import { ACTIVE_ACCENT, AGENT_CARD_COPY } from "./agentCards.constants";
import { DetailBlock } from "./agentPrimitives";
import {
  deriveAgentName, deriveTypeLine, connectionRowsFor, sourceRowsFor,
} from "./agentVersion.helpers";

// The environment's one agent, rendered as the focal point of the tab. Type
// icon on the left, name + type-line + active-version chip in the middle, and
// the primary "Add new version" CTA on the right — the tab's primary verb, kept
// loud so the user's eye lands on it. A connection/source detail strip sits
// underneath as a compact key/value grid; with one agent there is nothing to
// expand away from, so there is no expand/collapse chrome.
export default function AgentHeroCard({ agent, onAddVersion }) {
  const type = MODALITY[agent.typeId];
  const name = deriveAgentName(agent, type);
  const typeLine = deriveTypeLine(agent, type);
  const versions = agent.versions || [];
  const activeVersion = versions.find((v) => v.id === agent.activeVersionId) || versions[versions.length - 1];
  const activeLabel = activeVersion?.label || "v1";
  const values = agent.values || {};
  const connectionRows = connectionRowsFor(agent, type, values);
  const sourceRows = sourceRowsFor(agent);

  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider",
      borderRadius: 1.5, overflow: "hidden",
      bgcolor: "background.paper",
    }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 2 }}>
        <Box sx={{
          width: 44, height: 44, borderRadius: 1.25, flexShrink: 0,
          display: "grid", placeItems: "center",
          bgcolor: (t) => alpha(type?.color || t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.1),
          color: type?.color || "text.secondary",
        }}>
          <Iconify icon={type?.icon || "solar:cpu-bolt-linear"} width={22} />
        </Box>

        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.875} flexWrap="wrap" rowGap={0.5}>
            <Typography noWrap sx={{
              typography: "s1", fontWeight: "fontWeightBold", color: "text.primary",
              fontFamily: "ui-monospace, Menlo, monospace",
            }}>
              {name}
            </Typography>
            <Box sx={{
              px: 0.875, py: 0.25, borderRadius: 0.75,
              bgcolor: (t) => alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.16 : 0.1),
              color: "primary.main",
              typography: "s3", fontWeight: "fontWeightBold",
              fontFamily: "ui-monospace, Menlo, monospace",
            }}>
              {activeLabel}
            </Box>
            <Box sx={{
              px: 0.875, py: 0.25, borderRadius: 0.75,
              bgcolor: (t) => alpha(ACTIVE_ACCENT, t.palette.mode === "dark" ? 0.16 : 0.1),
              color: ACTIVE_ACCENT,
              typography: "s3", fontWeight: "fontWeightBold",
              letterSpacing: 0.3,
            }}>
              {AGENT_CARD_COPY.active}
            </Box>
          </Stack>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle", mt: 0.25 }}>
            {typeLine}
            {versions.length > 1 && (
              <Box component="span" sx={{ ml: 1, color: "text.disabled" }}>
                · {versions.length} versions
              </Box>
            )}
          </Typography>
        </Box>

        <Button
          variant="contained" color="primary"
          onClick={onAddVersion}
          startIcon={<Iconify icon="solar:add-circle-linear" width={17} />}
          sx={{ typography: "s2", fontWeight: "fontWeightBold", flexShrink: 0 }}
        >
          {AGENT_CARD_COPY.addVersion}
        </Button>
      </Stack>

      <Box sx={{
        borderTop: "1px solid", borderColor: "divider",
        p: 2.5, display: "grid", gap: 2,
        gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
      }}>
        <DetailBlock title={AGENT_CARD_COPY.connection} icon="solar:link-round-linear" rows={connectionRows} />
        <DetailBlock title={AGENT_CARD_COPY.source} icon="solar:code-square-linear" rows={sourceRows} />
      </Box>
    </Box>
  );
}
AgentHeroCard.propTypes = {
  agent: AGENT_SHAPE.isRequired,
  onAddVersion: PropTypes.func.isRequired,
};
