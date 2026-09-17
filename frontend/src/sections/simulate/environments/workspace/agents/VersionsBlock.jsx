import PropTypes from "prop-types";
import { Box, Stack, Typography, Button } from "@mui/material";

import Iconify from "src/components/iconify";

import { AGENT_VERSION_SHAPE } from "./agents.shapes";
import { SOURCE_ACCENT, AGENT_CARD_COPY } from "./agentCards.constants";
import { ChipButton } from "./agentPrimitives";

const TIMESTAMP_FORMAT = {
  month: "short", day: "numeric", year: "numeric",
  hour: "2-digit", minute: "2-digit",
};

// The version stack for one agent, rendered as a compact timeline: each row is
// one version — its label, when it was connected, the note carried when it was
// added — with a radio-style active indicator on the left and a "Set active"
// text action on the right for non-active rows. A "New version" button sits at
// the bottom. Active-version selection (which build of THIS agent to run) is a
// local decision, separate from active-for-runs (which agent to run).
export default function VersionsBlock({ versions, activeVersionId, onAddVersion, onSetActiveVersion }) {
  const list = versions?.length ? versions : [];
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.75 }}>
        <Iconify icon="solar:layers-minimalistic-linear" width={13} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", letterSpacing: 0.5, fontWeight: "fontWeightBold" }}>
          {AGENT_CARD_COPY.versions.toUpperCase()}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
          ({list.length})
        </Typography>
      </Stack>
      <Box sx={{
        borderRadius: 1.25, border: "1px solid", borderColor: "divider",
        bgcolor: "background.neutral", overflow: "hidden",
      }}>
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {list.length === 0 ? (
            <Typography sx={{ typography: "s3", color: "text.subtitle", p: 1.25 }}>
              {AGENT_CARD_COPY.noVersions}
            </Typography>
          ) : (
            list.slice().reverse().map((v) => {
              const isActive = v.id === activeVersionId;
              return (
                <Stack
                  key={v.id}
                  direction="row" alignItems="center" spacing={1.25}
                  sx={{ px: 1.5, py: 1 }}
                >
                  <Iconify
                    icon={isActive ? "solar:record-circle-bold" : "solar:circle-linear"}
                    width={15}
                    sx={{ color: isActive ? SOURCE_ACCENT : "text.subtitle", flexShrink: 0 }}
                  />
                  <Box flex={1} minWidth={0}>
                    <Stack direction="row" alignItems="center" spacing={0.75} flexWrap="wrap" rowGap={0.25}>
                      <Typography sx={{
                        typography: "s3", fontWeight: "fontWeightBold", color: "text.primary",
                        fontFamily: "ui-monospace, Menlo, monospace",
                      }}>
                        {v.label}
                      </Typography>
                      {v.connectedAt && (
                        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                          · {new Date(v.connectedAt).toLocaleString(undefined, TIMESTAMP_FORMAT)}
                        </Typography>
                      )}
                    </Stack>
                    {v.note && v.note !== "Environment source" && (
                      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                        {v.note}
                      </Typography>
                    )}
                  </Box>
                  {!isActive && (
                    <Button
                      size="small"
                      onClick={() => onSetActiveVersion?.(v.id)}
                      sx={{ typography: "s3", fontWeight: "fontWeightBold", color: "text.secondary", minWidth: 0 }}
                    >
                      {AGENT_CARD_COPY.setActive}
                    </Button>
                  )}
                </Stack>
              );
            })
          )}
        </Stack>
      </Box>
      <Stack direction="row" justifyContent="flex-end" sx={{ mt: 1 }}>
        <ChipButton
          icon="solar:add-circle-linear"
          label={AGENT_CARD_COPY.newVersion}
          onClick={() => onAddVersion?.()}
        />
      </Stack>
    </Box>
  );
}
VersionsBlock.propTypes = {
  versions: PropTypes.arrayOf(AGENT_VERSION_SHAPE),
  activeVersionId: PropTypes.string,
  onAddVersion: PropTypes.func,
  onSetActiveVersion: PropTypes.func,
};
