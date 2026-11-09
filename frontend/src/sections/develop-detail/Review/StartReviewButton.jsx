import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  Button,
  Divider,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useExperimentReviewQueues } from "src/api/develop/review";
import StartReviewDialog from "./StartReviewDialog";
import { matchingReviewQueues, reviewPageUrl } from "./reviewModel";

/**
 * `Review ▾` (§3.12): Pairwise… / Rubric… open StartReviewDialog prefilled
 * with the runs in view (A = baseline run, B = first candidate); Continue my
 * review opens the review page of the queue that already reviews these runs.
 * `initialMode` (from `&review=`) opens the dialog once on mount.
 */
const StartReviewButton = ({
  datasetId,
  runIds = [],
  baselineId,
  filters = [],
  initialMode,
  startHere,
  buttonProps,
}) => {
  const navigate = useNavigate();
  const location = useLocation();
  const [, setSearchParams] = useSearchParams();
  const anchor = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialog, setDialog] = useState(null);
  const runA =
    baselineId && runIds.includes(baselineId) ? baselineId : runIds[0];
  const runB = runIds.find((id) => id !== runA) ?? null;
  const canPair = Boolean(runA && runB);

  const queuesQ = useExperimentReviewQueues(datasetId);
  const continueQueue = useMemo(() => {
    const queues = queuesQ.data ?? [];
    const pair = canPair
      ? matchingReviewQueues(queues, { mode: "pairwise", runA, runB })
      : [];
    const rubric = matchingReviewQueues(queues, {
      mode: "rubric",
      runId: runB ?? runA,
    });
    return (
      [...pair, ...rubric].find(
        (q) => (q.item_count ?? 0) > (q.completed_count ?? 0),
      ) ?? null
    );
  }, [queuesQ.data, canPair, runA, runB]);

  // `&review=pairwise|rubric` transfer: open once, then drop the param.
  const opened = useRef(false);
  useEffect(() => {
    if (!initialMode || opened.current || !runA) return;
    opened.current = true;
    setDialog(initialMode === "pairwise" && !canPair ? "rubric" : initialMode);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("review");
        return next;
      },
      { replace: true },
    );
  }, [initialMode, runA, canPair, setSearchParams]);

  const here = `${location.pathname}${location.search}`;
  const open = (mode) => {
    setMenuOpen(false);
    setDialog(mode);
  };

  return (
    <>
      <Button
        ref={anchor}
        variant="outlined"
        size="medium"
        onClick={() => setMenuOpen(true)}
        startIcon={<Iconify icon="mdi:account-check-outline" width={16} />}
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        sx={{ padding: "8px 12px" }}
        disabled={!runA}
        aria-haspopup="menu"
        {...buttonProps}
      >
        <Typography typography="s1" fontWeight="fontWeightMedium">
          Review
        </Typography>
      </Button>
      <Menu
        anchorEl={anchor.current}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 280, maxWidth: 380 } } }}
      >
        {startHere ? (
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{ px: 2, pt: 0.5, pb: 0.25, fontWeight: 600 }}
          >
            Start here
          </Typography>
        ) : null}
        {startHere ? (
          <MenuItem
            disabled={Boolean(startHere.disabledReason) || startHere.busy}
            onClick={() => {
              setMenuOpen(false);
              startHere.onClick?.();
            }}
            sx={{ alignItems: "flex-start" }}
          >
            <ListItemIcon sx={{ mt: 0.25 }}>
              <Iconify
                icon={
                  startHere.busy
                    ? "mdi:loading"
                    : "mdi:arrow-down-bold-circle-outline"
                }
                width={18}
                sx={{ color: "primary.main" }}
              />
            </ListItemIcon>
            <ListItemText
              primary={startHere.label}
              secondary={startHere.disabledReason ?? startHere.secondary}
              primaryTypographyProps={{
                typography: "s1",
                fontWeight: "fontWeightSemiBold",
                color: "primary.main",
              }}
              secondaryTypographyProps={{
                typography: "s3",
                sx: { whiteSpace: "normal" },
              }}
            />
          </MenuItem>
        ) : null}
        {startHere ? <Divider /> : null}
        {startHere ? (
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{ px: 2, pt: 0.5, pb: 0.25, fontWeight: 600 }}
          >
            Other ways
          </Typography>
        ) : null}
        <MenuItem onClick={() => open("pairwise")}>
          <ListItemIcon>
            <Iconify icon="mdi:compare-horizontal" width={18} />
          </ListItemIcon>
          <ListItemText
            primary="Pairwise…"
            secondary={
              canPair
                ? "Which of two runs is better, row by row"
                : "Pick the run to compare against in the next step"
            }
            primaryTypographyProps={{ typography: "s1" }}
            secondaryTypographyProps={{ typography: "s3" }}
          />
        </MenuItem>
        <MenuItem onClick={() => open("rubric")}>
          <ListItemIcon>
            <Iconify icon="mdi:star-outline" width={18} />
          </ListItemIcon>
          <ListItemText
            primary="Rubric…"
            secondary="Rate one run's outputs with labels"
            primaryTypographyProps={{ typography: "s1" }}
            secondaryTypographyProps={{ typography: "s3" }}
          />
        </MenuItem>
        {continueQueue ? <Divider /> : null}
        {continueQueue ? (
          <MenuItem
            onClick={() => {
              setMenuOpen(false);
              navigate(
                reviewPageUrl(datasetId, continueQueue.id, { from: here }),
              );
            }}
          >
            <ListItemIcon>
              <Iconify icon="mdi:play-circle-outline" width={18} />
            </ListItemIcon>
            <ListItemText
              primary="Continue my review"
              secondary={`${continueQueue.name} · ${Math.max(0, (continueQueue.item_count ?? 0) - (continueQueue.completed_count ?? 0))} left`}
              primaryTypographyProps={{ typography: "s1" }}
              secondaryTypographyProps={{ typography: "s3", noWrap: true }}
            />
          </MenuItem>
        ) : null}
      </Menu>
      <StartReviewDialog
        open={Boolean(dialog)}
        onClose={() => setDialog(null)}
        datasetId={datasetId}
        mode={dialog ?? "pairwise"}
        runA={runA}
        runB={dialog === "rubric" ? runB ?? runA : runB ?? undefined}
        filters={filters}
        returnTo={here}
      />
    </>
  );
};

StartReviewButton.propTypes = {
  datasetId: PropTypes.string,
  // run ids in view; the first is the baseline (run A)
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
  // current results filters (pill JSON) for the "Rows" source
  filters: PropTypes.array,
  // opens the dialog on mount in this mode (`review=pairwise|rubric`)
  initialMode: PropTypes.oneOf(["pairwise", "rubric"]),
  // R3: the suggested first review, shown first in the menu
  // `{label, secondary?, disabledReason?, busy?, onClick}`
  startHere: PropTypes.object,
  // R3: extra props for the trigger button (aria, tooltip anchor)
  buttonProps: PropTypes.object,
};

export default StartReviewButton;
