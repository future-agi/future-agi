import { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Slider } from "@mui/material";
import { recordingClock } from "./recordingClock";

export const WAVEFORM_BARS = 96;
// Quiet audio is lifted to stay visible, at most this much, so it never
// looks as loud as speech. Silence and gaps stay empty.
const MAX_GAIN = 8;
const FULL = 32767;
const HEIGHT = 30;
const SLOT = 3;

/**
 * Bars like the Voice Observability waveform from the server's min/max
 * overview columns: each bar is the loudest column it covers, as a 0–1
 * level after a bounded gain. A gap (null) or true silence is 0.
 */
export function waveformBars(peaks, count = WAVEFORM_BARS) {
  if (!Array.isArray(peaks) || !peaks.length) return [];
  const per = peaks.length / count;
  const raw = [];
  let loudest = 0;
  for (let b = 0; b < count; b++) {
    const from = Math.floor(b * per);
    const to = Math.min(
      peaks.length,
      Math.max(from + 1, Math.floor((b + 1) * per)),
    );
    let level = 0;
    for (let x = from; x < to; x++) {
      const p = peaks[x];
      if (p) level = Math.max(level, Math.abs(p[0]), Math.abs(p[1]));
    }
    raw.push(level);
    loudest = Math.max(loudest, level);
  }
  if (!loudest) return raw.map(() => 0);
  const gain = Math.min(MAX_GAIN, FULL / loudest);
  return raw.map((level) => Math.min(1, (level * gain) / FULL));
}

export default function RecordingSeekBar({ seconds, duration, onSeek, peaks }) {
  const bars = useMemo(() => (peaks ? waveformBars(peaks) : []), [peaks]);
  // A wholly silent recording draws no bars, so it keeps the plain track.
  const drawn = bars.some((level) => level > 0);
  const played = duration > 0 ? seconds / duration : 0;
  return (
    <Box
      sx={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        flex: 1,
        minWidth: 0,
        height: HEIGHT,
      }}
    >
      {peaks && (
        <Box
          component="svg"
          aria-hidden="true"
          data-testid="recording-waveform"
          viewBox={`0 0 ${bars.length * SLOT} ${HEIGHT}`}
          preserveAspectRatio="none"
          sx={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: HEIGHT,
            pointerEvents: "none",
            color: "text.secondary",
          }}
        >
          {bars.map((level, i) => {
            if (!level) return null;
            const height = Math.max(2, level * (HEIGHT - 4));
            return (
              <rect
                key={i}
                x={i * SLOT + 0.5}
                y={(HEIGHT - height) / 2}
                width={SLOT - 1}
                height={height}
                rx={0.75}
                fill="currentColor"
                fillOpacity={(i + 0.5) / bars.length <= played ? 1 : 0.4}
              />
            );
          })}
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
          ...(drawn && {
            // The bars show progress; keep only the thumb over them.
            "& .MuiSlider-rail": { opacity: 0 },
            "& .MuiSlider-track": { opacity: 0 },
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
