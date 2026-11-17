// AudioLens (`kind="audio"`): TTS A/B. The text once on top; per
// lane its waveform (played progress), `Play`, the duration and the lane's
// own eval readouts (Round-trip WER, Time to first byte, Naturalness).
// `A/B` / `Q` (only while the lens has focus or the pointer is over it)
// swaps the audible lane at the same position. `Match loudness` (the `⋯`)
// turns the louder lanes down to the quietest one (gain from the peaks'
// RMS, `−2.1 dB`). One audio at a time app-wide (audioBus). Plain media
// elements play the clips; no WaveSurfer here.
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Menu,
  MenuItem,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ErrorState from "src/sections/common/ux/ErrorState";
import { NUM_SX, fmtNumber } from "src/sections/common/format/fmt";
import MiniWaveform from "src/sections/common/Media/MiniWaveform";
import { claimAudio, releaseAudio } from "src/sections/common/Media/audioBus";
import { fmtDuration, mediaRefOf } from "src/sections/common/Media/mediaRef";
import { clockOf } from "src/sections/common/Media/mediaModel";
import { useSeekableUrls } from "src/sections/common/Media/seekableUrl";
import Term from "src/sections/common/ux/Term";
import LaneLabel from "./LaneLabel";
import { matchLoudness } from "./lensModel";
import { HeardLine } from "./TranscriptDiff";

const TEXT_KEYS = ["opening_line", "text", "input", "prompt", "script"];

/** The text the clips speak: a string input, or a known text field. */
function textOf(input) {
  if (typeof input === "string") return input;
  if (input && typeof input === "object")
    for (const k of TEXT_KEYS)
      if (typeof input[k] === "string" && input[k].trim()) return input[k];
  return null;
}

const audioRefOf = (v) => {
  const r = mediaRefOf(v, { columnType: "audio" });
  const one = Array.isArray(r) ? r[0] : r;
  return one?.kind === "audio" ? one : null;
};

/** Readouts from the lane's eval scores (data, shown as the eval wrote it). */
const READOUTS = [
  { key: "eval:roundtrip_wer", label: "Round-trip WER", term: "wer" },
  { key: "eval:ttfb", label: "Time to first byte" },
  { key: "eval:naturalness", label: "Naturalness" },
];

/** IT-R5 G4 P2: `≤ 5%` from a check named `Round-trip WER ≤ 5%`. */
const ruleOf = (s) => /\s([≤≥]\s*.+)$/.exec(s?.name ?? "")?.[1] ?? null;

/** A lane's scores by metric key (the result's map, or `[{key, …}]`). */
function scoresOf(lane) {
  const raw = lane?.result?.scores ?? lane?.scores ?? null;
  const own = !raw
    ? {}
    : Array.isArray(raw)
      ? Object.fromEntries(raw.filter((x) => x?.key).map((x) => [x.key, x]))
      : raw;
  return { ...measuredReadouts(lane?.value), ...own };
}

/**
 * Readouts a generated clip carries itself (options-truth L3 G7 W5): the
 * time to first byte, round-trip WER and naturalness measured on it, shown
 * even when the run picked no eval for them (an eval's score wins).
 */
/** A clip value as an object (results serve outputs as JSON strings). */
export function clipOf(value) {
  if (value && typeof value === "object") return value;
  if (typeof value !== "string" || !value.trim().startsWith("{")) return null;
  try {
    const v = JSON.parse(value);
    return v && typeof v === "object" ? v : null;
  } catch {
    return null;
  }
}

export function measuredReadouts(value) {
  const m = clipOf(value)?.measured ?? null;
  if (!m) return {};
  const out = {};
  if (Number.isFinite(m.roundtrip_wer))
    out["eval:roundtrip_wer"] = {
      display: `${(m.roundtrip_wer * 100).toFixed(1)}%`,
      reason: "Measured on this clip (transcribed back and compared).",
      measured: { transcript: m.roundtrip_transcript },
    };
  if (Number.isFinite(m.ttfb_ms))
    out["eval:ttfb"] = {
      display: `${Math.round(m.ttfb_ms)} ms`,
      reason: "Measured on this clip.",
    };
  if (Number.isFinite(m.naturalness))
    out["eval:naturalness"] = {
      display: m.naturalness.toFixed(1),
      reason: "Audio judge on this clip, 1-5.",
    };
  return out;
}

/** The text the lanes' clips speak (each generated clip carries it). */
const spokenTextOf = (lanes = []) =>
  lanes
    .map((l) => clipOf(l?.value)?.transcript)
    .find((t) => typeof t === "string" && t.trim()) ?? null;

let lensSeq = 0;

export default function AudioLens({
  lanes = [],
  input,
  height,
  keysEnabled = false,
  loading,
  error,
  onRetry,
}) {
  const idRef = useRef(null);
  if (!idRef.current) {
    lensSeq += 1;
    idRef.current = `audio-lens-${lensSeq}`;
  }
  const busId = idRef.current;
  const refs = useMemo(() => lanes.map((l) => audioRefOf(l.value)), [lanes]);
  const seekable = useSeekableUrls(refs.map((r) => r?.url ?? null));
  const audios = useRef([]);
  const [playing, setPlaying] = useState(null); // lane index or null
  const [pos, setPos] = useState(0);
  const [match, setMatch] = useState(false);
  const [menuEl, setMenuEl] = useState(null);
  const [active, setActive] = useState(false);
  const rootRef = useRef(null);
  const gains = useMemo(
    () => matchLoudness(refs.map((r) => r?.peaks ?? null)),
    [refs],
  );

  useEffect(() => {
    audios.current.forEach((a, i) => {
      if (a) a.volume = match && gains[i] ? Math.min(1, gains[i].linear) : 1;
    });
  }, [match, gains, seekable.ready]);

  const stop = useCallback(() => {
    audios.current.forEach((a) => a?.pause());
    setPlaying(null);
  }, []);
  useEffect(
    () => () => {
      audios.current.forEach((a) => a?.pause());
      releaseAudio(busId);
    },
    [busId],
  );

  const playLane = useCallback(
    (i, at) => {
      const a = audios.current[i];
      if (!a) return;
      audios.current.forEach((x, j) => j !== i && x?.pause());
      claimAudio(busId, stop);
      if (Number.isFinite(at)) a.currentTime = Math.min(at, a.duration || at);
      a.play().catch(() => setPlaying(null));
      setPlaying(i);
    },
    [busId, stop],
  );
  const toggle = (i) => {
    if (playing === i) {
      audios.current[i]?.pause();
      setPlaying(null);
      releaseAudio(busId);
    } else playLane(i, playing != null ? undefined : pos);
  };
  /** A/B: the other lane, at the same position. */
  const swap = useCallback(() => {
    if (lanes.length < 2) return;
    const from = playing ?? 0;
    const to = (from + 1) % lanes.length;
    const at = audios.current[from]?.currentTime ?? pos;
    playLane(to, at);
  }, [lanes.length, playing, pos, playLane]);

  // `Q` (widget-local): only while the lens has focus or the pointer is over it.
  useEffect(() => {
    if (!keysEnabled) return undefined;
    const onKey = (e) => {
      const el = rootRef.current;
      const t = e.target;
      if (
        !el ||
        e.metaKey ||
        e.ctrlKey ||
        e.altKey ||
        (t &&
          (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)))
      )
        return;
      if (
        !(active || el.matches(":hover") || el.contains(document.activeElement))
      )
        return;
      if (e.key === "q" || e.key === "Q") {
        e.preventDefault();
        swap();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [keysEnabled, active, swap]);

  if (error)
    return (
      <Box sx={{ p: 3 }}>
        <ErrorState thing="these clips" onRetry={onRetry} />
      </Box>
    );

  const text = textOf(input) ?? spokenTextOf(lanes);
  const loudest = gains.reduce(
    (m, g) => (g && g.db < (m?.db ?? 0) ? g : m),
    null,
  );

  return (
    <Stack
      ref={rootRef}
      tabIndex={0}
      data-modality-lens="audio"
      aria-label="Audio lens: play each clip, A/B at the same moment with Q"
      onPointerEnter={() => setActive(true)}
      onPointerLeave={() => setActive(false)}
      sx={{
        height: height ?? "100%",
        minHeight: 0,
        p: 1.5,
        gap: 1.25,
        outline: "none",
        overflow: "auto",
        "&:focus-visible": {
          boxShadow: (t) => `inset 0 0 0 2px ${t.palette.primary.main}`,
        },
      }}
    >
      <Stack direction="row" alignItems="flex-start" gap={1}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography typography="s3" color="text.secondary">
            Text
          </Typography>
          <Typography typography="s1" data-audio-text="">
            {text ? `“${text}”` : "No input text on this row"}
          </Typography>
        </Box>
        <CustomTooltip
          show
          size="small"
          title={
            keysEnabled
              ? "Play the other clip from the same moment (Q while pointing at the lens)"
              : "Play the other clip from the same moment"
          }
        >
          <span>
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={swap}
              disabled={lanes.length < 2 || !seekable.ready}
              startIcon={<Iconify icon="mdi:swap-horizontal" width={16} />}
              sx={{ height: 28 }}
              data-audio-ab=""
            >
              {playing == null ? "Play A/B" : "Switch"}
            </Button>
          </span>
        </CustomTooltip>
        <IconButton
          size="small"
          aria-label="More audio options"
          onClick={(e) => setMenuEl(e.currentTarget)}
        >
          <Iconify icon="mdi:dots-horizontal" width={18} />
        </IconButton>
        <Menu
          anchorEl={menuEl}
          open={Boolean(menuEl)}
          onClose={() => setMenuEl(null)}
        >
          <MenuItem
            onClick={() => {
              setMatch((m) => !m);
              setMenuEl(null);
            }}
            sx={{ typography: "s2", gap: 1 }}
          >
            <Iconify
              icon={
                match ? "mdi:checkbox-marked" : "mdi:checkbox-blank-outline"
              }
              width={16}
            />
            Match loudness
            {loudest ? (
              <Typography typography="s3" color="text.secondary" sx={NUM_SX}>
                {`${fmtNumber(loudest.db)} dB on the louder clip`}
              </Typography>
            ) : null}
          </MenuItem>
        </Menu>
      </Stack>
      {loading ? (
        <Stack gap={1}>
          {lanes.map((l) => (
            <Skeleton key={l.runId} variant="rounded" height={64} />
          ))}
        </Stack>
      ) : (
        lanes.map((l, i) => {
          const r = refs[i];
          const scores = scoresOf(l);
          const heard = scores["eval:roundtrip_wer"]?.measured?.transcript;
          const dur = r?.duration_ms;
          const progress =
            playing === i && dur ? Math.min(1, (pos * 1000) / dur) : 0;
          return (
            <Stack
              key={l.runId}
              gap={0.75}
              data-audio-lane={l.runId}
              data-audible={playing === i ? "true" : "false"}
              sx={{
                p: 1,
                border: "1px solid",
                borderColor:
                  playing === i ? l.color ?? "primary.main" : "divider",
                borderRadius: 1,
                minHeight: 64,
              }}
            >
              <Stack direction="row" alignItems="center" gap={1}>
                <LaneLabel lane={l} dense />
                <Box sx={{ flex: 1 }} />
                {match && gains[i] && gains[i].db < -0.05 ? (
                  <Typography
                    typography="s3"
                    color="text.secondary"
                    sx={NUM_SX}
                  >
                    {`${fmtNumber(gains[i].db)} dB`}
                  </Typography>
                ) : null}
              </Stack>
              {r ? (
                <Stack direction="row" alignItems="center" gap={1}>
                  <IconButton
                    size="small"
                    aria-label={
                      playing === i ? `Pause ${l.label}` : `Play ${l.label}`
                    }
                    onClick={() => toggle(i)}
                    disabled={!seekable.ready}
                    data-audio-play={i}
                  >
                    <Iconify
                      icon={playing === i ? "mdi:pause" : "mdi:play"}
                      width={18}
                    />
                  </IconButton>
                  <Box sx={{ flex: 1, minWidth: 0, color: l.color }}>
                    <MiniWaveform
                      peaks={r.peaks}
                      width={480}
                      height={28}
                      progress={progress}
                      color={l.color}
                    />
                  </Box>
                  <Typography
                    typography="s3"
                    color="text.secondary"
                    sx={{ ...NUM_SX, minWidth: 72, textAlign: "right" }}
                  >
                    {`${playing === i ? clockOf(pos * 1000) : clockOf(0)} / ${fmtDuration(dur)}`}
                  </Typography>
                  {seekable.ready ? (
                    <audio
                      ref={(el) => {
                        audios.current[i] = el;
                      }}
                      src={seekable.urls[i] ?? undefined}
                      preload="auto"
                      onTimeUpdate={(e) =>
                        playing === i && setPos(e.currentTarget.currentTime)
                      }
                      onEnded={() => {
                        if (playing === i) {
                          setPlaying(null);
                          setPos(0);
                          releaseAudio(busId);
                        }
                      }}
                      style={{ display: "none" }}
                    />
                  ) : null}
                </Stack>
              ) : (
                <Typography typography="s2" color="text.disabled">
                  No audio in this run&apos;s output
                </Typography>
              )}
              {text && typeof heard === "string" ? (
                // IT-R5 G4 P2: what the clip said, transcribed back, against
                // the text (missing words struck, extra ones underlined).
                <Box data-audio-heard="" sx={{ minWidth: 0 }}>
                  <HeardLine turn={{ scripted_text: text, text: heard }} />
                </Box>
              ) : null}
              <Stack direction="row" gap={2} sx={{ flexWrap: "wrap" }}>
                {READOUTS.map((m) => {
                  const s = scores[m.key];
                  const rule = ruleOf(s);
                  const tone =
                    s?.status === "failed"
                      ? "error.main"
                      : s?.status === "passed"
                        ? "success.main"
                        : "text.primary";
                  return (
                    <CustomTooltip
                      key={m.key}
                      show
                      size="small"
                      title={
                        s
                          ? `${s.reason ?? ""}${rule ? ` Passes at ${rule}.` : ""}`.trim()
                          : "This run has no score for it"
                      }
                    >
                      <Stack data-audio-metric={m.key} data-status={s?.status}>
                        <Typography typography="s3" color="text.secondary">
                          {m.term ? <Term k={m.term}>{m.label}</Term> : m.label}
                          {rule ? ` ${rule}` : ""}
                        </Typography>
                        <Typography
                          typography="s2"
                          sx={{ ...NUM_SX, color: tone }}
                        >
                          {s?.display ?? "—"}
                          {s?.status === "failed"
                            ? " ✗"
                            : s?.status === "passed" && rule
                              ? " ✓"
                              : ""}
                        </Typography>
                      </Stack>
                    </CustomTooltip>
                  );
                })}
                <Stack>
                  <Typography typography="s3" color="text.secondary">
                    Duration
                  </Typography>
                  <Typography typography="s2" sx={NUM_SX}>
                    {Number.isFinite(dur) ? fmtDuration(dur) : "—"}
                  </Typography>
                </Stack>
              </Stack>
            </Stack>
          );
        })
      )}
    </Stack>
  );
}

AudioLens.propTypes = {
  lanes: PropTypes.array,
  input: PropTypes.any,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  keysEnabled: PropTypes.bool,
  loading: PropTypes.bool,
  error: PropTypes.bool,
  onRetry: PropTypes.func,
};
