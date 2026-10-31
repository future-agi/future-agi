import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { Box, Stack, Typography, Button, Tab } from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { CustomTabs } from "src/components/tabs/tabs";
import SideDrawer from "../../../../components/SideDrawer";
import {
  useDebugAnalysis,
  useRunCalls,
  withCallContext,
} from "src/api/simulate-environments/runDetail";
import DiagnosisPane from "./DiagnosisPane";
import ImaginePane from "./ImaginePane";
import BetaChip from "./BetaChip";
import {
  SELF_IMPROVEMENT_BETA_HINT,
  useSelfImprovementOpen,
} from "./selfImprovement";

/**
 * Fix my agent — the Debug-failures drawer.
 *
 * Opened by the run header's "Debug failures" button. Opening it requests the
 * run's Omega diagnosis once (`debug-analysis`); the DiagnosisPane shows the
 * issues it found and hands their calls back to the table. A second "Imagine"
 * tab is present but gated (no backend).
 *
 * The drawer uses the shared `SideDrawer`, which already paints the single
 * close-X — so this content deliberately does NOT add its own, or the two would
 * stack in the same corner.
 */
// The API refuses a run whose calls are still in flight; anything else is a
// generic failure — raw backend text is not user copy.
function refusalMessage(error) {
  return error?.code === "execution_not_completed"
    ? "Diagnosis is available once every call in this run finishes."
    : null;
}

export default function FixMyAgentDrawer({
  open,
  onClose,
  executionId,
  stats,
  onLaunch,
  onViewCalls,
}) {
  const [tab, setTab] = useState("diagnosis");
  const { analysis, isLoading, request, isRequesting, requestError } =
    useDebugAnalysis(open ? executionId : null);

  // The first open of a finished run queues its diagnosis; later opens read it.
  const requested = useRef(null);
  useEffect(() => {
    if (!open || isLoading || analysis.status !== "not_requested") return;
    if (requested.current === executionId) return;
    requested.current = executionId;
    request();
  }, [open, isLoading, analysis.status, executionId, request]);

  // One-offs carry Omega's own prose; their calls give its call ids readable labels.
  const citedIds = useMemo(
    () => [...new Set(analysis.oneOffs.flatMap((way) => way.callIds))],
    [analysis.oneOffs],
  );
  const { tasks: citedCalls } = useRunCalls(
    citedIds.length ? executionId : null,
    {
      limit: 500,
      filters: { call_execution_id: citedIds },
    },
  );

  // A refused request (e.g. calls still running) must read as an error, never an
  // endless loader.
  const view = useMemo(() => {
    const base =
      requestError && analysis.status === "not_requested"
        ? {
            ...analysis,
            status: "failed",
            errorMessage: refusalMessage(requestError),
          }
        : analysis;
    return withCallContext(base, citedCalls);
  }, [analysis, requestError, citedCalls]);

  // Once diagnosed, the header counts what the diagnosis counts, so they agree.
  const failing = view.summary?.brokenCalls ?? stats?.failed ?? 0;
  const measured =
    view.summary?.measuredCalls ?? stats?.measured ?? stats?.total ?? 0;

  // The primary path out of the diagnosis: launch a real optimization. Shown once
  // the diagnosis has finished so the very first optimization is reachable here
  // (the Trials tab, where past runs live, is hidden until one exists).
  const selfImprovementOpen = useSelfImprovementOpen();
  const canLaunch =
    tab === "diagnosis" &&
    view.status === "completed" &&
    view.goals.length + view.oneOffs.length > 0;

  return (
    <SideDrawer open={open} onClose={onClose} width={{ xs: "100%", sm: 570 }}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        {/* Header — the drawer's close-X is the SideDrawer's, not ours. */}
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{
            px: 2.5,
            py: 2,
            pr: 5,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Box
            sx={{
              width: 30,
              height: 30,
              borderRadius: "8px",
              display: "grid",
              placeItems: "center",
              flexShrink: 0,
              color: "status.discovery.fg",
              bgcolor: "status.discovery.bg",
            }}
          >
            <Iconify icon="solar:magic-stick-3-linear" width={16} />
          </Box>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 500 }}>
              Debug failures
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {failing} failing of {measured} measured
            </Typography>
          </Box>
        </Stack>

        {/* Tab bar — Diagnosis (real) and Imagine (gated). */}
        <CustomTabs
          value={tab}
          onChange={(_, v) => setTab(v)}
          sx={{ boxShadow: "none", borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Tab
            value="diagnosis"
            label="Diagnosis"
            icon={<Iconify icon="solar:document-medicine-linear" width={14} />}
            iconPosition="start"
          />
          <Tab
            value="imagine"
            label="Imagine"
            icon={<Iconify icon="solar:magic-stick-3-linear" width={14} />}
            iconPosition="start"
          />
        </CustomTabs>

        {tab === "diagnosis" ? (
          <DiagnosisPane
            analysis={view}
            isLoading={isLoading}
            isRequesting={isRequesting}
            onRetry={() => request()}
            onViewCalls={onViewCalls}
          />
        ) : (
          <ImaginePane />
        )}

        {canLaunch && (
          <Box
            sx={{
              px: 2.5,
              py: 2,
              borderTop: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <CustomTooltip
              show={!selfImprovementOpen}
              title={SELF_IMPROVEMENT_BETA_HINT}
              size="small"
              arrow
            >
              {/* A disabled button fires no hover events; the box carries the tooltip. */}
              <Box>
                <Button
                  fullWidth
                  variant="contained"
                  color="primary"
                  disabled={!selfImprovementOpen}
                  onClick={onLaunch}
                  startIcon={
                    <Iconify icon="solar:magic-stick-3-bold" width={16} />
                  }
                >
                  Run self improvement
                  {!selfImprovementOpen && <BetaChip />}
                </Button>
              </Box>
            </CustomTooltip>
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
  onViewCalls: PropTypes.func,
};
