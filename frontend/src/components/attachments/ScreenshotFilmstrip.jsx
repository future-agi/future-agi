import { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  IconButton,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import useAttachmentGrants from "./useAttachmentGrants";
import { ImageZoomDialog, MediaPlaceholder, canRenderImage } from "./AttachmentImage";

// Only this many step buttons are mounted, centred on the selection, so a
// 1,000-step run mounts ~15 buttons and resolves exactly one original.
const WINDOW = 15;
const SETTLE_MS = 150;

const frameRequest = (frame) => ({
  occurrence_id: frame.item.reference.occurrence_id,
  parent: frame.parent,
  representation: "original",
});

function offsetLabel(startTime, traceStart) {
  const start = traceStart ? new Date(traceStart).getTime() : NaN;
  if (!startTime || !Number.isFinite(start)) return null;
  const seconds = (startTime - start) / 1000;
  return `+${seconds < 10 ? seconds.toFixed(2) : seconds.toFixed(1)} s`;
}

/**
 * Chronological computer-use screenshots for one trace. Each frame is its own
 * occurrence (identical pixels stay distinct steps). Only the selected frame's
 * original is resolved, after the selection settles; nothing is prefetched.
 */
export default function ScreenshotFilmstrip({
  projectId,
  frames,
  initialIndex = 0,
  currentSpanId,
  traceStartTime,
  onSelectSpan,
}) {
  const [index, setIndex] = useState(() =>
    Math.min(Math.max(0, initialIndex), Math.max(0, frames.length - 1)),
  );
  const [settled, setSettled] = useState(index);
  const [zoom, setZoom] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setIndex(Math.min(Math.max(0, initialIndex), Math.max(0, frames.length - 1)));
  }, [initialIndex, frames.length]);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(index), SETTLE_MS);
    return () => clearTimeout(timer);
  }, [index]);
  useEffect(() => setFailed(false), [settled]);

  const frame = frames[index];
  const settledFrame = frames[settled];
  const showable =
    settledFrame && settledFrame.item.status === "ready" && canRenderImage(settledFrame.item);
  const request = useMemo(
    () => (settledFrame ? frameRequest(settledFrame) : null),
    [settledFrame],
  );
  const { status, results, retry } = useAttachmentGrants(
    projectId,
    showable ? [request] : [],
    { enabled: Boolean(showable) },
  );
  if (!frame) return null;

  const go = (next) =>
    setIndex(Math.min(Math.max(0, next), frames.length - 1));
  const onKeyDown = (event) => {
    const moves = {
      ArrowLeft: index - 1,
      ArrowRight: index + 1,
      Home: 0,
      End: frames.length - 1,
    };
    if (event.key in moves) {
      event.preventDefault();
      go(moves[event.key]);
    }
  };
  const start = Math.max(
    0,
    Math.min(index - Math.floor(WINDOW / 2), frames.length - WINDOW),
  );
  const visible = frames.slice(start, start + WINDOW);
  const result = results[0];
  const label = `Screenshot ${index + 1} of ${frames.length}`;
  const pending = settled !== index || status === "loading" || status === "idle";
  const offset = offsetLabel(frame.startTime, traceStartTime);

  let viewer;
  if (frame.item.status !== "ready" || !canRenderImage(frame.item))
    viewer = <MediaPlaceholder status={frame.item.status === "ready" ? "unavailable" : frame.item.status} height={240} />;
  else if (pending) viewer = <Skeleton variant="rectangular" height={240} />;
  else if (status !== "ready" || result?.status !== "ready" || failed)
    viewer = (
      <MediaPlaceholder
        status={failed ? "unavailable" : result?.status || "unavailable"}
        height={240}
      >
        <ButtonBase
          onClick={() => {
            setFailed(false);
            retry();
          }}
          sx={{ fontSize: 12, textDecoration: "underline" }}
        >
          Retry
        </ButtonBase>
      </MediaPlaceholder>
    );
  else
    viewer = (
      <ButtonBase
        onClick={() => setZoom(true)}
        aria-label={`Zoom ${label}`}
        sx={{
          display: "block",
          width: "100%",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          overflow: "hidden",
          bgcolor: "background.default",
        }}
      >
        <Box
          component="img"
          src={result.gateway_path}
          alt={label}
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
          sx={{ display: "block", width: "100%", height: 240, objectFit: "contain" }}
        />
      </ButtonBase>
    );

  return (
    <Stack
      spacing={1}
      role="region"
      aria-label="Computer-use screenshots"
      tabIndex={0}
      onKeyDown={onKeyDown}
      sx={{
        p: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main" },
      }}
    >
      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <Typography variant="body2" fontWeight="fontWeightMedium">
          Computer-use screenshots
        </Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontVariantNumeric: "tabular-nums" }}
          aria-live="polite"
        >
          {`Step ${index + 1} of ${frames.length}`}
        </Typography>
      </Stack>
      {viewer}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ minWidth: 0 }}>
        <Typography variant="caption" noWrap sx={{ flex: 1, minWidth: 0 }}>
          {[frame.spanName || "Span", frame.item.reference.tool_call_id && `call ${frame.item.reference.tool_call_id}`, offset]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
        {onSelectSpan && frame.parent.span_id !== currentSpanId && (
          <Button
            size="small"
            color="inherit"
            onClick={() => onSelectSpan(frame.parent.span_id)}
          >
            View span
          </Button>
        )}
      </Stack>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <IconButton
          size="small"
          onClick={() => go(index - 1)}
          disabled={index === 0}
          aria-label="Previous screenshot"
        >
          <Iconify icon="solar:alt-arrow-left-linear" width={16} />
        </IconButton>
        <Stack
          direction="row"
          spacing={0.5}
          sx={{ flex: 1, minWidth: 0, overflow: "hidden" }}
          aria-label="Screenshot steps"
        >
          {visible.map((step, offsetIndex) => {
            const stepIndex = start + offsetIndex;
            const selected = stepIndex === index;
            return (
              <ButtonBase
                key={step.key}
                onClick={() => go(stepIndex)}
                aria-current={selected ? "step" : undefined}
                aria-label={`Step ${stepIndex + 1}${step.spanName ? `: ${step.spanName}` : ""}`}
                sx={{
                  minWidth: 28,
                  height: 24,
                  px: 0.5,
                  fontSize: 11,
                  borderRadius: "4px",
                  border: "1px solid",
                  borderColor: selected ? "text.primary" : "divider",
                  bgcolor: selected ? "text.primary" : "transparent",
                  color: selected ? "background.paper" : step.item.status === "ready" ? "text.primary" : "text.disabled",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {stepIndex + 1}
              </ButtonBase>
            );
          })}
        </Stack>
        <IconButton
          size="small"
          onClick={() => go(index + 1)}
          disabled={index === frames.length - 1}
          aria-label="Next screenshot"
        >
          <Iconify icon="solar:alt-arrow-right-linear" width={16} />
        </IconButton>
      </Stack>
      <Typography variant="caption" color="text.secondary">
        Screenshots captured at each step, not a continuous recording.
      </Typography>
      <ImageZoomDialog
        open={zoom}
        onClose={() => setZoom(false)}
        projectId={projectId}
        request={request}
        label={label}
      />
    </Stack>
  );
}
ScreenshotFilmstrip.propTypes = {
  projectId: PropTypes.string.isRequired,
  frames: PropTypes.arrayOf(PropTypes.object).isRequired,
  initialIndex: PropTypes.number,
  currentSpanId: PropTypes.string,
  traceStartTime: PropTypes.oneOfType([
    PropTypes.string,
    PropTypes.number,
    PropTypes.instanceOf(Date),
  ]),
  onSelectSpan: PropTypes.func,
};
