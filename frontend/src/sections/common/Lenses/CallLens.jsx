// The Call lens (ModalityLens kind="call"): 2–4 call lanes,
// baseline first, on one SyncClock. Answer first (`First difference: …`,
// auto-focused), then the transport, the audio lanes, the transcript diff,
// the latency line + timeline and the metric strip with its source label.
// Keys (only with `keysEnabled`, and only while the lens has focus or the
// pointer is over it): `Space` play all, `<` / `>` previous / next turn.
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
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Skeleton,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import ErrorState from "src/sections/common/ux/ErrorState";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import useLayoutPrefs from "src/sections/common/ux/oneview/layoutPrefs";
import { fmtMs, NUM_SX } from "src/sections/common/format/fmt";
import { formatClock } from "src/components/VoiceDetailDrawerV2/transcriptUtils";
import { claimAudio, releaseAudio } from "src/sections/common/Media/audioBus";
import AudioLanes, { audioLanesHeight } from "./AudioLanes";
import TranscriptDiff from "./TranscriptDiff";
import LatencyTimeline, { COMPACT_H, EXPANDED_H } from "./LatencyTimeline";
import CallMetricStrip from "./CallMetricStrip";
import { createSyncClock } from "./SyncClock";
import useLaneCalls from "./useLaneCalls";
import {
  alignCalls,
  callCause,
  rankedCallDifference,
  latencyRows,
  matchLoudness,
  seekTimesFor,
  slowestLine,
  slowestTurn,
  stepsOf,
} from "./lensModel";

/**
 * The `Latency p90` eval's pass threshold (`r4Evaluators`: ≤ 2.0 s); a
 * screen with the eval definition passes its own via `latencyThresholdMs`.
 */
export const LATENCY_P90_TARGET_MS = 2000;
const RATES = [1, 1.5, 2];

/**
 * IT-R5 G4 P5: the folded waveforms, one 32 px strip: where the call is
 * (the anchor lane's clock), the first difference marked, a click seeks
 * every lane; `Waveforms` opens them again.
 */
function ScrubStrip({ clock, lanes, marks, onExpand }) {
  useClockTick(clock);
  const dur = clock.duration() || 0;
  const t = clock.time() || 0;
  const anchor = clock.anchorId() ?? lanes[0]?.runId;
  const mark = marks?.[anchor] ?? Object.values(marks ?? {})[0] ?? null;
  const pct = (sec) => `${dur ? Math.min(100, (sec / dur) * 100) : 0}%`;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      data-scrub-strip=""
      sx={{ height: 32, flexShrink: 0, minWidth: 0 }}
    >
      <Box
        role="slider"
        tabIndex={0}
        aria-label="Call position: click to move every lane there"
        aria-valuemin={0}
        aria-valuemax={Math.round(dur)}
        aria-valuenow={Math.round(t)}
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          if (dur) clock.seek(((e.clientX - r.left) / r.width) * dur);
        }}
        sx={{
          position: "relative",
          flex: 1,
          height: 12,
          borderRadius: 6,
          bgcolor: "action.hover",
          cursor: "pointer",
          overflow: "hidden",
        }}
      >
        {mark ? (
          <CustomTooltip
            show
            size="small"
            title={`First difference: ${mark.label}`}
          >
            <Box
              sx={{
                position: "absolute",
                top: 0,
                bottom: 0,
                left: pct(mark.startMs / 1000),
                width: `calc(${pct((mark.endMs - mark.startMs) / 1000)} + 2px)`,
                bgcolor: "warning.lighter",
                borderLeft: "2px solid",
                borderColor: "warning.main",
              }}
            />
          </CustomTooltip>
        ) : null}
        <Box
          sx={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: 0,
            width: pct(t),
            bgcolor: lanes[0]?.color ?? "primary.main",
            opacity: 0.35,
          }}
        />
      </Box>
      <Button
        size="small"
        color="inherit"
        onClick={onExpand}
        endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
        sx={{ height: 24, typography: "s3", flexShrink: 0 }}
        data-wave-expand=""
      >
        Waveforms
      </Button>
    </Stack>
  );
}
ScrubStrip.propTypes = {
  clock: PropTypes.object.isRequired,
  lanes: PropTypes.array.isRequired,
  marks: PropTypes.object,
  onExpand: PropTypes.func.isRequired,
};

/** `#1B Acme voice v2 · LiveKit` → `Acme voice v2`. */
export const laneName = (label) =>
  String(label ?? "")
    .replace(/^#\S+\s+/, "")
    .split(" · ")[0]
    .trim() || String(label ?? "");

function useClockTick(clock) {
  const [, setN] = useState(0);
  useEffect(() => clock.subscribe(() => setN((n) => (n + 1) % 1e6)), [clock]);
}

function TransportTime({ clock }) {
  useClockTick(clock);
  return (
    <Typography
      typography="s2"
      color="text.secondary"
      sx={{ ...NUM_SX, minWidth: 84 }}
      data-transport-time=""
    >
      {`${formatClock(clock.time())} / ${formatClock(clock.duration())}`}
    </Typography>
  );
}

TransportTime.propTypes = { clock: PropTypes.object.isRequired };

function PlayAll({ clock, disabled, onToggle }) {
  useClockTick(clock);
  const playing = clock.isPlaying();
  return (
    <KeyHintTooltip
      title={playing ? "Pause all lanes" : "Play every lane together"}
      action="lensPlay"
    >
      <span>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={disabled}
          onClick={onToggle}
          data-play-all={playing ? "playing" : "paused"}
          startIcon={
            <Iconify icon={playing ? "mdi:pause" : "mdi:play"} width={16} />
          }
          sx={{ height: 28, minWidth: 104 }}
        >
          {playing ? "Pause" : "Play all"}
        </Button>
      </span>
    </KeyHintTooltip>
  );
}

PlayAll.propTypes = {
  clock: PropTypes.object.isRequired,
  disabled: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
};

export default function CallLens({
  lanes: rawLanes,
  input,
  focusTurn,
  onFocusTurn,
  keysEnabled = false,
  height,
  loading: outerLoading = false,
  error: outerError = false,
  onRetry,
  latencyThresholdMs = LATENCY_P90_TARGET_MS,
}) {
  const lanes = useMemo(() => (rawLanes ?? []).slice(0, 4), [rawLanes]);
  const { calls, loading, error, refetch } = useLaneCalls(lanes);
  const clock = useMemo(() => createSyncClock(), []);
  const rootRef = useRef(null);
  const [active, setActive] = useState(false);
  const busId = useMemo(
    () => `call-lens-${Math.random().toString(36).slice(2)}`,
    [],
  );
  const [prefs, patchPrefs] = useLayoutPrefs("callLens");
  const expanded = Boolean(prefs?.extras?.timelineExpanded);
  // IT-R2 G4 P6: the transcript gets the room; the latency chart and the
  // metrics table open on demand (remembered): one line and one row.
  const chartOn = Boolean(prefs?.extras?.latencyChart);
  const metricsTable = Boolean(prefs?.extras?.metricsTable);
  const [rate, setRate] = useState(1);
  const [solo, setSolo] = useState(null);
  const [loopOn, setLoopOn] = useState(false);
  const [align, setAlign] = useState(true);
  const [normalize, setNormalize] = useState(false);
  const [soloAnchor, setSoloAnchor] = useState(null);
  const [moreAnchor, setMoreAnchor] = useState(null);
  const [innerTurn, setInnerTurn] = useState(null);

  useEffect(
    () => () => {
      clock.destroy();
      releaseAudio(busId);
    },
    [clock, busId],
  );

  const names = useMemo(() => lanes.map((l) => laneName(l.label)), [lanes]);
  const callerName = input?.persona?.name ?? "The caller";
  const rows = useMemo(() => alignCalls(calls), [calls]);
  const steps = useMemo(() => stepsOf(rows), [rows]);
  const diff = useMemo(
    // Ranked by outcome: a different tool beats args, interruption, wording.
    () => rankedCallDifference(calls, { names, callerName }),
    [calls, names, callerName],
  );
  // The first difference, marked on every lane's waveform (IT-R1 G4): each
  // lane's own turns of that aligned row.
  const diffMarks = useMemo(() => {
    if (!diff) return {};
    const row = rows.find((r) => r.k === diff.k);
    return Object.fromEntries(
      lanes
        .map((l, i) => {
          const pair = row?.lanes?.[i];
          const turns = [pair?.caller, ...(pair?.agents ?? [])].filter(
            (t) => t && Number.isFinite(t.start_ms),
          );
          if (!turns.length) return null;
          return [
            l.runId,
            {
              startMs: Math.min(...turns.map((t) => t.start_ms)),
              endMs: Math.max(...turns.map((t) => t.end_ms ?? t.start_ms)),
              label: `turn ${diff.turn}`,
            },
          ];
        })
        .filter(Boolean),
    );
  }, [diff, rows, lanes]);
  // IT-R5 G4 P5: the cause in the same line (heard diff + tool result).
  const cause = useMemo(() => callCause(calls, { names }), [calls, names]);
  const failing = useMemo(() => {
    const l = lanes.find((x, i) => i > 0 && x.failing?.length);
    return l ? { label: names[lanes.indexOf(l)], list: l.failing } : null;
  }, [lanes, names]);
  // IT-R5 G4 P5: the waveforms fold to one 32 px scrub strip after the
  // first scroll of the transcript (Show waveforms in ⋯ keeps them).
  const waveKeep = Boolean(prefs?.extras?.waveExpanded);
  const [folded, setFolded] = useState(false);
  const waveFolded = folded && !waveKeep;
  const latRows = useMemo(() => latencyRows(calls), [calls]);
  const slow = useMemo(() => slowestTurn(latRows), [latRows]);

  // Focused turn: controlled (`focusTurn` / `onFocusTurn`, the URL `turn=`)
  // or local; the first difference is focused when nothing else is.
  const turnNo = focusTurn ?? innerTurn ?? diff?.turn ?? null;
  const focusStep = useMemo(
    () => steps.find((s) => s.n === turnNo) ?? null,
    [steps, turnNo],
  );
  const setTurn = useCallback(
    (n) => {
      if (onFocusTurn) onFocusTurn(n);
      else setInnerTurn(n);
    },
    [onFocusTurn],
  );

  const seekToStep = useCallback(
    (step) => {
      if (!step) return;
      const times = seekTimesFor(rows, step, calls);
      if (align) {
        clock.seekLanes(
          Object.fromEntries(lanes.map((l, i) => [l.runId, times[i]])),
        );
      } else {
        const t = times.find((x) => Number.isFinite(x));
        if (Number.isFinite(t)) clock.seek(t);
      }
      if (loopOn) {
        const anchor = clock.anchorId();
        const ai = Math.max(
          0,
          lanes.findIndex((l) => l.runId === anchor),
        );
        const row = rows.find((r) => r.k === step.k);
        const lane = row?.lanes?.[ai];
        const turn =
          lane && (step.slot === 0 ? lane.caller : lane.agents[step.slot - 1]);
        if (turn && align)
          clock.setLoop({
            start: Object.fromEntries(lanes.map((l, i) => [l.runId, times[i]])),
            end: turn.end_ms / 1000,
          });
      }
    },
    [rows, calls, lanes, align, clock, loopOn],
  );

  const focusOn = useCallback(
    (step) => {
      if (!step) return;
      setTurn(step.n);
      seekToStep(step);
    },
    [setTurn, seekToStep],
  );

  // Seek once to the auto-focused first difference when the lanes are ready.
  const seededRef = useRef(false);
  // Re-render the lens only when a lane registers or leaves, never on the
  // 100 ms play ticks (PlayAll / TransportTime follow those themselves).
  const [readyCount, setReadyCount] = useState(0);
  useEffect(
    () =>
      clock.subscribe(() =>
        setReadyCount((n) =>
          n === clock.lanes().length ? n : clock.lanes().length,
        ),
      ),
    [clock],
  );
  const audioLanes = calls.filter(
    (c) => c?.recording?.agent?.url || c?.recording?.caller?.url,
  ).length;
  useEffect(() => {
    if (
      seededRef.current ||
      !focusStep ||
      !audioLanes ||
      readyCount < audioLanes
    )
      return;
    seededRef.current = true;
    seekToStep(focusStep);
  }, [focusStep, readyCount, audioLanes, seekToStep]);

  const toggle = useCallback(() => {
    if (clock.isPlaying()) {
      clock.pause();
      releaseAudio(busId);
    } else {
      claimAudio(busId, () => clock.pause());
      clock.play();
    }
  }, [clock, busId]);

  const move = useCallback(
    (d) => {
      if (!steps.length) return;
      const i = focusStep ? steps.indexOf(focusStep) : -1;
      const next = steps[Math.max(0, Math.min(steps.length - 1, i + d))];
      focusOn(next);
    },
    [steps, focusStep, focusOn],
  );

  const isActive = useCallback(() => {
    const el = rootRef.current;
    return Boolean(
      el &&
        (active || el.matches(":hover") || el.contains(document.activeElement)),
    );
  }, [active]);
  useShortcuts(
    "taskCompare",
    {
      lensPlay: {
        run: toggle,
        when: isActive,
        reason: "Point at or focus the call lens",
      },
      prevTurn: {
        run: () => move(-1),
        when: isActive,
        reason: "Point at or focus the call lens",
      },
      nextTurn: {
        run: () => move(1),
        when: isActive,
        reason: "Point at or focus the call lens",
      },
    },
    { enabled: keysEnabled, register: false },
  );

  // IT-R2 G4 P6 · Play all follows the call: the turn playing on the
  // anchor lane is focused (highlighted on every lane, scrolled into view).
  const turnStarts = useMemo(
    () => steps.map((st) => ({ st, at: seekTimesFor(rows, st, calls) })),
    [steps, rows, calls],
  );
  const followRef = useRef(null);
  useEffect(
    () =>
      clock.subscribe(() => {
        if (!clock.isPlaying() || !turnStarts.length) return;
        const anchor = clock.anchorId();
        const ai = lanes.findIndex((l) => l.runId === anchor);
        if (ai < 0) return;
        const t = clock.laneTimes()[anchor];
        if (!Number.isFinite(t)) return;
        let cur = null;
        turnStarts.forEach(({ st, at }) => {
          if (Number.isFinite(at[ai]) && at[ai] <= t + 0.05) cur = st;
        });
        if (cur && followRef.current !== cur.n) {
          followRef.current = cur.n;
          setTurn(cur.n);
        }
      }),
    [clock, turnStarts, lanes, setTurn],
  );

  // Transport `⋯` actions.
  useEffect(() => {
    clock.setRate(rate);
  }, [clock, rate, readyCount]);
  useEffect(() => {
    clock.setSolo(solo);
  }, [clock, solo, readyCount]);
  useEffect(() => {
    const gains = normalize
      ? matchLoudness(calls.map((c) => c?.recording?.agent?.peaks ?? null))
      : calls.map(() => null);
    lanes.forEach((l, i) => clock.setGain(l.runId, gains[i]?.linear ?? 1));
  }, [clock, normalize, calls, lanes, readyCount]);
  useEffect(() => {
    if (!loopOn) clock.setLoop(null);
    else if (focusStep) seekToStep(focusStep);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loopOn]);

  if (outerError || error)
    return (
      <Box sx={{ p: 3 }}>
        <ErrorState
          thing="these calls"
          onRetry={() => {
            refetch();
            onRetry?.();
          }}
        />
      </Box>
    );

  const isLoading = outerLoading || loading || calls.every((c) => !c);
  const anyAudio = calls.some((c) => c?.recording?.agent?.url);
  const passedByRun = Object.fromEntries(lanes.map((l) => [l.runId, l.passed]));
  const focusK = focusStep?.k;

  return (
    <Stack
      ref={rootRef}
      tabIndex={0}
      data-modality-lens="call"
      data-lens-active={active ? "true" : "false"}
      aria-label="Call lens: play every lane together, compare the transcripts and the latency per turn"
      onPointerEnter={() => setActive(true)}
      onPointerLeave={() => setActive(false)}
      sx={{
        height: height ?? "100%",
        minHeight: 0,
        p: 1.5,
        gap: 0.5,
        outline: "none",
        "&:focus-visible": {
          boxShadow: (t) => `inset 0 0 0 2px ${t.palette.primary.main}`,
        },
        bgcolor: "background.paper",
      }}
    >
      {/* Answer first: one fixed-height line (IT-R5 G4 P5) */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ height: 26, flexShrink: 0, minWidth: 0 }}
      >
        <Iconify
          icon="mdi:target"
          width={16}
          sx={{ color: diff ? "warning.dark" : "text.disabled", flexShrink: 0 }}
        />
        {isLoading ? (
          <Skeleton width={420} height={20} />
        ) : diff ? (
          <Box
            component="button"
            type="button"
            data-first-difference={diff.turn}
            title={cause ? diff.text : undefined}
            onClick={() => focusOn(steps.find((s) => s.n === diff.turn))}
            sx={{
              p: 0,
              border: 0,
              bgcolor: "transparent",
              cursor: "pointer",
              textAlign: "left",
              color: "text.primary",
              typography: "s1",
              fontWeight: 500,
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              "&:hover": { textDecoration: "underline" },
            }}
          >
            {cause
              ? `First difference: turn ${diff.turn} · ${cause.text}`
              : diff.text}
          </Box>
        ) : (
          <Typography typography="s1" color="text.secondary">
            No difference between these calls: same tool calls and replies on
            every turn.
          </Typography>
        )}
        {diff && !isLoading ? (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled={!anyAudio || !readyCount}
            data-play-both=""
            onClick={() => {
              focusOn(steps.find((s) => s.n === diff.turn));
              if (!clock.isPlaying()) toggle();
            }}
            startIcon={<Iconify icon="mdi:play" width={14} />}
            sx={{
              height: 24,
              flexShrink: 0,
              typography: "s3",
              borderColor: "divider",
            }}
          >
            {lanes.length > 2 ? "Play all lanes" : "Play both"}
          </Button>
        ) : null}
      </Stack>
      {failing ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          data-call-failing=""
          sx={{ height: 22, flexShrink: 0, minWidth: 0, pl: 3 }}
        >
          <Typography typography="s3" color="text.secondary" noWrap>
            {failing.label} failed
          </Typography>
          {failing.list.slice(0, 2).map((name) => (
            <Box
              key={name}
              component="span"
              sx={{
                px: 0.75,
                height: 20,
                lineHeight: "20px",
                borderRadius: 0.5,
                typography: "s3",
                color: "red.700",
                bgcolor: "red.o5",
                border: "1px solid",
                borderColor: "red.200",
                whiteSpace: "nowrap",
              }}
            >
              {`✗ ${name}`}
            </Box>
          ))}
          {failing.list.length > 2 ? (
            <Typography typography="s3" color="text.secondary" noWrap>
              {`+${failing.list.length - 2} more in Checks`}
            </Typography>
          ) : null}
        </Stack>
      ) : null}

      {/* Transport */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        flexWrap="wrap"
        data-transport=""
      >
        <PlayAll
          clock={clock}
          disabled={!anyAudio || !readyCount}
          onToggle={toggle}
        />
        <TransportTime clock={clock} />
        <ToggleButtonGroup
          size="small"
          exclusive
          value={rate}
          onChange={(_, v) => v && setRate(v)}
          aria-label="Playback speed"
          sx={{
            "& .MuiToggleButton-root": {
              height: 26,
              px: 1,
              typography: "s3",
              textTransform: "none",
            },
          }}
        >
          {RATES.map((r) => (
            <ToggleButton key={r} value={r} aria-label={`${r}× speed`}>
              {`${r}×`}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <Button
          size="small"
          color="inherit"
          variant="outlined"
          disabled={!anyAudio}
          onClick={(e) => setSoloAnchor(e.currentTarget)}
          endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
          sx={{ height: 26, typography: "s3", borderColor: "divider" }}
          data-solo={solo ?? "all"}
        >
          {solo
            ? `Solo: ${laneName(lanes.find((l) => l.runId === solo)?.label ?? "")}`
            : `Solo: ${lanes.length > 2 ? "every lane" : "both"}`}
        </Button>
        <CustomTooltip
          show
          size="small"
          arrow
          title="Replay the focused turn on every lane until you turn it off"
        >
          <span>
            <ToggleButton
              size="small"
              value="loop"
              selected={loopOn}
              disabled={!anyAudio || !focusStep}
              onChange={() => setLoopOn((v) => !v)}
              sx={{
                height: 26,
                px: 1,
                typography: "s3",
                textTransform: "none",
              }}
            >
              <Iconify icon="mdi:repeat" width={14} sx={{ mr: 0.5 }} />
              Loop this turn
            </ToggleButton>
          </span>
        </CustomTooltip>
        <IconButton
          size="small"
          aria-label="More playback options"
          onClick={(e) => setMoreAnchor(e.currentTarget)}
          sx={{ ml: "auto" }}
        >
          <Iconify icon="mdi:dots-horizontal" width={18} />
        </IconButton>
        <Menu
          anchorEl={soloAnchor}
          open={Boolean(soloAnchor)}
          onClose={() => setSoloAnchor(null)}
          MenuListProps={{ dense: true }}
        >
          <MenuItem
            selected={!solo}
            onClick={() => {
              setSolo(null);
              setSoloAnchor(null);
            }}
          >
            <ListItemText primaryTypographyProps={{ typography: "s2" }}>
              {lanes.length > 2 ? "Every lane" : "Both lanes"}
            </ListItemText>
          </MenuItem>
          {lanes.map((l, i) => (
            <MenuItem
              key={l.runId}
              selected={solo === l.runId}
              disabled={!calls[i]?.recording?.agent?.url}
              onClick={() => {
                setSolo(l.runId);
                setSoloAnchor(null);
              }}
            >
              <ListItemIcon>
                <Box
                  sx={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    bgcolor: l.color,
                  }}
                />
              </ListItemIcon>
              <ListItemText primaryTypographyProps={{ typography: "s2" }}>
                {l.label}
              </ListItemText>
            </MenuItem>
          ))}
        </Menu>
        <Menu
          anchorEl={moreAnchor}
          open={Boolean(moreAnchor)}
          onClose={() => setMoreAnchor(null)}
          MenuListProps={{ dense: true }}
        >
          <MenuItem
            onClick={() => setAlign((v) => !v)}
            data-align-turns={align ? "on" : "off"}
          >
            <ListItemIcon>
              <Iconify
                icon={
                  align
                    ? "mdi:checkbox-marked-outline"
                    : "mdi:checkbox-blank-outline"
                }
                width={18}
              />
            </ListItemIcon>
            <ListItemText
              primaryTypographyProps={{ typography: "s2" }}
              secondary="Clicking a turn moves every lane to its own matching turn (calls drift in time)"
              secondaryTypographyProps={{
                typography: "s3",
                sx: { whiteSpace: "normal", maxWidth: 280 },
              }}
            >
              Align on turns
            </ListItemText>
          </MenuItem>
          <MenuItem
            onClick={() => setNormalize((v) => !v)}
            disabled={!anyAudio}
          >
            <ListItemIcon>
              <Iconify
                icon={
                  normalize
                    ? "mdi:checkbox-marked-outline"
                    : "mdi:checkbox-blank-outline"
                }
                width={18}
              />
            </ListItemIcon>
            <ListItemText
              primaryTypographyProps={{ typography: "s2" }}
              secondary="Play every lane at the quietest lane's loudness (from the waveform peaks)"
              secondaryTypographyProps={{
                typography: "s3",
                sx: { whiteSpace: "normal", maxWidth: 280 },
              }}
            >
              Normalize loudness
            </ListItemText>
          </MenuItem>
          <MenuItem
            onClick={() => {
              patchPrefs({
                extras: { waveExpanded: waveKeep ? undefined : true },
              });
              setFolded(false);
            }}
            data-wave-keep={waveKeep ? "on" : "off"}
          >
            <ListItemIcon>
              <Iconify
                icon={
                  waveKeep
                    ? "mdi:checkbox-marked-outline"
                    : "mdi:checkbox-blank-outline"
                }
                width={18}
              />
            </ListItemIcon>
            <ListItemText
              primaryTypographyProps={{ typography: "s2" }}
              secondary="Keep the waveforms open while you scroll the transcript"
              secondaryTypographyProps={{
                typography: "s3",
                sx: { whiteSpace: "normal", maxWidth: 280 },
              }}
            >
              Show waveforms
            </ListItemText>
          </MenuItem>
        </Menu>
      </Stack>

      {/* Audio lanes (folded: kept mounted so play still works, clipped
          under one scrub strip) */}
      {waveFolded ? (
        <ScrubStrip
          clock={clock}
          lanes={lanes}
          marks={diffMarks}
          onExpand={() => setFolded(false)}
        />
      ) : null}
      <Box
        data-audio-lanes={waveFolded ? "folded" : "open"}
        aria-hidden={waveFolded || undefined}
        sx={{
          flexShrink: 0,
          ...(waveFolded
            ? { height: 0, overflow: "hidden", visibility: "hidden" }
            : {
                minHeight: audioLanesHeight(
                  lanes,
                  Object.fromEntries(lanes.map((l, i) => [l.runId, calls[i]])),
                ),
              }),
        }}
      >
        <AudioLanes
          lanes={lanes}
          calls={Object.fromEntries(
            lanes.map((l, i) => [l.runId, isLoading ? null : calls[i]]),
          )}
          clock={clock}
          passedByRun={passedByRun}
          marks={diffMarks}
        />
      </Box>

      {/* Transcript diff (scrolls inside): the most room in the lens */}
      <Box
        data-call-transcript=""
        onWheelCapture={() => !folded && anyAudio && setFolded(true)}
        onTouchMoveCapture={() => !folded && anyAudio && setFolded(true)}
        sx={{
          flex: 1,
          minHeight: "max(150px, 40%)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {isLoading ? (
          <Stack gap={1} sx={{ p: 1 }}>
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} height={28} />
            ))}
          </Stack>
        ) : (
          <TranscriptDiff
            rows={rows}
            lanes={lanes}
            calls={calls}
            steps={steps}
            focusStep={focusStep}
            onFocusStep={focusOn}
            callerName={callerName}
          />
        )}
      </Box>

      {/* Latency: one line; the chart on demand (remembered) */}
      <Stack gap={0.25} sx={{ flexShrink: 0 }}>
        <Stack direction="row" alignItems="center" gap={1}>
          <Typography
            typography="s2"
            color="text.primary"
            data-slowest-turn={slow?.n ?? ""}
            noWrap
            sx={{ minWidth: 0, flex: "0 1 auto" }}
          >
            {isLoading
              ? " "
              : slow
                ? slowestLine(slow, names, { fmtMs })
                : "No response gaps to compare (text only or no recording)."}
          </Typography>
          <Button
            size="small"
            color="inherit"
            onClick={() =>
              patchPrefs({
                extras: { latencyChart: chartOn ? undefined : true },
              })
            }
            endIcon={
              <Iconify
                icon={chartOn ? "mdi:chevron-up" : "mdi:chevron-down"}
                width={14}
              />
            }
            sx={{ height: 24, typography: "s3", flexShrink: 0 }}
            data-timeline-toggle={chartOn ? "shown" : "hidden"}
            disabled={!latRows.length}
          >
            {chartOn ? "Hide chart" : "Show chart"}
          </Button>
          <Box sx={{ flex: 1 }} />
          {chartOn ? (
            <Button
              size="small"
              color="inherit"
              onClick={() =>
                patchPrefs({
                  extras: { timelineExpanded: expanded ? undefined : true },
                })
              }
              sx={{ height: 24, typography: "s3", flexShrink: 0 }}
              data-timeline-size={expanded ? "expanded" : "compact"}
            >
              {expanded ? "Smaller" : "Taller"}
            </Button>
          ) : null}
        </Stack>
        {chartOn ? (
          <Box sx={{ height: expanded ? EXPANDED_H : COMPACT_H }}>
            {latRows.length ? (
              <LatencyTimeline
                rows={latRows}
                lanes={lanes}
                names={names}
                thresholdMs={latencyThresholdMs}
                expanded={expanded}
                focusK={focusK}
                onFocusRow={(row) => {
                  const step = steps.find((s) => s.k === row.k && s.slot === 1);
                  focusOn(step);
                }}
              />
            ) : null}
          </Box>
        ) : null}
      </Stack>

      {/* Metrics + source */}
      <Box sx={{ flexShrink: 0 }}>
        {isLoading ? (
          <Skeleton height={64} />
        ) : (
          <CallMetricStrip
            lanes={lanes}
            calls={calls}
            compact={!metricsTable}
            onToggle={() =>
              patchPrefs({
                extras: { metricsTable: metricsTable ? undefined : true },
              })
            }
          />
        )}
      </Box>
    </Stack>
  );
}

CallLens.propTypes = {
  lanes: PropTypes.array,
  input: PropTypes.any,
  focusTurn: PropTypes.number,
  onFocusTurn: PropTypes.func,
  keysEnabled: PropTypes.bool,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  loading: PropTypes.bool,
  error: PropTypes.bool,
  onRetry: PropTypes.func,
  latencyThresholdMs: PropTypes.number,
};
