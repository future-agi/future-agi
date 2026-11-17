// The R4 lens lab (prototype only):
// `/dashboard/develop/r4-lab?lens=<kind>&dataset=<id>&row=<rowId>&runs=<ids>`
// shows one real row through the existing hooks in a ModalityLens inside a
// 1200 × 720 frame, then every MediaValue size. `?spike=1` is the sync
// spike (two call lanes on one SyncClock, with a live time readout);
// `?logos=1` keeps F's logo check.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { Link as RouterLink, useSearchParams } from "react-router-dom";
import { callOf, mediaRefOf } from "src/sections/common/Media/mediaRef";
import AudioLanes from "./AudioLanes";
import { createSyncClock } from "./SyncClock";
import LogoLab from "./LogoLab";
import useLabData, { LAB_DEFAULTS, labParams } from "./useLabData";
import ModalityLens from "./ModalityLens";
import { useViewUrlState } from "src/sections/common/ux/viewState";
import LabMediaSizes from "./LabMediaSizes";

const LENSES = ["call", "image", "document", "audio", "video"];

function SpikeReadout({ clock }) {
  const [, setN] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setN((n) => n + 1), 100);
    return () => clearInterval(id);
  }, []);
  const times = clock.laneTimes();
  return (
    <Typography
      typography="s2"
      component="pre"
      data-spike-times={JSON.stringify({
        master: clock.time(),
        lanes: times,
        playing: clock.isPlaying(),
        stats: clock.stats(),
        ready: clock.lanes(),
      })}
      sx={{ m: 0, fontFamily: "monospace" }}
    >
      {`master ${clock.time().toFixed(2)} s · ${Object.entries(times)
        .map(([id, t]) => `${id.slice(-3)} ${t.toFixed(2)} s`)
        .join(" · ")} · corrections ${clock.stats().corrections}`}
    </Typography>
  );
}

SpikeReadout.propTypes = { clock: PropTypes.object.isRequired };

function Spike({ lanes }) {
  const clock = useMemo(() => createSyncClock(), []);
  useEffect(() => {
    // Prototype page only: the walk reads the clock directly.
    window.__lensClock = clock;
    return () => {
      delete window.__lensClock;
      clock.destroy();
    };
  }, [clock]);
  const calls = Object.fromEntries(
    lanes.map((l) => [l.runId, callOf(l.value)]),
  );
  return (
    <Stack gap={1.5} data-spike="">
      <Typography typography="m3" fontWeight="fontWeightSemiBold">
        Sync spike · two players on one clock
      </Typography>
      <AudioLanes lanes={lanes} calls={calls} clock={clock} />
      <Stack direction="row" gap={1}>
        <Button size="small" variant="outlined" onClick={() => clock.play()}>
          Play
        </Button>
        <Button size="small" variant="outlined" onClick={() => clock.pause()}>
          Pause
        </Button>
        <Button
          size="small"
          variant="outlined"
          onClick={() => clock.seek(30)}
          data-spike-seek=""
        >
          Seek 30 s
        </Button>
      </Stack>
      <SpikeReadout clock={clock} />
    </Stack>
  );
}

Spike.propTypes = { lanes: PropTypes.arrayOf(PropTypes.object).isRequired };

export default function LensLab() {
  const [search] = useSearchParams();
  const lens = LENSES.includes(search.get("lens"))
    ? search.get("lens")
    : "call";
  const params = labParams(search, lens);
  const {
    lanes: dataLanes,
    row,
    isLoading,
    isError,
    refetch,
  } = useLabData(params);
  // No run in the world outputs video: the video lens shows this row's input
  // clip in every lane (said on the page) so the renderer is still walked.
  const inputClip = useMemo(
    () =>
      Object.values(row?.inputs ?? {}).find((v) => {
        const r = mediaRefOf(v);
        return (Array.isArray(r) ? r[0] : r)?.kind === "video";
      }) ?? null,
    [row],
  );
  const videoStandIn =
    lens === "video" &&
    inputClip &&
    !dataLanes.some((l) => {
      const r = mediaRefOf(l.value);
      return (Array.isArray(r) ? r[0] : r)?.kind === "video";
    });
  const lanes = useMemo(
    () =>
      videoStandIn
        ? dataLanes.map((l) => ({ ...l, value: inputClip }))
        : dataLanes,
    [videoStandIn, dataLanes, inputClip],
  );
  // The frozen URL keys a lens host owns: `turn` (call) and `lens` (image
  // mode), the way Task compare will pass them.
  const [view, setView] = useViewUrlState({ turn: null, mode: null });
  const [vote, setVote] = useState(null);
  if (search.get("logos") === "1") return <LogoLab />;

  return (
    <Box
      sx={{ px: 3, py: 2, display: "flex", flexDirection: "column", gap: 1.5 }}
    >
      <Stack direction="row" alignItems="baseline" gap={2} flexWrap="wrap">
        <Typography
          typography="m3"
          fontWeight="fontWeightSemiBold"
          component="h1"
        >
          Lens lab
        </Typography>
        <Typography typography="s2" color="text.secondary">
          {`Prototype page · ${params.note}${videoStandIn ? " · every lane shows that clip; shared play / seek not built" : ""}`}
        </Typography>
        {vote ? (
          <Typography
            typography="s2"
            color="text.secondary"
            data-lab-vote={vote}
          >
            {`onVote("${vote}")`}
          </Typography>
        ) : null}
        <Stack direction="row" gap={0.5} sx={{ ml: "auto" }}>
          {LENSES.map((k) => (
            <Button
              key={k}
              size="small"
              component={RouterLink}
              to={`?lens=${k}`}
              variant={k === lens ? "contained" : "text"}
              color={k === lens ? "primary" : "inherit"}
            >
              {k}
            </Button>
          ))}
        </Stack>
      </Stack>
      {search.get("spike") === "1" ? (
        row ? (
          <Spike lanes={lanes} />
        ) : (
          <Typography typography="s2">Loading the row…</Typography>
        )
      ) : (
        <>
          <Box
            data-lens-frame=""
            sx={{
              width: 1200,
              maxWidth: "100%",
              // A 1200 × 720 frame; on a 720 px tall screen it takes what is
              // left under the lab header (the lens scrolls inside).
              height: "min(720px, calc(100vh - 88px))",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <ModalityLens
              kind={lens === "document" ? "document" : lens}
              lanes={lanes}
              rowId={params.rowId}
              input={row?.inputs}
              expected={row?.expected}
              keysEnabled
              focusTurn={view.turn == null ? undefined : Number(view.turn)}
              onFocusTurn={(n) =>
                setView({ turn: n == null ? null : String(n) })
              }
              mode={view.mode ?? undefined}
              onModeChange={(m) => setView({ mode: m })}
              onVote={setVote}
              height="100%"
              loading={isLoading}
              error={isError}
              onRetry={refetch}
            />
          </Box>
          <LabMediaSizes lanes={lanes} row={row} />
        </>
      )}
    </Box>
  );
}

export { LAB_DEFAULTS };
