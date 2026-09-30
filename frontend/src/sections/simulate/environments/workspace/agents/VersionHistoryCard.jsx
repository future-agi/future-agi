import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Tooltip } from "@mui/material";

import Iconify from "src/components/iconify";

import { AGENT_SHAPE } from "./agents.shapes";
import { ACTIVE_ACCENT, AGENT_CARD_COPY } from "./agentCards.constants";

// A seeded-from-template env is read-only until forked; the set-active /
// roll-back control carries this on its tooltip while locked.
const LOCK_TOOLTIP = "Fork this environment to edit.";

const TIMESTAMP_FORMAT = {
  month: "short", day: "numeric", year: "numeric",
  hour: "2-digit", minute: "2-digit",
};

// Timeline of every version the agent has carried. The active version is
// highlighted (green tick + ACTIVE pill); every other row exposes a set-active
// action — "Roll back to this" for older versions, "Set active" for newer ones,
// both firing the same handler. A vertical rail with connected dots makes the
// timeline read as a sequence, latest at the top where the eye first lands.
export default function VersionHistoryCard({ agent, onSetActiveVersion, locked = false }) {
  const versions = agent.versions || [];
  const activeId = agent.activeVersionId || versions[versions.length - 1]?.id;
  const activeIdx = versions.findIndex((v) => v.id === activeId);
  const ordered = versions.slice().reverse();

  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider",
      borderRadius: 1.5, overflow: "hidden",
      bgcolor: "background.paper",
    }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2.5, py: 1.75, borderBottom: "1px solid", borderColor: "divider" }}>
        <Iconify icon="solar:layers-minimalistic-linear" width={16} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", letterSpacing: 0.3, color: "text.subtitle", textTransform: "uppercase" }}>
          {AGENT_CARD_COPY.versionHistory}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
          · {versions.length} version{versions.length === 1 ? "" : "s"}
        </Typography>
      </Stack>

      <Box sx={{ px: 2.5, py: 2 }}>
        <Stack spacing={0}>
          {ordered.map((v, i) => {
            const isActive = v.id === activeId;
            const originalIdx = versions.findIndex((x) => x.id === v.id);
            const isOlderThanActive = originalIdx < activeIdx;
            const isLast = i === ordered.length - 1;
            return (
              <Stack
                key={v.id}
                direction="row" alignItems="flex-start" spacing={2}
                sx={{ position: "relative", pb: isLast ? 0 : 2.5 }}
              >
                <Box sx={{ position: "relative", flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "center" }}>
                  <Box sx={{
                    width: 22, height: 22, borderRadius: "50%",
                    display: "grid", placeItems: "center", flexShrink: 0,
                    bgcolor: isActive
                      ? ACTIVE_ACCENT
                      : (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.1 : 0.06),
                    color: isActive ? "common.white" : "text.subtitle",
                    zIndex: 1,
                  }}>
                    {isActive ? (
                      <Iconify icon="solar:check-circle-bold" width={14} />
                    ) : (
                      <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "text.subtitle" }} />
                    )}
                  </Box>
                  {!isLast && (
                    <Box sx={{
                      width: 2, flex: 1, minHeight: 30, mt: 0.5,
                      bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06),
                    }} />
                  )}
                </Box>

                <Box flex={1} minWidth={0} sx={{ pt: 0.125 }}>
                  <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" rowGap={0.5}>
                    <Typography sx={{
                      typography: "s2", fontWeight: "fontWeightBold", color: "text.primary",
                      fontFamily: "ui-monospace, Menlo, monospace",
                    }}>
                      {v.label}
                    </Typography>
                    {isActive && (
                      <Box sx={{
                        px: 0.75, py: 0.125, borderRadius: 0.5,
                        bgcolor: (t) => alpha(ACTIVE_ACCENT, t.palette.mode === "dark" ? 0.18 : 0.12),
                        color: ACTIVE_ACCENT,
                        typography: "s3", fontWeight: "fontWeightBold", letterSpacing: 0.3,
                      }}>
                        {AGENT_CARD_COPY.active}
                      </Box>
                    )}
                    {v.connectedAt && (
                      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                        {new Date(v.connectedAt).toLocaleString(undefined, TIMESTAMP_FORMAT)}
                      </Typography>
                    )}
                    <Box flex={1} />
                    {!isActive && (
                      <Tooltip arrow title={locked ? LOCK_TOOLTIP : ""}>
                        <Box component="span" sx={{ display: "inline-flex" }}>
                          <Button
                            size="small"
                            disabled={locked}
                            onClick={() => onSetActiveVersion?.(v.id)}
                            startIcon={<Iconify icon={isOlderThanActive ? "solar:rewind-back-linear" : "solar:arrow-up-linear"} width={13} />}
                            sx={{
                              typography: "s3", fontWeight: "fontWeightBold", color: "text.secondary",
                              "&:hover": { color: "text.primary" },
                            }}
                          >
                            {isOlderThanActive ? AGENT_CARD_COPY.rollBack : AGENT_CARD_COPY.setActive}
                          </Button>
                        </Box>
                      </Tooltip>
                    )}
                  </Stack>
                  {v.note && v.note !== "Environment source" && (
                    <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.375 }}>
                      {v.note}
                    </Typography>
                  )}
                </Box>
              </Stack>
            );
          })}
        </Stack>
      </Box>
    </Box>
  );
}
VersionHistoryCard.propTypes = {
  agent: AGENT_SHAPE.isRequired,
  onSetActiveVersion: PropTypes.func.isRequired,
  locked: PropTypes.bool,
};
