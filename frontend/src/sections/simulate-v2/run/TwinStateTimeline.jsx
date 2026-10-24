import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Tooltip,
  IconButton,
  Slider,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { twinById, twinTimelineFor } from "../_mock/twins";
import TwinLogo from "./../components/TwinLogo";

/**
 * Turn-by-turn view of the twin sandbox during a run.
 *
 * Two axes readers actually care about:
 *   · time (which turn) — scrubber at the top
 *   · service (Slack, Notion, …) — one lane per service
 *
 * At any turn, each lane shows what mutations landed on that service
 * up to that point, with the events at the current turn highlighted.
 * A running "writes" counter per service headers the lane so
 * readers see at a glance which service the agent hit hardest.
 *
 * This is what nobody else in the twin space has: the state
 * evolution is *diffable*. A twin-end-state eval that fails 40% of
 * the way through a run tells you the failure mode; scrubbing the
 * timeline shows *which turn* caused it.
 */
export default function TwinStateTimeline({ envState, task }) {
  const timeline = useMemo(
    () => twinTimelineFor(envState, task),
    [envState, task],
  );
  const services = envState?.twinBacking?.services || [];
  const totalTurns = timeline.byTurn.length;
  const [turn, setTurn] = useState(Math.max(0, totalTurns - 1));

  if (!services.length) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
          This environment doesn&apos;t copy any outside services.
        </Typography>
      </Box>
    );
  }

  if (totalTurns === 0) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
          No turns recorded for this call.
        </Typography>
      </Box>
    );
  }

  const eventsBefore = timeline.events.filter((e) => e.turn <= turn);
  const eventsThisTurn = timeline.events.filter((e) => e.turn === turn);
  const currentText = timeline.byTurn[turn]?.text || "";
  const currentRole = timeline.byTurn[turn]?.role || "agent";

  const writesToTurn = Object.fromEntries(
    services.map((sId) => [
      sId,
      eventsBefore.filter((e) => e.service === sId && e.isWrite).length,
    ]),
  );

  const step = (delta) =>
    setTurn((prev) => Math.max(0, Math.min(totalTurns - 1, prev + delta)));

  return (
    <Box sx={{ p: 2 }}>
      {/*
        Scrubber row — big-enough tap targets on the step buttons,
        turn N/M readout in a monospace face so the digits don't wiggle,
        and the current step's own text as the caption so scrubbing feels
        anchored to what happened.
      */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          p: 1.5,
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "status.discovery.border",
          bgcolor: "status.discovery.bg",
          mb: 2,
        }}
      >
        <IconButton size="small" onClick={() => step(-1)} disabled={turn === 0}>
          <Iconify icon="solar:alt-arrow-left-linear" width={16} />
        </IconButton>
        <IconButton
          size="small"
          onClick={() => step(1)}
          disabled={turn === totalTurns - 1}
        >
          <Iconify icon="solar:alt-arrow-right-linear" width={16} />
        </IconButton>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "primary.main",
                fontFamily: "ui-monospace, Menlo, monospace",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              Turn {turn + 1} / {totalTurns}
            </Typography>
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                textTransform: "none",
                letterSpacing: 0.4,
              }}
            >
              · {currentRole === "agent" ? "Assistant" : currentRole}
            </Typography>
            <Box flex={1} />
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {eventsThisTurn.length} effect
              {eventsThisTurn.length === 1 ? "" : "s"} this turn
            </Typography>
          </Stack>
          <Typography
            noWrap
            sx={{ typography: "s2", color: "text.primary", mt: 0.25 }}
          >
            {currentText || (
              <Box
                component="span"
                sx={{ color: "text.tertiary", fontStyle: "italic" }}
              >
                —
              </Box>
            )}
          </Typography>
        </Box>
      </Stack>

      <Slider
        size="small"
        value={turn}
        onChange={(_, v) => setTurn(v)}
        min={0}
        max={Math.max(0, totalTurns - 1)}
        marks={timeline.byTurn.map((t) => ({ value: t.turn, label: "" }))}
        sx={{
          mb: 2,
          color: "primary.main",
          "& .MuiSlider-mark": {
            width: 3,
            height: 8,
            borderRadius: "4px",
            /* Marks at turns where twin effects landed get a purple dot,
               idle turns stay grey — the shape of the run is legible
               from the scrubber alone. */
            bgcolor: (t) => alpha(t.palette.text.disabled, 0.4),
          },
          ...activeMarkSx(timeline),
        }}
      />

      <Stack spacing={1.5}>
        {services.map((sId) => {
          const twin = twinById(sId);
          const laneEvents = eventsBefore.filter((e) => e.service === sId);
          const nowEvents = eventsThisTurn.filter((e) => e.service === sId);
          const writes = writesToTurn[sId];
          const reads = laneEvents.filter((e) => !e.isWrite).length;
          return (
            <Box
              key={sId}
              sx={{
                borderRadius: "8px",
                border: "1px solid",
                borderColor: nowEvents.length
                  ? "status.discovery.border"
                  : "divider",
                bgcolor: "background.paper",
                overflow: "hidden",
              }}
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.25}
                sx={{
                  px: 2,
                  py: 1.25,
                  bgcolor: nowEvents.length
                    ? "status.discovery.bg"
                    : "background.neutral",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Box
                  sx={{
                    width: 24,
                    height: 24,
                    flexShrink: 0,
                    display: "grid",
                    placeItems: "center",
                  }}
                >
                  <TwinLogo twin={twin} width={18} />
                </Box>
                <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
                  {twin?.name || sId}
                </Typography>
                <LaneCounter label="reads" count={reads} tint="text.tertiary" />
                <LaneCounter
                  label="writes"
                  count={writes}
                  tint="primary.main"
                  bold
                />
              </Stack>

              {laneEvents.length === 0 ? (
                <Box sx={{ px: 2, py: 1.5 }}>
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      fontStyle: "italic",
                    }}
                  >
                    No effects on this service by turn {turn + 1}.
                  </Typography>
                </Box>
              ) : (
                <Stack sx={{ px: 2, py: 1.25 }} spacing={0.75}>
                  {laneEvents.map((e, i) => {
                    const highlight = e.turn === turn;
                    return (
                      <Stack
                        key={`${e.turn}-${i}`}
                        direction="row"
                        alignItems="flex-start"
                        spacing={1}
                        sx={{
                          px: 1,
                          py: 0.75,
                          borderRadius: "4px",
                          bgcolor: highlight
                            ? "status.discovery.bg"
                            : "transparent",
                          border: "1px solid",
                          borderColor: highlight
                            ? "status.discovery.border"
                            : "transparent",
                        }}
                      >
                        <Typography
                          sx={{
                            typography: "s3",
                            fontWeight: 600,
                            color: "text.tertiary",
                            fontFamily: "ui-monospace, Menlo, monospace",
                            fontVariantNumeric: "tabular-nums",
                            minWidth: 32,
                            flexShrink: 0,
                          }}
                        >
                          T{e.turn + 1}
                        </Typography>
                        <Tooltip
                          title={
                            e.isWrite
                              ? "Write — mutated the sandbox"
                              : "Read — inspected the sandbox"
                          }
                          arrow
                        >
                          <Box
                            sx={{ display: "flex", flexShrink: 0, mt: "1px" }}
                          >
                            <Iconify
                              icon={
                                e.isWrite
                                  ? "solar:pen-2-linear"
                                  : "solar:eye-linear"
                              }
                              width={12}
                              sx={{
                                color: e.isWrite
                                  ? "primary.main"
                                  : "text.tertiary",
                              }}
                            />
                          </Box>
                        </Tooltip>
                        <Typography
                          sx={{
                            typography: "s2",
                            flex: 1,
                            minWidth: 0,
                            color: e.isWrite
                              ? "text.primary"
                              : "text.secondary",
                            fontWeight: e.isWrite ? 500 : 400,
                          }}
                        >
                          {e.summary}
                        </Typography>
                      </Stack>
                    );
                  })}
                </Stack>
              )}
            </Box>
          );
        })}
      </Stack>

      <Box
        sx={{
          mt: 2,
          p: 1.5,
          borderRadius: 1,
          border: "1px dashed",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          Sandbox end state at run finish:{" "}
          {services.map((sId, i) => (
            <Box component="span" key={sId}>
              {i > 0 && " · "}
              <Box
                component="span"
                sx={{ color: "text.primary", fontWeight: 600 }}
              >
                {twinById(sId)?.name || sId}
              </Box>{" "}
              {timeline.writesByService[sId] || 0} writes
            </Box>
          ))}
          . Evals of kind{" "}
          <Box component="span" sx={{ color: "primary.main", fontWeight: 600 }}>
            Clone state
          </Box>{" "}
          inspect the final sandbox — not just whether the SDK was called.
        </Typography>
      </Box>
    </Box>
  );
}

TwinStateTimeline.propTypes = {
  envState: PropTypes.object,
  task: PropTypes.object,
};

/* ── bits ────────────────────────────────────────────────────────────────── */

function LaneCounter({ label, count, tint, bold }) {
  return (
    <Stack direction="row" alignItems="baseline" spacing={0.5}>
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          color: tint,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {count}
      </Typography>
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          fontWeight: bold ? 600 : 500,
        }}
      >
        {label}
      </Typography>
    </Stack>
  );
}
LaneCounter.propTypes = {
  label: PropTypes.string,
  count: PropTypes.number,
  tint: PropTypes.string,
  bold: PropTypes.bool,
};

/**
 * Style overrides for the scrubber marks so a mark that sits on a
 * turn with any twin effect gets the accent tone. Kept as a function
 * so the whole `sx` block above stays readable.
 */
function activeMarkSx(timeline) {
  const active = new Set(
    timeline.byTurn.filter((t) => t.events.length > 0).map((t) => t.turn),
  );
  const rules = {};
  active.forEach((v) => {
    rules[`& .MuiSlider-mark[data-index="${v}"]`] = {
      bgcolor: "primary.main",
      height: 10,
    };
  });
  return rules;
}
