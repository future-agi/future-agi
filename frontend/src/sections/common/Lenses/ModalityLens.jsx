// A media compare lens (R4-SPEC D15; lane M fills F's stub, lane T mounts
// it; frozen props, additive optional ones marked). One lens per modality:
// call → CallLens, image → ImageLens, document / json → DocLens, audio →
// AudioLens, video → VideoLens.
import React, { Suspense, lazy } from "react";
import PropTypes from "prop-types";
import { Box, Skeleton } from "@mui/material";

const CallLens = lazy(() => import("./CallLens"));
const ImageLens = lazy(() => import("./ImageLens"));
const DocLens = lazy(() => import("./DocLens"));
const AudioLens = lazy(() => import("./AudioLens"));
const VideoLens = lazy(() => import("./VideoLens"));

const BY_KIND = {
  call: CallLens,
  image: ImageLens,
  document: DocLens,
  json: DocLens,
  audio: AudioLens,
  video: VideoLens,
};

export default function ModalityLens(props) {
  const { kind, compact, height } = props;
  const Lens = BY_KIND[kind];
  const h = height ?? (compact ? 220 : 360);
  if (!Lens) return null;
  return (
    <Suspense
      fallback={
        <Box sx={{ height: h, p: 1.5 }}>
          <Skeleton variant="rounded" height="100%" />
        </Box>
      }
    >
      <Lens {...props} height={h} />
    </Suspense>
  );
}

ModalityLens.propTypes = {
  kind: PropTypes.oneOf([
    "call",
    "image",
    "document",
    "json",
    "audio",
    "video",
  ]),
  lanes: PropTypes.arrayOf(
    PropTypes.shape({
      runId: PropTypes.string,
      label: PropTypes.string,
      color: PropTypes.string,
      isBaseline: PropTypes.bool,
      value: PropTypes.any,
      trace_id: PropTypes.string,
      expected: PropTypes.any,
      // Additive (M): the lane's pass/fail on the primary metric, when known.
      passed: PropTypes.bool,
    }),
  ),
  rowId: PropTypes.string,
  input: PropTypes.any,
  expected: PropTypes.any,
  focusTurn: PropTypes.number,
  onFocusTurn: PropTypes.func,
  mode: PropTypes.string,
  onModeChange: PropTypes.func,
  onVote: PropTypes.func,
  keysEnabled: PropTypes.bool,
  compact: PropTypes.bool,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  // Additive (M): data states and the call lens latency target.
  loading: PropTypes.bool,
  error: PropTypes.bool,
  onRetry: PropTypes.func,
  latencyThresholdMs: PropTypes.number,
};
