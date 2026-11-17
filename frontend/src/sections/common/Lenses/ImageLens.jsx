// The Image lens (ModalityLens kind="image"). Modes in one
// segmented control: Side by side (default, 2–4 lanes) · Slider (2 lanes) ·
// Overlay (opacity) · Difference (|A − B| on a same-origin canvas, gain,
// share of changed pixels) · Blink (`Q`; Auto-blink in `⋯`). Zoom and pan
// are synced across panes. Vote row `Which is better?` → `onVote(choice)`.
// Widget-local keys while the lens has focus: `0` fit, `+` / `−` zoom,
// `←` / `→` move the slider 5%, `shift+1` / `shift+2` / `shift+3` vote
// (never plain 1–3: those are Task compare's verdict keys).
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
  Collapse,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Slider,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import KeyCap from "src/sections/common/ux/KeyCap";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import { fmtPct, NUM_SX } from "src/sections/common/format/fmt";
import { mediaRefOf } from "src/sections/common/Media/mediaRef";
import LaneLabel from "./LaneLabel";
import { fitScale, imageDiff } from "./lensModel";

export const IMAGE_MODES = Object.freeze([
  { id: "side", label: "Side by side", min: 1 },
  { id: "slider", label: "Slider", min: 2, max: 2 },
  { id: "overlay", label: "Overlay", min: 2, max: 2 },
  { id: "difference", label: "Difference", min: 2, max: 2 },
  { id: "blink", label: "Blink", min: 2 },
]);
const ZOOMS = [1, 1.5, 2, 3, 4];
const VOTES = [
  { id: "a", label: "A better", key: "1" },
  { id: "tie", label: "Tie", key: "2" },
  { id: "b", label: "B better", key: "3" },
];

const firstImage = (value) => {
  const r = mediaRefOf(value, { columnType: undefined });
  const list = Array.isArray(r) ? r : r ? [r] : [];
  return list.find((x) => x.kind === "image") ?? null;
};

const parse = (v) => {
  if (v && typeof v === "object") return v;
  try {
    return JSON.parse(v);
  } catch {
    return null;
  }
};

/**
 * IT-R5 G4 S1: the lane's box score (`Box overlap ≥ 0.5`): the score that
 * measured an `expected_box` — `{display: "IoU 0.86", passed, expected}`.
 */
export function boxScoreOf(lane) {
  const scores = lane?.result?.scores ?? {};
  const s = Object.values(scores).find((x) => x?.measured?.expected_box);
  if (!s) return null;
  const e = s.measured.expected_box;
  return {
    display: s.display ?? null,
    passed: s.status === "passed",
    name: s.name ?? null,
    expected: Array.isArray(e)
      ? { x: e[0], y: e[1], w: e[2], h: e[3] }
      : e && Number.isFinite(e.x)
        ? e
        : null,
  };
}

/**
 * Boxes on a lane: `lane.boxes`, or `box` / `boxes` in a JSON output, plus
 * (IT-R5 G4 S1) the labelled box its score measured against, dashed.
 */
export function boxesOf(lane) {
  if (Array.isArray(lane?.boxes)) return lane.boxes;
  const exp = boxScoreOf(lane)?.expected;
  const expected = exp ? [{ ...exp, kind: "expected", label: "Expected" }] : [];
  const v = parse(lane?.value);
  const raw = v?.boxes ?? (v?.box ? [v.box] : null);
  if (!Array.isArray(raw)) return expected;
  return raw
    .map((b) =>
      Array.isArray(b)
        ? { x: b[0], y: b[1], w: b[2], h: b[3], label: "Box" }
        : b,
    )
    .filter((b) => Number.isFinite(b?.x))
    .concat(expected);
}

/** The input image(s) of a row: `[{name, ref}]` from an object of columns. */
export function inputImagesOf(input) {
  if (!input) return [];
  if (typeof input !== "object" || Array.isArray(input)) {
    const r = firstImage(input);
    return r ? [{ name: "input", ref: r }] : [];
  }
  return Object.entries(input)
    .map(([name, v]) => ({ name, ref: firstImage(v) }))
    .filter((x) => x.ref);
}

function useBoxSize() {
  const ref = useRef(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size];
}

/** One image at the shared zoom / pan, with box overlays. */
function Pane({ image, zoom, pan, onPan, boxes, showBoxes, style, children }) {
  const [ref, size] = useBoxSize();
  const w = image?.width || 640;
  const h = image?.height || 480;
  const base = fitScale(w, h, size.w - 8, size.h - 8);
  const s = base * zoom;
  const drag = useRef(null);
  return (
    <Box
      ref={ref}
      onPointerDown={(e) => {
        if (zoom <= 1) return;
        drag.current = { x: e.clientX, y: e.clientY, pan };
        e.currentTarget.setPointerCapture?.(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!drag.current) return;
        onPan({
          x: drag.current.pan.x + (e.clientX - drag.current.x),
          y: drag.current.pan.y + (e.clientY - drag.current.y),
        });
      }}
      onPointerUp={() => {
        drag.current = null;
      }}
      sx={{
        position: "relative",
        flex: 1,
        minHeight: 0,
        overflow: "hidden",
        bgcolor: (t) => (t.palette.mode === "dark" ? "grey.900" : "grey.100"),
        borderRadius: 0.5,
        cursor: zoom > 1 ? "grab" : "default",
        backgroundImage: (t) =>
          `linear-gradient(45deg, ${t.palette.action.hover} 25%, transparent 25%, transparent 75%, ${t.palette.action.hover} 75%), linear-gradient(45deg, ${t.palette.action.hover} 25%, transparent 25%, transparent 75%, ${t.palette.action.hover} 75%)`,
        backgroundSize: "16px 16px",
        backgroundPosition: "0 0, 8px 8px",
        ...style,
      }}
    >
      {image && size.w ? (
        <Box
          sx={{
            position: "absolute",
            left: "50%",
            top: "50%",
            width: w,
            height: h,
            transform: `translate(-50%, -50%) translate(${pan.x}px, ${pan.y}px) scale(${s})`,
            transformOrigin: "center",
          }}
        >
          {children ?? (
            <img
              src={image.url}
              alt={image.alt ?? image.name ?? "Output image"}
              width={w}
              height={h}
              draggable={false}
              style={{ display: "block", width: w, height: h }}
            />
          )}
          {showBoxes
            ? boxes.map((b, i) => (
                <Box
                  // eslint-disable-next-line react/no-array-index-key
                  key={i}
                  data-box={b.kind ?? "box"}
                  sx={{
                    position: "absolute",
                    left: b.x,
                    top: b.y,
                    width: b.w,
                    height: b.h,
                    border: `${2 / s}px ${b.kind === "expected" ? "dashed" : "solid"}`,
                    borderColor:
                      b.kind === "expected" ? "#16a34a" : b.color ?? "#e11d48",
                    pointerEvents: "none",
                  }}
                >
                  <Box
                    component="span"
                    sx={{
                      position: "absolute",
                      left: 0,
                      bottom: "100%",
                      fontSize: 12 / s,
                      lineHeight: 1.3,
                      px: 0.5 / s,
                      bgcolor:
                        b.kind === "expected"
                          ? "#16a34a"
                          : b.color ?? "#e11d48",
                      color: "#fff",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {b.label}
                  </Box>
                </Box>
              ))
            : null}
        </Box>
      ) : null}
      {!image ? (
        <Stack
          alignItems="center"
          justifyContent="center"
          sx={{ position: "absolute", inset: 0 }}
        >
          <Typography typography="s2" color="text.disabled">
            No image in this output
          </Typography>
        </Stack>
      ) : null}
    </Box>
  );
}

Pane.propTypes = {
  image: PropTypes.object,
  zoom: PropTypes.number.isRequired,
  pan: PropTypes.object.isRequired,
  onPan: PropTypes.func.isRequired,
  boxes: PropTypes.array,
  showBoxes: PropTypes.bool,
  style: PropTypes.object,
  children: PropTypes.node,
};

const loadImage = (url) =>
  new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = url;
  });

/** |A − B| drawn on a same-origin canvas, with the share of changed pixels. */
function DifferenceCanvas({ a, b, gain, onStats }) {
  const ref = useRef(null);
  const [error, setError] = useState(null);
  const w = a?.width || 640;
  const h = a?.height || 480;
  useEffect(() => {
    let alive = true;
    if (!a?.url || !b?.url) return undefined;
    Promise.all([loadImage(a.url), loadImage(b.url)])
      .then(([ia, ib]) => {
        if (!alive || !ref.current) return;
        const read = (img) => {
          const c = document.createElement("canvas");
          c.width = w;
          c.height = h;
          const ctx = c.getContext("2d");
          ctx.drawImage(img, 0, 0, w, h);
          return ctx.getImageData(0, 0, w, h).data;
        };
        const d = imageDiff(read(ia), read(ib), { gain });
        const ctx = ref.current.getContext("2d");
        ctx.putImageData(new ImageData(d.pixels, w, h), 0, 0);
        setError(null);
        onStats?.(d);
      })
      .catch(() => {
        if (alive)
          setError(
            "The difference needs both images on this site; one comes from another domain.",
          );
      });
    return () => {
      alive = false;
    };
  }, [a, b, gain, w, h, onStats]);
  return (
    <>
      <canvas
        ref={ref}
        width={w}
        height={h}
        style={{ display: "block", width: w, height: h }}
        data-diff-canvas=""
      />
      {error ? (
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ position: "absolute", inset: 0, p: 2 }}
        >
          {error}
        </Typography>
      ) : null}
    </>
  );
}

DifferenceCanvas.propTypes = {
  a: PropTypes.object,
  b: PropTypes.object,
  gain: PropTypes.number,
  onStats: PropTypes.func,
};

const letterOf = (i) => String.fromCharCode(65 + i);

export default function ImageLens({
  lanes: rawLanes,
  input,
  mode: modeProp,
  onModeChange,
  onVote,
  keysEnabled = false,
  height,
}) {
  const lanes = useMemo(() => (rawLanes ?? []).slice(0, 4), [rawLanes]);
  const images = useMemo(() => lanes.map((l) => firstImage(l.value)), [lanes]);
  const boxes = useMemo(() => lanes.map(boxesOf), [lanes]);
  const anyBoxes = boxes.some((b) => b.length);
  const inputs = useMemo(() => inputImagesOf(input), [input]);
  const two = lanes.length >= 2;
  const [innerMode, setInnerMode] = useState("side");
  const requested = modeProp ?? innerMode;
  const modeDef = IMAGE_MODES.find((m) => m.id === requested) ?? IMAGE_MODES[0];
  const mode = lanes.length >= modeDef.min ? modeDef.id : "side";
  const setMode = useCallback(
    (m) => {
      if (onModeChange) onModeChange(m);
      else setInnerMode(m);
    },
    [onModeChange],
  );
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [slider, setSlider] = useState(0.5);
  const [opacity, setOpacity] = useState(0.5);
  const [gain, setGain] = useState(4);
  const [blinkIdx, setBlinkIdx] = useState(0);
  const [autoBlink, setAutoBlink] = useState(false);
  const [showBoxes, setShowBoxes] = useState(true);
  const [inputOpen, setInputOpen] = useState(false);
  const [moreAnchor, setMoreAnchor] = useState(null);
  const [voted, setVoted] = useState(null);
  const [diffStats, setDiffStats] = useState(null);
  const [active, setActive] = useState(false);
  const rootRef = useRef(null);

  const fitView = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };
  const zoomBy = (d) => {
    setZoom((z) => {
      const i = ZOOMS.indexOf(z);
      const next =
        ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, (i < 0 ? 0 : i) + d))];
      if (next === 1) setPan({ x: 0, y: 0 });
      return next;
    });
  };
  const vote = useCallback(
    (choice) => {
      setVoted(choice);
      onVote?.(choice);
    },
    [onVote],
  );
  const blink = useCallback(() => {
    if (!two) return;
    if (mode !== "blink") setMode("blink");
    setBlinkIdx((i) => (i + 1) % lanes.length);
  }, [two, mode, setMode, lanes.length]);

  useEffect(() => {
    if (!autoBlink || mode !== "blink") return undefined;
    const id = setInterval(
      () => setBlinkIdx((i) => (i + 1) % lanes.length),
      500,
    );
    return () => clearInterval(id);
  }, [autoBlink, mode, lanes.length]);

  const isActive = useCallback(() => {
    const el = rootRef.current;
    return Boolean(el && (active || el.contains(document.activeElement)));
  }, [active]);
  useShortcuts(
    "taskCompare",
    {
      blink: {
        run: blink,
        when: () => two && isActive(),
        reason: "Needs an image lens with two images",
      },
    },
    { enabled: keysEnabled, register: false },
  );

  const onKeyDown = (e) => {
    const t = e.target;
    if (t?.closest?.(".MuiSlider-root, input, textarea, [role=menu]")) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.shiftKey && /^Digit[123]$/.test(e.code) && two) {
      e.preventDefault();
      vote(VOTES[Number(e.code.slice(-1)) - 1].id);
      return;
    }
    if (e.shiftKey && e.key !== "+") return;
    if (e.key === "0") {
      e.preventDefault();
      fitView();
    } else if (e.key === "+" || e.key === "=") {
      e.preventDefault();
      zoomBy(1);
    } else if (e.key === "-" || e.key === "_") {
      e.preventDefault();
      zoomBy(-1);
    } else if (
      mode === "slider" &&
      (e.key === "ArrowLeft" || e.key === "ArrowRight")
    ) {
      e.preventDefault();
      setSlider((v) =>
        Math.max(0, Math.min(1, v + (e.key === "ArrowLeft" ? -0.05 : 0.05))),
      );
    }
  };

  const shared = { zoom, pan, onPan: setPan, showBoxes };
  const [A, B] = images;

  let body;
  if (mode === "side" || !two) {
    body = (
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: `repeat(${lanes.length || 1}, minmax(0, 1fr))`,
          gap: 1,
          flex: 1,
          minHeight: 0,
        }}
      >
        {lanes.map((l, i) => (
          <Stack
            key={l.runId}
            gap={0.5}
            sx={{ minHeight: 0, minWidth: 0 }}
            data-image-pane={l.runId}
          >
            <Stack
              direction="row"
              alignItems="center"
              gap={0.75}
              sx={{ minWidth: 0 }}
            >
              <Typography
                typography="s3"
                color="text.disabled"
                sx={{ flexShrink: 0 }}
              >
                {letterOf(i)}
              </Typography>
              <LaneLabel lane={l} dense />
            </Stack>
            <Pane image={images[i]} boxes={boxes[i]} {...shared} />
            <Typography
              typography="s3"
              color="text.disabled"
              noWrap
              sx={NUM_SX}
            >
              {(() => {
                // IT-R5 G4 S1: the score under each image (`IoU 0.86 ·
                // pass`), else its size and description.
                const sc = boxScoreOf(l);
                if (sc?.display)
                  return (
                    <Box
                      component="span"
                      data-image-score={sc.passed ? "pass" : "fail"}
                      sx={{
                        color: sc.passed ? "green.700" : "red.700",
                        fontWeight: 600,
                      }}
                    >
                      {`${sc.display} · ${sc.passed ? "pass" : "fail"}`}
                      <Box
                        component="span"
                        sx={{ color: "text.disabled", fontWeight: 400 }}
                      >
                        {sc.name ? ` (${sc.name})` : ""}
                      </Box>
                    </Box>
                  );
                return images[i]
                  ? `${images[i].width ?? "?"} × ${images[i].height ?? "?"}${images[i].alt ? ` · ${images[i].alt}` : ""}`
                  : "";
              })()}
            </Typography>
          </Stack>
        ))}
      </Box>
    );
  } else if (mode === "slider") {
    body = (
      <Stack sx={{ flex: 1, minHeight: 0 }} gap={0.5}>
        <Stack direction="row" justifyContent="space-between">
          <LaneLabel lane={lanes[0]} dense />
          <LaneLabel lane={lanes[1]} dense />
        </Stack>
        <Box
          sx={{ position: "relative", flex: 1, minHeight: 0, display: "flex" }}
          data-image-slider={Math.round(slider * 100)}
        >
          <Pane image={B} boxes={boxes[1]} {...shared} />
          <Pane
            image={A}
            boxes={boxes[0]}
            {...shared}
            style={{
              position: "absolute",
              inset: 0,
              clipPath: `inset(0 ${(1 - slider) * 100}% 0 0)`,
              background: "transparent",
            }}
          />
          <Box
            role="slider"
            aria-label="Slider position"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(slider * 100)}
            onPointerDown={(e) => {
              const box = e.currentTarget.parentElement.getBoundingClientRect();
              const moveTo = (x) =>
                setSlider(Math.max(0, Math.min(1, (x - box.left) / box.width)));
              moveTo(e.clientX);
              const mv = (ev) => moveTo(ev.clientX);
              const up = () => {
                window.removeEventListener("pointermove", mv);
                window.removeEventListener("pointerup", up);
              };
              window.addEventListener("pointermove", mv);
              window.addEventListener("pointerup", up);
            }}
            sx={{
              position: "absolute",
              top: 0,
              bottom: 0,
              left: `calc(${slider * 100}% - 8px)`,
              width: 16,
              cursor: "ew-resize",
              display: "flex",
              justifyContent: "center",
              "&::before": {
                content: '""',
                width: 2,
                height: "100%",
                bgcolor: "common.white",
                boxShadow: "0 0 0 1px rgba(0,0,0,0.4)",
              },
            }}
          />
        </Box>
      </Stack>
    );
  } else if (mode === "overlay") {
    body = (
      <Stack sx={{ flex: 1, minHeight: 0 }} gap={0.5}>
        <Typography typography="s3" color="text.secondary">
          {`${lanes[1].label.split(" ")[0]} over ${lanes[0].label.split(" ")[0]} at ${fmtPct(opacity)} opacity`}
        </Typography>
        <Box
          sx={{ position: "relative", flex: 1, minHeight: 0, display: "flex" }}
        >
          <Pane image={A} boxes={boxes[0]} {...shared} />
          <Pane
            image={B}
            boxes={[]}
            {...shared}
            style={{
              position: "absolute",
              inset: 0,
              opacity,
              background: "transparent",
            }}
          />
        </Box>
      </Stack>
    );
  } else if (mode === "difference") {
    body = (
      <Stack sx={{ flex: 1, minHeight: 0 }} gap={0.5}>
        <Typography
          typography="s3"
          color="text.secondary"
          data-diff-share={diffStats ? diffStats.share : ""}
        >
          {diffStats
            ? `${fmtPct(diffStats.share)} of pixels changed between ${lanes[0].label.split(" ")[0]} and ${lanes[1].label.split(" ")[0]} · bright = different`
            : "Comparing pixels…"}
        </Typography>
        <Pane
          image={A}
          boxes={[]}
          {...shared}
          style={{ bgcolor: "common.black", backgroundImage: "none" }}
        >
          <DifferenceCanvas a={A} b={B} gain={gain} onStats={setDiffStats} />
        </Pane>
      </Stack>
    );
  } else {
    const i = blinkIdx % lanes.length;
    body = (
      <Stack
        sx={{ flex: 1, minHeight: 0 }}
        gap={0.5}
        data-blink-showing={letterOf(i)}
      >
        <Stack direction="row" alignItems="center" gap={1}>
          <Typography typography="s3" color="text.disabled">
            {`Showing ${letterOf(i)}`}
          </Typography>
          <LaneLabel lane={lanes[i]} dense />
          <Typography typography="s3" color="text.disabled" sx={{ ml: "auto" }}>
            <KeyCap>Q</KeyCap> switches
          </Typography>
        </Stack>
        <Pane image={images[i]} boxes={boxes[i]} {...shared} />
      </Stack>
    );
  }

  return (
    <Stack
      ref={rootRef}
      tabIndex={0}
      onKeyDown={onKeyDown}
      onPointerEnter={() => setActive(true)}
      onPointerLeave={() => setActive(false)}
      data-modality-lens="image"
      data-image-mode={mode}
      aria-label="Image lens: compare the images side by side, with a slider, overlay, difference or blink; Shift+1, 2 or 3 to vote"
      sx={{
        height: height ?? "100%",
        minHeight: 0,
        p: 1.5,
        gap: 1,
        outline: "none",
        bgcolor: "background.paper",
        "&:focus-visible": {
          boxShadow: (t) => `inset 0 0 0 2px ${t.palette.primary.main}`,
        },
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <ToggleButtonGroup
          size="small"
          exclusive
          value={mode}
          onChange={(_, v) => v && setMode(v)}
          aria-label="Compare mode"
          sx={{
            "& .MuiToggleButton-root": {
              height: 28,
              px: 1.25,
              typography: "s2",
              textTransform: "none",
            },
          }}
        >
          {IMAGE_MODES.map((m) => {
            const off = lanes.length < m.min;
            const btn = (
              <ToggleButton
                key={m.id}
                value={m.id}
                disabled={off}
                data-mode={m.id}
              >
                {m.label}
                {m.id === "blink" ? (
                  <KeyCap
                    sx={{
                      ml: 0.75,
                      height: 18,
                      minWidth: 18,
                      typography: "s3",
                    }}
                  >
                    Q
                  </KeyCap>
                ) : null}
              </ToggleButton>
            );
            return off ? (
              <CustomTooltip
                key={m.id}
                show
                size="small"
                title="Needs two images"
              >
                <span>{btn}</span>
              </CustomTooltip>
            ) : (
              btn
            );
          })}
        </ToggleButtonGroup>
        {mode === "overlay" ? (
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            sx={{ width: 200 }}
          >
            <Typography typography="s3" color="text.secondary">
              Opacity
            </Typography>
            <Slider
              size="small"
              min={0}
              max={1}
              step={0.05}
              value={opacity}
              onChange={(_, v) => setOpacity(v)}
              aria-label="Overlay opacity"
            />
          </Stack>
        ) : null}
        {mode === "difference" ? (
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            sx={{ width: 200 }}
          >
            <Typography typography="s3" color="text.secondary">
              Gain
            </Typography>
            <Slider
              size="small"
              min={1}
              max={8}
              step={1}
              value={gain}
              onChange={(_, v) => setGain(v)}
              aria-label="Difference gain"
            />
          </Stack>
        ) : null}
        <Stack
          direction="row"
          alignItems="center"
          gap={0.25}
          sx={{ ml: "auto" }}
        >
          <KeyHintTooltip title="Zoom out" keys={["-"]}>
            <span>
              <IconButton
                size="small"
                aria-label="Zoom out"
                disabled={zoom <= 1}
                onClick={() => zoomBy(-1)}
              >
                <Iconify icon="mdi:magnify-minus-outline" width={18} />
              </IconButton>
            </span>
          </KeyHintTooltip>
          <KeyHintTooltip title="Fit" keys={["0"]}>
            <Button
              size="small"
              color="inherit"
              onClick={fitView}
              sx={{ minWidth: 48, height: 28, typography: "s3", ...NUM_SX }}
              data-zoom={zoom}
            >
              {zoom === 1 ? "Fit" : `${zoom}×`}
            </Button>
          </KeyHintTooltip>
          <KeyHintTooltip title="Zoom in" keys={["+"]}>
            <span>
              <IconButton
                size="small"
                aria-label="Zoom in"
                disabled={zoom >= ZOOMS[ZOOMS.length - 1]}
                onClick={() => zoomBy(1)}
              >
                <Iconify icon="mdi:magnify-plus-outline" width={18} />
              </IconButton>
            </span>
          </KeyHintTooltip>
          <IconButton
            size="small"
            aria-label="More image options"
            onClick={(e) => setMoreAnchor(e.currentTarget)}
          >
            <Iconify icon="mdi:dots-horizontal" width={18} />
          </IconButton>
        </Stack>
        <Menu
          anchorEl={moreAnchor}
          open={Boolean(moreAnchor)}
          onClose={() => setMoreAnchor(null)}
          MenuListProps={{ dense: true }}
        >
          <MenuItem
            disabled={!two}
            onClick={() => {
              setAutoBlink((v) => !v);
              if (mode !== "blink") setMode("blink");
            }}
          >
            <ListItemIcon>
              <Iconify
                icon={
                  autoBlink
                    ? "mdi:checkbox-marked-outline"
                    : "mdi:checkbox-blank-outline"
                }
                width={18}
              />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s2" }}>
              Auto-blink every 500 ms
            </ListItemText>
          </MenuItem>
          <MenuItem
            disabled={!anyBoxes}
            onClick={() => setShowBoxes((v) => !v)}
          >
            <ListItemIcon>
              <Iconify
                icon={
                  showBoxes && anyBoxes
                    ? "mdi:checkbox-marked-outline"
                    : "mdi:checkbox-blank-outline"
                }
                width={18}
              />
            </ListItemIcon>
            <ListItemText
              primaryTypographyProps={{ typography: "s2" }}
              secondary={
                anyBoxes
                  ? "The labelled box (dashed) over each output"
                  : "No boxes in these outputs"
              }
              secondaryTypographyProps={{ typography: "s3" }}
            >
              {boxes.some((b) => b.some((x) => x.kind === "expected"))
                ? "Show expected"
                : "Show boxes"}
            </ListItemText>
          </MenuItem>
          <MenuItem disabled>
            <ListItemText primaryTypographyProps={{ typography: "s3" }}>
              {`Zoom ${zoom}× · drag to pan when zoomed`}
            </ListItemText>
          </MenuItem>
        </Menu>
      </Stack>

      {inputs.length ? (
        <Box>
          <Button
            size="small"
            color="inherit"
            onClick={() => setInputOpen((v) => !v)}
            startIcon={
              <Iconify
                icon={inputOpen ? "mdi:chevron-down" : "mdi:chevron-right"}
                width={16}
              />
            }
            sx={{ height: 24, typography: "s3", color: "text.secondary" }}
            data-input-toggle={inputOpen ? "open" : "closed"}
          >
            {`Input image · ${inputs[0].name}${inputs.length > 1 ? ` (+${inputs.length - 1})` : ""}`}
          </Button>
          <Collapse in={inputOpen} unmountOnExit>
            <Stack direction="row" gap={1} sx={{ height: 120, mt: 0.5 }}>
              {inputs.slice(0, 4).map((x) => (
                <Box key={x.name} sx={{ height: "100%" }}>
                  <img
                    src={x.ref.url}
                    alt={x.ref.alt ?? `Input · ${x.name}`}
                    style={{
                      height: "100%",
                      maxWidth: 240,
                      objectFit: "contain",
                      imageRendering: "auto",
                    }}
                  />
                </Box>
              ))}
            </Stack>
          </Collapse>
        </Box>
      ) : null}

      <Box
        sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}
      >
        {body}
      </Box>

      {two ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          data-vote-row={voted ?? ""}
        >
          <Typography typography="s2" color="text.secondary">
            Which is better?
          </Typography>
          {VOTES.map((v) => (
            <KeyHintTooltip
              key={v.id}
              title={`${v.label} (while the lens has focus)`}
              keys={[`shift+${v.key}`]}
            >
              <Button
                size="small"
                variant={voted === v.id ? "contained" : "outlined"}
                color={voted === v.id ? "primary" : "inherit"}
                onClick={() => vote(v.id)}
                sx={{ height: 28, typography: "s2", borderColor: "divider" }}
                data-vote={v.id}
              >
                {v.label}
              </Button>
            </KeyHintTooltip>
          ))}
          {voted ? (
            <Typography typography="s3" color="text.secondary">
              {`You picked: ${VOTES.find((v) => v.id === voted)?.label}`}
            </Typography>
          ) : null}
        </Stack>
      ) : null}
    </Stack>
  );
}

ImageLens.propTypes = {
  lanes: PropTypes.array,
  input: PropTypes.any,
  mode: PropTypes.string,
  onModeChange: PropTypes.func,
  onVote: PropTypes.func,
  keysEnabled: PropTypes.bool,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
};
