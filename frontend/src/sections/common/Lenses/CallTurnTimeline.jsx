// IT-R2 G4 P5 · the Graph tab of a call row (rule 15: same tab, different
// content — a call declares no agent graph): each call drawn as its turns
// on one time axis. Two tracks per run (caller grey, agent in the run's
// colour), interruptions marked ⚡ where the caller cut in, the reply
// latency over each answer, and tool calls as ticks on their turn. Labels
// are ≥ 11 px; hover a turn for its words; click to focus it (the Call tab
// opens on that turn). Text-only runs say so (no timing to draw).
import React, { useMemo, useRef, useState, useLayoutEffect } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import Term from "src/sections/common/ux/Term";
import { fmtMs } from "src/sections/common/format/fmt";
import { pairsOf } from "./lensModel";

const LABEL_W = 150;
const TRACK_H = 18;
const ROW_GAP = 4;
const CALLER = "#94A3B8";

/** Per-call drawing data: turns with times, tool ticks, ⚡ marks. Pure. */
export function timelineOf(call) {
  const turns = (call?.turns ?? []).filter(
    (t) =>
      (t.role === "caller" || t.role === "agent") &&
      Number.isFinite(t.start_ms),
  );
  const pairs = pairsOf(call);
  const turnNo = new Map();
  pairs.forEach((p, k) => {
    if (p.caller) turnNo.set(p.caller, k);
    p.agents.forEach((a) => turnNo.set(a, k));
  });
  return turns.map((t) => ({
    role: t.role,
    start: t.start_ms,
    end: Math.max(t.start_ms + 200, t.end_ms ?? t.start_ms + 800),
    text: t.text ?? "",
    turn: turnNo.get(t) ?? null,
    gap: Number.isFinite(t.gap_ms) ? t.gap_ms : null,
    cut: t.interrupted_by === "caller",
    tool: t.tool ?? null,
  }));
}

function useWidth(ref) {
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) =>
      setW(Math.round(e.contentRect.width)),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

export default function CallTurnTimeline({ lanes, calls, onFocusTurn }) {
  const ref = useRef(null);
  const width = useWidth(ref);
  const data = useMemo(
    () =>
      lanes.map((l, i) => ({
        lane: l,
        call: calls[i],
        items: timelineOf(calls[i]),
      })),
    [lanes, calls],
  );
  const maxMs = Math.max(
    1000,
    ...data.flatMap((d) => d.items.map((x) => x.end)),
    ...data.map((d) => d.call?.duration_ms ?? 0),
  );
  const plotW = Math.max(200, width - LABEL_W - 16);
  const x = (ms) => (ms / maxMs) * plotW;
  const ticks = [];
  const step = maxMs > 120000 ? 30000 : maxMs > 50000 ? 10000 : 5000;
  for (let t = 0; t <= maxMs; t += step) ticks.push(t);
  return (
    <Stack
      ref={ref}
      gap={1.25}
      data-testid="call-turn-timeline"
      sx={{ p: 1.5, minWidth: 0, overflow: "auto", flex: 1 }}
    >
      <Typography typography="s2" color="text.secondary">
        Each call&apos;s turns on one time axis · ⚡ the caller cut in (
        <Term k="bargeIn">kept talking</Term>) · the number over an answer is
        its reply latency · ▲ a tool call · click a turn to open it in Call.
      </Typography>
      {data.map(({ lane, call, items }) => {
        const textOnly = call?.depth === "text_only";
        return (
          <Stack
            key={lane.runId}
            direction="row"
            alignItems="flex-start"
            gap={1}
            data-timeline-lane={lane.runId}
            sx={{ minWidth: 0 }}
          >
            <Box sx={{ width: LABEL_W, flexShrink: 0, minWidth: 0, pt: 0.25 }}>
              <Stack direction="row" alignItems="center" gap={0.5}>
                <Box
                  sx={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    bgcolor: lane.color ?? "text.disabled",
                    flexShrink: 0,
                  }}
                />
                <Typography
                  typography="s2"
                  fontWeight={600}
                  noWrap
                  title={lane.label}
                >
                  {lane.label}
                </Typography>
              </Stack>
              <Typography typography="s3" color="text.secondary" noWrap>
                {textOnly
                  ? "Text only"
                  : `${items.filter((t) => t.cut).length} ⚡ · ${items.filter((t) => t.tool).length} tool calls`}
              </Typography>
            </Box>
            {textOnly || !items.length ? (
              <Box
                sx={{
                  flex: 1,
                  height: TRACK_H * 2 + ROW_GAP + 16,
                  display: "flex",
                  alignItems: "center",
                  px: 1.5,
                  borderRadius: 0.5,
                  border: "1px dashed",
                  borderColor: "divider",
                  bgcolor: "background.neutral",
                }}
              >
                <Typography typography="s2" color="text.secondary">
                  {textOnly
                    ? "Text only: no timing to draw. Its turns line up in Call and Steps."
                    : "No turns recorded for this call."}
                </Typography>
              </Box>
            ) : (
              <Box
                sx={{
                  position: "relative",
                  width: plotW,
                  height: 16 + TRACK_H * 2 + ROW_GAP,
                  flexShrink: 0,
                }}
              >
                {["caller", "agent"].map((role, ri) => (
                  <Box
                    key={role}
                    sx={{
                      position: "absolute",
                      left: 0,
                      right: 0,
                      top: 16 + ri * (TRACK_H + ROW_GAP),
                      height: TRACK_H,
                      borderRadius: 0.5,
                      bgcolor: "background.neutral",
                    }}
                  />
                ))}
                {items.map((t, i) => {
                  const top =
                    16 + (t.role === "caller" ? 0 : TRACK_H + ROW_GAP);
                  const left = x(t.start);
                  const w = Math.max(3, x(t.end) - left);
                  const tip = `${t.role === "caller" ? "Caller" : lane.label}${t.turn != null ? ` · turn ${t.turn}` : ""} · ${fmtMs(t.start)}${t.gap != null ? ` · replied in ${fmtMs(t.gap)}` : ""}${t.cut ? " · the caller cut in" : ""}${t.tool ? ` · ${t.tool.name}(${JSON.stringify(t.tool.args ?? {})})` : ""} — ${t.text.slice(0, 140)}`;
                  return (
                    <React.Fragment key={i}>
                      <CustomTooltip show title={tip} size="small">
                        <Box
                          role="button"
                          tabIndex={0}
                          aria-label={tip}
                          onClick={() =>
                            t.turn != null && onFocusTurn?.(t.turn)
                          }
                          onKeyDown={(e) =>
                            e.key === "Enter" &&
                            t.turn != null &&
                            onFocusTurn?.(t.turn)
                          }
                          sx={{
                            position: "absolute",
                            top,
                            left,
                            width: w,
                            height: TRACK_H,
                            borderRadius: "3px",
                            bgcolor: t.role === "caller" ? CALLER : lane.color,
                            opacity: 0.85,
                            cursor: t.turn != null ? "pointer" : "default",
                            "&:hover, &:focus-visible": {
                              opacity: 1,
                              outline: "2px solid",
                              outlineColor: "text.primary",
                            },
                          }}
                        />
                      </CustomTooltip>
                      {t.role === "agent" && t.gap != null && w > 26 ? (
                        <Typography
                          aria-hidden
                          sx={{
                            position: "absolute",
                            top: 0,
                            left,
                            fontSize: 11,
                            lineHeight: "14px",
                            color:
                              t.gap > 2000 ? "warning.dark" : "text.secondary",
                            fontVariantNumeric: "tabular-nums",
                            whiteSpace: "nowrap",
                            pointerEvents: "none",
                          }}
                        >
                          {fmtMs(t.gap)}
                        </Typography>
                      ) : null}
                      {t.cut ? (
                        <Box
                          aria-hidden
                          sx={{
                            position: "absolute",
                            top: 16 + TRACK_H - 2,
                            left: x(t.end) - 6,
                            fontSize: 12,
                            lineHeight: "12px",
                            pointerEvents: "none",
                          }}
                        >
                          ⚡
                        </Box>
                      ) : null}
                      {t.tool ? (
                        <Box
                          aria-hidden
                          sx={{
                            position: "absolute",
                            top: 16 + TRACK_H * 2 + ROW_GAP - 7,
                            left: left + Math.min(w, 10) - 5,
                            fontSize: 11,
                            lineHeight: "11px",
                            color: "text.primary",
                            pointerEvents: "none",
                          }}
                        >
                          ▲
                        </Box>
                      ) : null}
                    </React.Fragment>
                  );
                })}
              </Box>
            )}
          </Stack>
        );
      })}
      <Stack direction="row" gap={1} sx={{ minWidth: 0 }}>
        <Box sx={{ width: LABEL_W, flexShrink: 0 }} />
        <Box sx={{ position: "relative", width: plotW, height: 16 }}>
          {ticks.map((t) => (
            <Typography
              key={t}
              sx={{
                position: "absolute",
                left: x(t),
                transform: t ? "translateX(-50%)" : "none",
                fontSize: 11,
                color: "text.secondary",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {`${Math.round(t / 1000)} s`}
            </Typography>
          ))}
        </Box>
      </Stack>
    </Stack>
  );
}

CallTurnTimeline.propTypes = {
  lanes: PropTypes.arrayOf(PropTypes.object).isRequired,
  calls: PropTypes.array.isRequired,
  onFocusTurn: PropTypes.func,
};
