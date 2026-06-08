import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import GlobalStyles from "@mui/material/GlobalStyles";
import Paper from "@mui/material/Paper";
import Popper from "@mui/material/Popper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import {
  destinationTourStorageIdentity,
  dismissDestinationTourAnchor,
  isDestinationTourReplay,
  readDestinationTourDismissals,
  resetDestinationTourAnchorDismissal,
} from "./destinationTourDismissal";
import {
  destinationTourCopyForStep,
  destinationTourProgressForStep,
} from "./destinationTourAnchorConfig";

const findTourTarget = (anchor) => {
  if (!anchor) return null;
  const byTourAnchor = Array.from(
    document.querySelectorAll("[data-tour-anchor]"),
  ).find((item) => item.getAttribute("data-tour-anchor") === anchor);
  if (byTourAnchor) return byTourAnchor;

  const byTestId = Array.from(document.querySelectorAll("[data-testid]")).find(
    (item) => item.getAttribute("data-testid") === anchor,
  );
  if (byTestId) return byTestId;

  return document.getElementById(anchor);
};

const tourHomeHref = ({ journeyStep, searchParams, source, tourAnchor }) => {
  const homeParams = new URLSearchParams({ source });
  if (journeyStep) homeParams.set("journey_step", journeyStep);
  if (tourAnchor) homeParams.set("tour_anchor", tourAnchor);

  ["quick_start_goal", "quick_start_id", "quick_start_primary_path"].forEach(
    (key) => {
      const value = searchParams.get(key);
      if (value) homeParams.set(key, value);
    },
  );

  return `/dashboard/home?${homeParams.toString()}`;
};

export default function DestinationTourAnchor({ maxAttempts = 12 }) {
  const [searchParams] = useSearchParams();
  const tourAnchor = searchParams.get("tour_anchor");
  const journeyStep = searchParams.get("journey_step");
  const isReplay = isDestinationTourReplay(searchParams);
  const storageIdentity = destinationTourStorageIdentity();
  const [targetEl, setTargetEl] = useState(null);
  const [targetMissing, setTargetMissing] = useState(false);
  const [retryAttempt, setRetryAttempt] = useState(0);
  const [dismissedAnchors, setDismissedAnchors] = useState(() =>
    readDestinationTourDismissals({ identity: storageIdentity }),
  );

  const copy = useMemo(
    () => destinationTourCopyForStep(journeyStep),
    [journeyStep],
  );
  const progress = useMemo(
    () => destinationTourProgressForStep({ journeyStep, tourAnchor }),
    [journeyStep, tourAnchor],
  );
  const homeHref = useMemo(
    () =>
      tourHomeHref({
        journeyStep,
        searchParams,
        source: "destination_tour_plan",
        tourAnchor,
      }),
    [journeyStep, searchParams, tourAnchor],
  );
  const hidden = !tourAnchor || (!isReplay && dismissedAnchors.has(tourAnchor));

  useEffect(() => {
    const nextDismissals =
      isReplay && tourAnchor
        ? resetDestinationTourAnchorDismissal({
            anchor: tourAnchor,
            identity: storageIdentity,
          })
        : readDestinationTourDismissals({ identity: storageIdentity });
    setDismissedAnchors(nextDismissals);
  }, [isReplay, storageIdentity, tourAnchor]);

  useEffect(() => {
    setTargetEl(null);
    setTargetMissing(false);
    if (hidden) return undefined;

    let cancelled = false;
    let attempt = 0;
    let timeoutId;

    const resolveTarget = () => {
      if (cancelled) return;
      const nextTarget = findTourTarget(tourAnchor);
      if (nextTarget) {
        setTargetEl(nextTarget);
        nextTarget.scrollIntoView?.({ block: "center", behavior: "smooth" });
        return;
      }
      attempt += 1;
      if (attempt < maxAttempts) {
        timeoutId = window.setTimeout(resolveTarget, 150);
        return;
      }
      setTargetMissing(true);
    };

    resolveTarget();

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [hidden, maxAttempts, retryAttempt, tourAnchor]);

  useEffect(() => {
    if (!targetEl || hidden) return undefined;
    targetEl.setAttribute("data-onboarding-tour-active", "true");
    return () => {
      targetEl.removeAttribute("data-onboarding-tour-active");
    };
  }, [hidden, targetEl]);

  if (hidden) {
    return null;
  }

  if (!targetEl && targetMissing) {
    const fallbackHref = tourHomeHref({
      journeyStep,
      searchParams,
      source: "destination_tour_fallback",
      tourAnchor,
    });

    return (
      <Paper
        data-testid="destination-tour-missing-anchor"
        elevation={6}
        sx={{
          position: "fixed",
          right: { xs: 12, sm: 20 },
          bottom: { xs: 12, sm: 20 },
          zIndex: (theme) => theme.zIndex.modal + 1,
          border: "1px solid",
          borderColor: "primary.main",
          borderRadius: 1,
          maxWidth: { xs: "calc(100vw - 24px)", sm: 360 },
          p: 1.5,
        }}
      >
        <Stack spacing={1}>
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Chip
              size="small"
              color="primary"
              label={
                progress
                  ? `Step ${progress.stepNumber} of ${progress.stepCount}`
                  : "Current step"
              }
            />
            <Typography variant="subtitle2">{copy.label}</Typography>
          </Stack>
          {progress ? (
            <Typography variant="caption" color="text.secondary">
              {progress.planTitle}
            </Typography>
          ) : null}
          <Typography variant="body2" color="text.secondary">
            {copy.description}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            This page changed or is still loading. Return to Home for the latest
            step, or try finding the action again.
          </Typography>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
            <Button
              size="small"
              variant="contained"
              component={RouterLink}
              href={fallbackHref}
              startIcon={<Iconify icon="mdi:home-outline" width={16} />}
            >
              Back to Home
            </Button>
            <Button
              size="small"
              variant="text"
              onClick={() => setRetryAttempt((current) => current + 1)}
              startIcon={<Iconify icon="mdi:refresh" width={16} />}
            >
              Try again
            </Button>
            <Button
              size="small"
              variant="text"
              onClick={() =>
                setDismissedAnchors(
                  dismissDestinationTourAnchor({
                    anchor: tourAnchor,
                    identity: storageIdentity,
                  }),
                )
              }
            >
              Dismiss
            </Button>
          </Stack>
        </Stack>
      </Paper>
    );
  }

  if (!targetEl) {
    return null;
  }

  return (
    <>
      <GlobalStyles
        styles={(theme) => ({
          '[data-onboarding-tour-active="true"]': {
            position: "relative",
            outline: `2px solid ${theme.palette.primary.main}`,
            outlineOffset: 4,
            boxShadow: `0 0 0 6px ${alpha(theme.palette.primary.main, 0.14)}`,
            borderRadius: 8,
            zIndex: theme.zIndex.drawer + 1,
          },
        })}
      />
      <Popper
        open
        anchorEl={targetEl}
        placement="bottom-start"
        modifiers={[
          { name: "offset", options: { offset: [0, 10] } },
          { name: "preventOverflow", options: { padding: 12 } },
        ]}
        sx={{ zIndex: (theme) => theme.zIndex.modal + 1 }}
      >
        <Paper
          data-testid="destination-tour-anchor"
          elevation={6}
          sx={{
            border: "1px solid",
            borderColor: "primary.main",
            borderRadius: 1,
            maxWidth: 320,
            p: 1.25,
          }}
        >
          <Stack spacing={1}>
            <Stack direction="row" spacing={0.75} alignItems="center">
              <Chip
                size="small"
                color="primary"
                label={
                  progress
                    ? `Step ${progress.stepNumber} of ${progress.stepCount}`
                    : "Current step"
                }
              />
              <Typography variant="subtitle2">{copy.label}</Typography>
            </Stack>
            {progress ? (
              <Typography variant="caption" color="text.secondary">
                {progress.planTitle}
                {progress.nextLabel ? ` - Next: ${progress.nextLabel}` : ""}
              </Typography>
            ) : null}
            <Typography variant="body2" color="text.secondary">
              {copy.description}
            </Typography>
            <Box>
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Button
                  size="small"
                  variant="text"
                  onClick={() =>
                    setDismissedAnchors(
                      dismissDestinationTourAnchor({
                        anchor: tourAnchor,
                        identity: storageIdentity,
                      }),
                    )
                  }
                  startIcon={<Iconify icon="mdi:check" width={16} />}
                >
                  Got it
                </Button>
                <Button
                  size="small"
                  variant="text"
                  component={RouterLink}
                  href={homeHref}
                  startIcon={<Iconify icon="mdi:map-outline" width={16} />}
                >
                  View plan
                </Button>
              </Stack>
            </Box>
          </Stack>
        </Paper>
      </Popper>
    </>
  );
}

DestinationTourAnchor.propTypes = {
  maxAttempts: PropTypes.number,
};
