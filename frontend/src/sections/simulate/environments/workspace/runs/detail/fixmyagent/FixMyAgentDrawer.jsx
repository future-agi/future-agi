import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, IconButton, Tooltip, Button } from "@mui/material";

import Iconify from "src/components/iconify";
import SideDrawer from "../../../../components/SideDrawer";
import { BUILD_TONES } from "../../../../buildEnvironment/buildTones";
import { useOptimizerAnalysis } from "src/api/simulate-environments/runDetail";
import DiagnosisPane from "./DiagnosisPane";
import ImaginePane from "./ImaginePane";

/**
 * Fix my agent — the Debug-failures drawer.
 *
 * Opened by the run header's "Debug failures" button. Its primary content is the
 * DiagnosisPane, sourced from the REAL `optimiser-analysis` endpoint via
 * `useOptimizerAnalysis`. A second "Imagine" tab is present but gated (no
 * backend), matching the designer's two-tab shape without fabricating its
 * exploratory analytics.
 *
 * The drawer uses the shared `SideDrawer`, which already paints the single
 * close-X — so this content deliberately does NOT add its own, or the two would
 * stack in the same corner.
 */
function TabButton({ active, onClick, icon, children }) {
  return (
    <Box
      onClick={onClick}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        py: 1.25,
        cursor: "pointer",
        borderBottom: "2px solid",
        borderColor: active ? "text.primary" : "transparent",
        color: active ? "text.primary" : "text.subtitle",
        "&:hover": { color: "text.primary" },
      }}
    >
      <Iconify icon={icon} width={14} />
      <Typography sx={{ typography: "s2", fontWeight: active ? 700 : 500 }}>{children}</Typography>
    </Box>
  );
}
TabButton.propTypes = {
  active: PropTypes.bool,
  onClick: PropTypes.func,
  icon: PropTypes.string,
  children: PropTypes.node,
};

export default function FixMyAgentDrawer({ open, onClose, executionId, stats, onLaunch }) {
  const [tab, setTab] = useState("diagnosis");
  const { analysis, isLoading, refresh, isRefreshing } = useOptimizerAnalysis(
    open ? executionId : null,
  );

  const failing = stats?.failed ?? 0;
  const measured = stats?.measured ?? stats?.total ?? 0;

  // The primary path out of the diagnosis: launch a real optimization over the
  // findings. Shown once a diagnosis exists so the very first optimization is
  // reachable here (the Trials tab, where past runs live, is hidden until one
  // exists — this button is what creates the first).
  const canLaunch = tab === "diagnosis" && !isLoading && analysis.hasResponse && !analysis.isWorking;

  return (
    <SideDrawer open={open} onClose={onClose} width={{ xs: "100%", sm: 570 }}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        {/* Header — the drawer's close-X is the SideDrawer's, not ours. */}
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{ px: 2.5, py: 2, pr: 5, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Box
            sx={{
              width: 30,
              height: 30,
              borderRadius: 1,
              display: "grid",
              placeItems: "center",
              flexShrink: 0,
              color: BUILD_TONES.accent,
              bgcolor: (t) => alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.16 : 0.1),
            }}
          >
            <Iconify icon="solar:magic-stick-3-linear" width={16} />
          </Box>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s1", fontWeight: 700 }}>Debug failures</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {failing} failing of {measured} measured
            </Typography>
          </Box>
          <Tooltip arrow title="Read the run again">
            <span>
              <IconButton size="small" onClick={() => refresh()} disabled={isRefreshing}>
                <Iconify icon="solar:refresh-linear" width={16} sx={{ color: "text.subtitle" }} />
              </IconButton>
            </span>
          </Tooltip>
        </Stack>

        {/* Tab bar — Diagnosis (real) and Imagine (gated). */}
        <Stack
          direction="row"
          spacing={2}
          sx={{ px: 2.5, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <TabButton active={tab === "diagnosis"} onClick={() => setTab("diagnosis")} icon="solar:document-medicine-linear">
            Diagnosis
          </TabButton>
          <TabButton active={tab === "imagine"} onClick={() => setTab("imagine")} icon="solar:magic-stick-3-linear">
            Imagine
          </TabButton>
        </Stack>

        {tab === "diagnosis" ? (
          <DiagnosisPane analysis={analysis} isLoading={isLoading} />
        ) : (
          <ImaginePane />
        )}

        {canLaunch && (
          <Box sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}>
            <Button
              fullWidth
              variant="contained"
              color="primary"
              onClick={onLaunch}
              startIcon={<Iconify icon="solar:magic-stick-3-bold" width={16} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              Run Self Improvement
            </Button>
          </Box>
        )}
      </Stack>
    </SideDrawer>
  );
}

FixMyAgentDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  executionId: PropTypes.string,
  stats: PropTypes.shape({
    failed: PropTypes.number,
    measured: PropTypes.number,
    total: PropTypes.number,
  }),
  onLaunch: PropTypes.func,
};
