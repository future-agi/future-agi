// One renderer for every media value (R4-SPEC D9; lane M fills F's stub,
// frozen props + additive optional ones). Sizes: `cell` (32 px grid
// cells), `row` (row view), `card` (drawer cards), `lens` (inside a lens).
//   image    cell: up to 3 thumbs + `+n`, hover preview, click → lightbox
//            row / card: fitted (≤ 480 px tall, never past 2×), alt, `640 × 480`
//   audio    cell: MiniWaveform + `0:04` + ▶ (click to play, one at a time)
//            row / card: CustomAudioPlayer + speed + download
//   video    cell: poster 44 × 26 + duration; row / card: <video controls>
//   document cell: DocChip `contract.pdf · 4 pages`; row / card: PDF viewer
//   call     cell: CallChip; row / card: CallView
//   persona  PersonaChip (`Ana · calm`, traits in the tooltip)
// Anything mediaRefOf does not recognise renders as text, exactly as before.
import React, {
  Suspense,
  lazy,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import CustomAudioPlayer from "src/components/custom-audio/CustomAudioPlayer";
import Iconify from "src/components/iconify";
import { NUM_SX } from "src/sections/common/format/fmt";
import { useCall } from "src/api/develop/r4";
import { callOf, mediaRefOf } from "./mediaRef";
import { personaOf, fittedSize } from "./mediaModel";
import { claimAudio, onAudioChange, releaseAudio } from "./audioBus";
import { useSeekableUrls } from "./seekableUrl";
import { callIdOf } from "src/sections/common/Lenses/useLaneCalls";
import AudioCellPlayer from "./AudioCellPlayer";
import CallChip from "./CallChip";
import DocChip, { docChipText } from "./DocChip";
import ImageStrip from "./ImageStrip";
import MediaLightbox from "./MediaLightbox";
import PersonaChip from "./PersonaChip";
import VideoPoster from "./VideoPoster";

// The full call view (player + transcript) loads on first use: grid cells
// show a CallChip, so CustomCellRender's import graph stays light.
const CallViewLazy = lazy(() => import("./CallView"));
function CallView(props) {
  return (
    <Suspense
      fallback={
        <Typography typography="s2" color="text.disabled">
          Loading the call…
        </Typography>
      }
    >
      <CallViewLazy {...props} />
    </Suspense>
  );
}

const textOf = (v) =>
  v == null ? "" : typeof v === "string" ? v : JSON.stringify(v);

const MAX_H = { row: 480, card: 480, lens: 480 };

function FittedImage({ item, maxHeight, onOpen }) {
  const [ref, setRef] = useState(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref) return undefined;
    const measure = () => setWidth(ref.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(ref);
    return () => ro.disconnect();
  }, [ref]);
  const box = fittedSize(
    item.width || 0,
    item.height || 0,
    width || 480,
    maxHeight,
  );
  const sized = Boolean(item.width && item.height);
  return (
    <Stack
      ref={setRef}
      gap={0.5}
      sx={{ minWidth: 0, width: "100%" }}
      data-media-image=""
    >
      <Box
        component="button"
        type="button"
        onClick={onOpen}
        aria-label={`Open ${item.alt ?? item.name ?? "image"}`}
        sx={{
          p: 0,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
          bgcolor: "background.neutral",
          cursor: "zoom-in",
          alignSelf: "flex-start",
          overflow: "hidden",
          maxWidth: "100%",
        }}
      >
        <img
          src={item.url}
          alt={item.alt ?? item.name ?? "Image"}
          style={{
            display: "block",
            width: sized ? box.w : "auto",
            height: sized ? box.h : "auto",
            maxWidth: "100%",
            maxHeight,
            objectFit: "contain",
          }}
        />
      </Box>
      <Typography typography="s3" color="text.secondary" sx={NUM_SX} noWrap>
        {[
          sized ? `${item.width} × ${item.height}` : null,
          item.alt ?? item.name,
        ]
          .filter(Boolean)
          .join(" · ")}
      </Typography>
    </Stack>
  );
}

FittedImage.propTypes = {
  item: PropTypes.object.isRequired,
  maxHeight: PropTypes.number,
  onOpen: PropTypes.func,
};

function ImagesBlock({ items, maxHeight }) {
  const [open, setOpen] = useState(null);
  return (
    <>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns:
            items.length > 1 ? "repeat(auto-fill, minmax(160px, 1fr))" : "1fr",
          gap: 1,
        }}
      >
        {items.map((it, i) => (
          <FittedImage
            // eslint-disable-next-line react/no-array-index-key
            key={i}
            item={it}
            maxHeight={items.length > 1 ? Math.min(maxHeight, 200) : maxHeight}
            onOpen={() => setOpen(i)}
          />
        ))}
      </Box>
      <MediaLightbox
        open={open != null}
        items={items}
        index={open ?? 0}
        onClose={() => setOpen(null)}
      />
    </>
  );
}

ImagesBlock.propTypes = {
  items: PropTypes.array.isRequired,
  maxHeight: PropTypes.number,
};

const RATES = [1, 1.5, 2];
let audioSeq = 0;

/** Row / card audio: the reused CustomAudioPlayer + speed + download. */
function AudioBlock({ item }) {
  const idRef = useRef(null);
  if (!idRef.current) {
    audioSeq += 1;
    idRef.current = `audio-block-${audioSeq}`;
  }
  const seekable = useSeekableUrls([item.url]);
  const wsRef = useRef(null);
  const [rate, setRate] = useState(1);
  const [pauseToken, setPauseToken] = useState(null);
  useEffect(
    () =>
      onAudioChange((id) => {
        if (id && id !== idRef.current) setPauseToken(`${id}:${Date.now()}`);
      }),
    [],
  );
  useEffect(() => () => releaseAudio(idRef.current), []);
  useEffect(() => {
    try {
      wsRef.current?.setPlaybackRate?.(rate);
    } catch {
      /* not ready */
    }
  }, [rate]);
  const store = (key, data) => {
    const ws = data?.instance;
    if (!ws || ws === wsRef.current) return;
    wsRef.current = ws;
    try {
      ws.setPlaybackRate?.(rate);
      ws.on?.("play", () => claimAudio(idRef.current, () => ws.pause()));
      ws.on?.("pause", () => releaseAudio(idRef.current));
    } catch {
      /* ignore */
    }
  };
  return (
    <Stack gap={0.5} data-media-audio="">
      <Box sx={{ minHeight: 50, display: "flex", alignItems: "center" }}>
        {seekable.ready ? (
          <CustomAudioPlayer
            audioData={{ url: seekable.urls[0] }}
            cacheKey={`${idRef.current}`}
            pauseWhen={pauseToken}
            getWaveSurferInstance={() => null}
            storeWaveSurferInstance={store}
            updateWaveSurferInstance={() => {}}
          />
        ) : (
          <Box
            sx={{
              height: 40,
              width: "100%",
              borderRadius: 0.5,
              bgcolor: "background.neutral",
            }}
          />
        )}
      </Box>
      <Stack direction="row" alignItems="center" gap={1}>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={rate}
          onChange={(_, v) => v && setRate(v)}
          aria-label="Playback speed"
          sx={{
            "& .MuiToggleButton-root": {
              height: 22,
              px: 0.75,
              typography: "s3",
              textTransform: "none",
            },
          }}
        >
          {RATES.map((r) => (
            <ToggleButton key={r} value={r}>{`${r}×`}</ToggleButton>
          ))}
        </ToggleButtonGroup>
        <Typography
          typography="s3"
          color="text.secondary"
          noWrap
          sx={{ minWidth: 0 }}
        >
          {item.name ?? "Audio"}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Button
          size="small"
          color="inherit"
          component="a"
          href={seekable.urls[0] ?? undefined}
          download={item.name ?? "audio.wav"}
          disabled={!seekable.ready}
          startIcon={<Iconify icon="mdi:download" width={14} />}
          sx={{ typography: "s3", height: 22 }}
        >
          Download
        </Button>
      </Stack>
    </Stack>
  );
}

AudioBlock.propTypes = { item: PropTypes.object.isRequired };

// Chrome / Edge / Firefox / Safari report whether they render PDFs inline;
// without a viewer (some headless and locked-down browsers) the block shows
// the chip and `Open in new tab` instead of an empty frame.
const pdfInline = () =>
  typeof navigator === "undefined" || navigator.pdfViewerEnabled !== false;

function DocumentBlock({ item, maxHeight }) {
  const pdf = item.mime === "application/pdf" && pdfInline();
  const openable = /^(https?:|\/|blob:)/i.test(String(item.url ?? ""));
  return (
    <Stack gap={0.5} data-media-document="">
      <Stack direction="row" alignItems="center" gap={1}>
        <DocChip mediaRef={item} />
        {openable ? (
          <Button
            size="small"
            color="inherit"
            component="a"
            href={item.url}
            target="_blank"
            rel="noreferrer"
            startIcon={<Iconify icon="mdi:open-in-new" width={14} />}
            sx={{ typography: "s3", height: 24 }}
          >
            Open in new tab
          </Button>
        ) : null}
      </Stack>
      {pdf ? (
        <Box
          component="object"
          data={item.url}
          type="application/pdf"
          aria-label={docChipText(item)}
          sx={{
            width: "100%",
            height: Math.min(maxHeight, 420),
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 0.5,
          }}
        >
          <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
            {"This browser can't show the PDF inline. Use Open in new tab."}
          </Typography>
        </Box>
      ) : null}
    </Stack>
  );
}

DocumentBlock.propTypes = {
  item: PropTypes.object.isRequired,
  maxHeight: PropTypes.number,
};

/** A call whose value was cut (grid JSON): read it whole by its id. */
function CallById({ id, size, compact, onOpen }) {
  const { data, isLoading } = useCall(id);
  if (!data)
    return (
      <Typography typography="s2" color="text.disabled">
        {isLoading ? "Loading the call…" : "Call not found"}
      </Typography>
    );
  return size === "cell" ? (
    <CallChip
      call={data}
      compact={compact}
      onOpen={onOpen ? () => onOpen(data) : undefined}
    />
  ) : (
    <CallView call={data} />
  );
}

CallById.propTypes = {
  id: PropTypes.string.isRequired,
  size: PropTypes.string,
  compact: PropTypes.bool,
  onOpen: PropTypes.func,
};

export default function MediaValue({
  value,
  mediaRef,
  size = "cell",
  columnType,
  onOpen,
  maxItems = 3,
  dense = false,
  compact = false,
  maxHeight,
  textFallback,
}) {
  const call = useMemo(
    () => (mediaRef ? null : callOf(value)),
    [value, mediaRef],
  );
  const persona = useMemo(
    () => (mediaRef || call ? null : personaOf(value)),
    [value, mediaRef, call],
  );
  const ref = useMemo(
    () =>
      mediaRef ?? (call || persona ? null : mediaRefOf(value, { columnType })),
    [mediaRef, call, persona, value, columnType],
  );
  const h = maxHeight ?? MAX_H[size] ?? 480;

  if (call) {
    if (!Array.isArray(call.turns) && size !== "cell")
      return <CallById id={call.call_id} size={size} />;
    return size === "cell" ? (
      <CallChip
        call={call}
        compact={compact}
        wave={!dense}
        onOpen={onOpen ? () => onOpen(call) : undefined}
      />
    ) : (
      <CallView call={call} />
    );
  }
  if (!ref && typeof value === "string" && /"kind"\s*:\s*"call"/.test(value)) {
    const id = callIdOf(value);
    if (id)
      return <CallById id={id} size={size} compact={compact} onOpen={onOpen} />;
  }
  if (persona)
    return (
      <PersonaChip persona={persona} size={size === "cell" ? "cell" : "row"} />
    );
  if (!ref)
    return (
      textFallback ?? (
        <Typography
          component="span"
          typography={size === "cell" ? "s2" : "s1"}
          color="text.primary"
        >
          {textOf(value)}
        </Typography>
      )
    );

  const list = Array.isArray(ref) ? ref : [ref];
  const kind = list.every((x) => x.kind === list[0].kind)
    ? list[0].kind
    : "file";
  if (size === "cell") {
    if (kind === "image") return <ImageStrip items={list} max={maxItems} />;
    if (kind === "audio")
      return <AudioCellPlayer mediaRef={list[0]} dense={dense} />;
    if (kind === "video") return <VideoPoster mediaRef={list[0]} />;
    return (
      <Stack direction="row" gap={0.5} sx={{ minWidth: 0, overflow: "hidden" }}>
        {list.slice(0, maxItems).map((x, i) => (
          // eslint-disable-next-line react/no-array-index-key
          <DocChip key={i} mediaRef={x} />
        ))}
        {list.length > maxItems ? (
          <Typography
            typography="s3"
            color="text.secondary"
          >{`+${list.length - maxItems}`}</Typography>
        ) : null}
      </Stack>
    );
  }
  if (kind === "image") return <ImagesBlock items={list} maxHeight={h} />;
  if (kind === "audio")
    return (
      <Stack gap={1}>
        {list.map((x, i) => (
          // eslint-disable-next-line react/no-array-index-key
          <AudioBlock key={i} item={x} />
        ))}
      </Stack>
    );
  if (kind === "video")
    return (
      <Stack gap={1}>
        {list.map((x, i) => (
          <video
            // eslint-disable-next-line react/no-array-index-key
            key={i}
            src={x.url}
            controls
            preload="metadata"
            poster={x.poster_url}
            style={{
              maxWidth: "100%",
              maxHeight: h,
              display: "block",
              borderRadius: 4,
            }}
            data-media-video=""
          >
            <track kind="captions" />
          </video>
        ))}
      </Stack>
    );
  return (
    <Stack gap={1}>
      {list.map((x, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <DocumentBlock key={i} item={x} maxHeight={h} />
      ))}
    </Stack>
  );
}

MediaValue.propTypes = {
  value: PropTypes.any,
  mediaRef: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
  size: PropTypes.oneOf(["cell", "row", "card", "lens"]),
  columnType: PropTypes.string,
  onOpen: PropTypes.func,
  maxItems: PropTypes.number,
  dense: PropTypes.bool,
  // Additive (M): X's 72 px Matrix call cells; the row view's collapsed
  // block height; what to render when the value is not media.
  compact: PropTypes.bool,
  maxHeight: PropTypes.number,
  textFallback: PropTypes.node,
};
