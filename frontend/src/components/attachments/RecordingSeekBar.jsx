import { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Slider } from "@mui/material";
import { recordingClock } from "./recordingClock";
export default function RecordingSeekBar({ seconds, duration, onSeek, peaks }) {
  const path = useMemo(
    () =>
      peaks
        ?.map((p, x) => {
          if (!p || (!p[0] && !p[1])) return "";
          const y = 15 - (15 * p[1]) / 32768;
          return p[0] === p[1]
            ? `M${x} ${y}h1`
            : `M${x + 0.5} ${y}V${15 - (15 * p[0]) / 32768}`;
        })
        .join(""),
    [peaks],
  );
  return (
    <Box
      sx={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        flex: 1,
        minWidth: 0,
        height: 30,
      }}
    >
      {peaks && (
        <Box
          component="svg"
          aria-hidden="true"
          data-testid="recording-waveform"
          viewBox={`0 0 ${peaks.length} 30`}
          preserveAspectRatio="none"
          sx={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: 30,
            pointerEvents: "none",
            color: "text.secondary",
          }}
        >
          <path d={path} stroke="currentColor" />
        </Box>
      )}
      <Slider
        aria-label="Recording position"
        min={0}
        max={duration}
        step={Math.min(0.1, duration / 10)}
        value={seconds}
        onChange={(_, v) => onSeek(v)}
        getAriaValueText={(v) =>
          `${recordingClock(v, duration)} of ${recordingClock(duration, duration)}`
        }
        sx={{
          color: "text.primary",
          ...(peaks && {
            "& .MuiSlider-rail": { opacity: 0 },
            "& .MuiSlider-track": { height: 2 },
          }),
        }}
      />
    </Box>
  );
}
RecordingSeekBar.propTypes = {
  seconds: PropTypes.number.isRequired,
  duration: PropTypes.number.isRequired,
  onSeek: PropTypes.func.isRequired,
  peaks: PropTypes.array,
};
