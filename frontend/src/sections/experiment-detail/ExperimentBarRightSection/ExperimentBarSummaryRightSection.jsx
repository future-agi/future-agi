import { compareFileBase } from "src/sections/common/Runs/runLabel";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { useSearchParams } from "react-router-dom";
import { enqueueSnackbar } from "src/components/snackbar";
import axios, { endpoints } from "src/utils/axios";
import { useExperimentDetailContext } from "../experiment-context";
import { trackEvent, Events } from "src/utils/Mixpanel";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import BaselinePicker from "src/sections/common/Runs/BaselinePicker";
import RunPickerPopover from "src/sections/common/Runs/RunPickerPopover";
import StartReviewButton from "src/sections/develop-detail/Review/StartReviewButton";
import { NO_REVIEW_FILTER } from "../reviewDoor";
import { compareSummaryHref } from "src/sections/common/Analysis/analysisLinks";
import { analysisCsv } from "src/sections/common/Analysis/analysisCsv";
import {
  analysisQuestions,
  openFalconAnalysis,
} from "src/sections/falcon-ai/helpers/openFalconAnalysis";
import { ResultsExportDialog } from "src/sections/common/export/ExportDialog";

const VIEWER_TIP = "You need edit access to this dataset";

export const SEGMENTED_SX = {
  "& .MuiToggleButton-root": {
    px: 1.25,
    py: 0.5,
    typography: "s2",
    fontWeight: 500,
    textTransform: "none",
    color: "text.secondary",
    gap: 0.5,
  },
  "& .Mui-selected": {
    color: "primary.main !important",
    bgcolor: (t) => `${t.palette.primary.main}14 !important`,
  },
};

/**
 * `Overview | Workbench` (R2-SPEC §6.2): writes `view=workbench` (Overview
 * drops it, so the parity URL stays byte-identical).
 */
export function SummaryViewToggle() {
  const [searchParams, setSearchParams] = useSearchParams();
  const value =
    searchParams.get("view") === "workbench" ? "workbench" : "overview";
  return (
    <ToggleButtonGroup
      exclusive
      size="small"
      value={value}
      aria-label="Summary view"
      onChange={(_, v) => {
        if (!v || v === value) return;
        setSearchParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            if (v === "workbench") next.set("view", "workbench");
            else next.delete("view");
            return next;
          },
          { replace: false },
        );
      }}
      sx={SEGMENTED_SX}
    >
      <ToggleButton value="overview">
        <Iconify icon="mdi:view-dashboard-outline" width={16} />
        Overview
      </ToggleButton>
      <ToggleButton value="workbench">
        <Iconify icon="mdi:table-pivot" width={16} />
        Workbench
      </ToggleButton>
    </ToggleButtonGroup>
  );
}

function saveBlob(blob, name) {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.setAttribute("download", name);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

/** `Ask Falcon ▾`: three questions about the comparison, or your own. */
function AskFalconButton({ questions, context }) {
  const [anchor, setAnchor] = useState(null);
  const ask = (question) => {
    setAnchor(null);
    openFalconAnalysis({ question, context });
  };
  return (
    <>
      <Button
        variant="outlined"
        startIcon={<Iconify icon="mdi:creation-outline" />}
        endIcon={<Iconify icon="mdi:chevron-down" width={18} />}
        onClick={(e) => setAnchor(e.currentTarget)}
      >
        Ask Falcon
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 340 } } }}
      >
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ px: 2, pt: 0.5, pb: 1 }}
        >
          Falcon answers from the comparison on screen.
        </Typography>
        {questions.map((q) => (
          <MenuItem key={q} onClick={() => ask(q)}>
            <ListItemIcon>
              <Iconify icon="mdi:message-question-outline" width={18} />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s1" }}>
              {q}
            </ListItemText>
          </MenuItem>
        ))}
        <Divider />
        <MenuItem onClick={() => ask("")}>
          <ListItemIcon>
            <Iconify icon="mdi:pencil-outline" width={18} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ typography: "s1" }}>
            Ask your own question
          </ListItemText>
        </MenuItem>
      </Menu>
    </>
  );
}

AskFalconButton.propTypes = {
  questions: PropTypes.arrayOf(PropTypes.string).isRequired,
  context: PropTypes.object.isRequired,
};

/**
 * The Summary toolbar (PRODUCT-SPEC §3.13): baseline picker (or, on a
 * one-run experiment, `Compare with dataset baseline #1A` and `Compare
 * with…`) · Ask Falcon · Review ▾ · Choose winner (experiment page) ·
 * Download CSV. Called with no props (individual-experiment Summary) it
 * keeps its old Download CSV + Choose winner pair.
 */
const ExperimentBarSummaryRightSection = ({
  mode,
  datasetId,
  analysis,
  runIds = [],
  baselineId,
  onBaselineChange,
  filters,
  canEdit = true,
}) => {
  const { experimentId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const canDownloadExperiment = Boolean(experimentId);
  const downloadInFlightRef = useRef(false);
  const [isDownloading, setIsDownloading] = useState(false);
  // W3-6 B3: `Download…` the rows of this comparison (CSV / JSON / JSONL).
  const [exportOpen, setExportOpen] = useState(false);
  const { setChooseWinnerOpen } = useExperimentDetailContext();
  const enhanced = Boolean(mode);
  const runs = analysis?.runs ?? [];
  const single = enhanced && runIds.length === 1;
  const here = `${location.pathname}${location.search}`;

  const { mutate: downloadExperiment } = useMutation({
    mutationFn: () =>
      axios.get(endpoints.develop.experiment.downloadExperiment(experimentId), {
        responseType: "blob",
      }),
    onMutate: () => {
      setIsDownloading(true);
    },
    onSuccess: (response) => {
      saveBlob(new Blob([response.data]), `experiment-${experimentId}.csv`);
      enqueueSnackbar("Experiment downloaded successfully", {
        variant: "success",
      });
    },
    onError: () => {
      enqueueSnackbar("Failed to download experiment", {
        variant: "error",
      });
    },
    onSettled: () => {
      downloadInFlightRef.current = false;
      setIsDownloading(false);
    },
    meta: { errorHandled: true },
  });

  const handleDownload = () => {
    if (mode === "compare") {
      if (!analysis) return;
      saveBlob(
        new Blob([analysisCsv(analysis)], { type: "text/csv" }),
        `${compareFileBase(runs.map((r) => r.label))}.csv`,
      );
      enqueueSnackbar("Comparison downloaded", { variant: "success" });
      return;
    }
    if (!experimentId || downloadInFlightRef.current) return;
    downloadInFlightRef.current = true;
    trackEvent(Events.expDownloadClicked);
    downloadExperiment();
  };

  const datasetBaseline = analysis?.dataset_baseline;
  const onlyRun = single ? runs[0] : null;
  const candidate = runs.find((r) => r.run_id === analysis?.candidate_run_id);
  const worklistMetric = analysis?.metrics?.find(
    (m) => m.key === analysis?.worklist?.metric,
  );
  const questions = analysisQuestions({
    candidateLabel: candidate?.label,
    regressedMetricName: worklistMetric?.name,
  });
  const falconContext = {
    datasetId,
    experimentId: mode === "experiment" ? experimentId : undefined,
    runs,
    baselineId: analysis?.baseline_run_id,
    filters,
    url: here,
  };

  const downloadButton = (enhanced
    ? mode === "compare" || canDownloadExperiment
    : canDownloadExperiment) && (
    <Button
      variant="outlined"
      startIcon={
        isDownloading ? (
          <CircularProgress color="inherit" size={16} />
        ) : (
          <Iconify icon="material-symbols:download" />
        )
      }
      disabled={isDownloading || (enhanced && !analysis)}
      onClick={handleDownload}
    >
      {isDownloading ? "Downloading…" : "Download CSV"}
    </Button>
  );

  const winnerBlocked = !canEdit
    ? VIEWER_TIP
    : enhanced && runIds.length < 2
      ? "Choose winner ranks two or more runs of this experiment"
      : null;
  const chooseWinner = (!enhanced || mode === "experiment") && (
    <CustomTooltip
      show={Boolean(winnerBlocked)}
      size="small"
      placement="top"
      arrow
      title={winnerBlocked ?? ""}
    >
      <span>
        <Button
          variant="contained"
          color="primary"
          disabled={Boolean(winnerBlocked)}
          startIcon={<Iconify icon="mdi:crown-outline" />}
          onClick={() => {
            trackEvent(Events.expWinnerClick);
            setChooseWinnerOpen(true);
          }}
        >
          Choose winner
        </Button>
      </span>
    </CustomTooltip>
  );

  if (!enhanced) {
    return (
      <Box display="flex" alignItems="center" gap={1.5}>
        {downloadButton}
        {chooseWinner}
      </Box>
    );
  }

  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      gap={1.5}
      flexWrap="wrap"
      sx={{ width: "100%" }}
    >
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        {single ? (
          <>
            <Typography typography="s2" color="text.secondary">
              {onlyRun
                ? `${onlyRun.label} · absolute values`
                : "Absolute values"}
            </Typography>
            {datasetBaseline && datasetBaseline.run_id !== onlyRun?.run_id && (
              <Chip
                size="small"
                variant="outlined"
                icon={<Iconify icon="mdi:pin-outline" width={16} />}
                label={`Compare with dataset baseline ${datasetBaseline.label}`}
                clickable
                onClick={() =>
                  navigate(
                    compareSummaryHref({
                      datasetId,
                      runIds: [datasetBaseline.run_id, onlyRun.run_id],
                      baselineId: datasetBaseline.run_id,
                      from: here,
                    }),
                  )
                }
                sx={{ height: 30, typography: "s2", fontWeight: 500 }}
              />
            )}
            {onlyRun && (
              <RunPickerPopover
                datasetId={datasetId}
                excludeIds={[onlyRun.run_id]}
                groupBySameTarget
                max={8}
                label="Compare with…"
                confirmLabel="Compare"
                onPick={(picked) => {
                  if (!picked?.length) return;
                  navigate(
                    compareSummaryHref({
                      datasetId,
                      runIds: [...picked, onlyRun.run_id],
                      baselineId: picked[0],
                      from: here,
                    }),
                  );
                }}
              />
            )}
          </>
        ) : (
          <>
            <BaselinePicker
              datasetId={datasetId}
              value={baselineId ?? analysis?.baseline_run_id ?? "none"}
              pinnedRunId={datasetBaseline?.run_id ?? null}
              runsInView={runs}
              restrictTo={mode === "experiment" ? runIds : undefined}
              allowNone
              onChange={(id) => onBaselineChange?.(id)}
            />
          </>
        )}
      </Stack>
      <Stack direction="row" alignItems="center" gap={1.5}>
        <AskFalconButton questions={questions} context={falconContext} />
        <CustomTooltip
          show={!canEdit}
          size="small"
          placement="top"
          arrow
          title={VIEWER_TIP}
        >
          <span>
            <StartReviewButton
              datasetId={datasetId}
              runIds={runIds}
              baselineId={analysis?.baseline_run_id ?? undefined}
              // L7c g6 (grid:review-rows, HIDE): no `Current filter`
              // source from this door until g8's dialog counts it right.
              filters={NO_REVIEW_FILTER}
            />
          </span>
        </CustomTooltip>
        {chooseWinner}
        {downloadButton}
        {datasetId && runIds.length > 0 && (
          <Button
            variant="outlined"
            startIcon={<Iconify icon="material-symbols:download" />}
            onClick={() => setExportOpen(true)}
          >
            Download…
          </Button>
        )}
      </Stack>
      <ResultsExportDialog
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        datasetId={datasetId}
        view={{
          runIds,
          baselineId: analysis?.baseline_run_id ?? baselineId ?? undefined,
          pills: filters ?? [],
          show: "all",
          search: "",
          sort: "order",
        }}
      />
    </Stack>
  );
};

ExperimentBarSummaryRightSection.propTypes = {
  mode: PropTypes.oneOf(["experiment", "compare"]),
  datasetId: PropTypes.string,
  analysis: PropTypes.object,
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
  onBaselineChange: PropTypes.func,
  filters: PropTypes.array,
  canEdit: PropTypes.bool,
};

export default ExperimentBarSummaryRightSection;
