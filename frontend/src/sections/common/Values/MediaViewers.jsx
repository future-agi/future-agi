import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  IconButton,
  Link,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { dataUriBytes, fileNameOf } from "./sniff";
import { loadPdfjs } from "./pdfjsLoader";

/** `48 KB` / `1.2 MB`. */
export function formatBytes(n) {
  if (n == null || !Number.isFinite(n)) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024)
    return `${Math.round(n / 1024).toLocaleString("en-US")} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** `0:08` / `1:02:03`. */
export function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

const sizeCache = new Map();

/** Content length of a same-origin / CORS URL (one HEAD per URL), else null. */
export function useFileSize(url) {
  const [size, setSize] = useState(() => dataUriBytes(url));
  useEffect(() => {
    if (!url || url.startsWith("data:")) return undefined;
    let alive = true;
    if (!sizeCache.has(url))
      sizeCache.set(
        url,
        fetch(url, { method: "HEAD" })
          .then((r) => {
            const n = Number(r.headers.get("content-length"));
            return r.ok && Number.isFinite(n) && n > 0 ? n : null;
          })
          .catch(() => null),
      );
    sizeCache.get(url).then((n) => {
      if (alive && n) setSize(n);
    });
    return () => {
      alive = false;
    };
  }, [url]);
  return size;
}

const Broken = ({ url, what = "image" }) => (
  <Stack
    direction="row"
    alignItems="center"
    gap={0.75}
    sx={{
      px: 1.25,
      py: 1,
      borderRadius: 0.5,
      border: "1px dashed",
      borderColor: "divider",
      color: "text.secondary",
    }}
  >
    <Iconify icon="mdi:image-broken-variant" width={16} />
    <Typography typography="s2">{`Couldn't load this ${what}`}</Typography>
    {url && !url.startsWith("data:") && (
      <>
        <Typography typography="s2">·</Typography>
        <Link href={url} target="_blank" rel="noreferrer" typography="s2">
          Open URL
        </Link>
      </>
    )}
  </Stack>
);

Broken.propTypes = { url: PropTypes.string, what: PropTypes.string };

/** One lazily loaded image with a skeleton and a broken state. */
export const LazyImage = ({ src, alt = "", height = 160, onClick, sx }) => {
  const [state, setState] = useState("loading");
  if (state === "error") return <Broken url={src} />;
  return (
    <Box sx={{ position: "relative", height, ...sx }}>
      {state === "loading" && (
        <Skeleton
          variant="rounded"
          sx={{ position: "absolute", inset: 0, height: "100%" }}
        />
      )}
      <Box
        component="img"
        src={src}
        alt={alt}
        loading="lazy"
        onLoad={() => setState("ok")}
        onError={() => setState("error")}
        onClick={onClick}
        sx={{
          height: "100%",
          maxWidth: "100%",
          objectFit: "contain",
          borderRadius: 0.5,
          border: "1px solid",
          borderColor: "divider",
          cursor: onClick ? "zoom-in" : "default",
          visibility: state === "ok" ? "visible" : "hidden",
          display: "block",
        }}
      />
    </Box>
  );
};

LazyImage.propTypes = {
  src: PropTypes.string,
  alt: PropTypes.string,
  height: PropTypes.number,
  onClick: PropTypes.func,
  sx: PropTypes.object,
};

/** Images with a lightbox (← / → between them, Esc closes). */
export const ImageGallery = ({ urls }) => {
  const [open, setOpen] = useState(null);
  const list = (urls ?? []).filter(Boolean);
  const many = list.length > 1;
  return (
    <>
      <Stack direction="row" gap={1} flexWrap="wrap">
        {list.map((u, i) => (
          <LazyImage
            key={`${u}-${i}`}
            src={u}
            height={many ? 120 : 240}
            alt={fileNameOf(u)}
            onClick={() => setOpen(i)}
          />
        ))}
      </Stack>
      <Dialog
        open={open !== null}
        onClose={() => setOpen(null)}
        maxWidth="lg"
        onKeyDown={(e) => {
          if (!many) return;
          if (e.key === "ArrowRight") setOpen((i) => (i + 1) % list.length);
          if (e.key === "ArrowLeft")
            setOpen((i) => (i - 1 + list.length) % list.length);
        }}
      >
        {open !== null && (
          <Box sx={{ p: 1.5, position: "relative" }}>
            <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 1 }}>
              <Typography
                typography="s2"
                color="text.secondary"
                noWrap
                sx={{ flex: 1 }}
              >
                {many
                  ? `${open + 1} of ${list.length} · ${fileNameOf(list[open])}`
                  : fileNameOf(list[open])}
              </Typography>
              {!list[open].startsWith("data:") && (
                <Link
                  href={list[open]}
                  target="_blank"
                  rel="noreferrer"
                  typography="s2"
                >
                  Open ↗
                </Link>
              )}
              <IconButton
                size="small"
                aria-label="Close"
                onClick={() => setOpen(null)}
              >
                <Iconify icon="mdi:close" width={16} />
              </IconButton>
            </Stack>
            <Box
              component="img"
              src={list[open]}
              alt={fileNameOf(list[open])}
              sx={{
                maxWidth: "80vw",
                maxHeight: "75vh",
                display: "block",
                mx: "auto",
              }}
            />
          </Box>
        )}
      </Dialog>
    </>
  );
};

ImageGallery.propTypes = { urls: PropTypes.arrayOf(PropTypes.string) };

/** Native audio player with the duration and an optional transcript. */
export const AudioPlayer = ({ url, transcript }) => {
  const [duration, setDuration] = useState(null);
  const [showText, setShowText] = useState(false);
  const [error, setError] = useState(false);
  if (error) return <Broken url={url} what="audio" />;
  return (
    <Stack gap={0.75}>
      <Stack direction="row" alignItems="center" gap={1}>
        <Box
          component="audio"
          controls
          preload="metadata"
          src={url}
          onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
          onError={() => setError(true)}
          sx={{ flex: 1, minWidth: 0, height: 36 }}
        />
        {duration ? (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {formatDuration(duration)}
          </Typography>
        ) : null}
        {transcript ? (
          <Button
            size="small"
            variant="text"
            onClick={() => setShowText((s) => !s)}
            sx={{ typography: "s2", minWidth: 0 }}
          >
            {showText ? "Hide transcript" : "Transcript"}
          </Button>
        ) : null}
      </Stack>
      {showText && transcript && (
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ whiteSpace: "pre-wrap" }}
        >
          {transcript}
        </Typography>
      )}
    </Stack>
  );
};

AudioPlayer.propTypes = { url: PropTypes.string, transcript: PropTypes.string };

/** Native video player with a poster and the duration. */
export const VideoPlayer = ({ url, poster }) => {
  const [duration, setDuration] = useState(null);
  const [error, setError] = useState(false);
  if (error) return <Broken url={url} what="video" />;
  return (
    <Stack gap={0.5}>
      <Box
        component="video"
        controls
        preload="metadata"
        src={url}
        poster={poster}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onError={() => setError(true)}
        sx={{
          maxWidth: "100%",
          maxHeight: 280,
          borderRadius: 0.5,
          bgcolor: "common.black",
        }}
      />
      <Typography typography="s3" color="text.secondary">
        {[fileNameOf(url), duration ? formatDuration(duration) : null]
          .filter(Boolean)
          .join(" · ")}
      </Typography>
    </Stack>
  );
};

VideoPlayer.propTypes = { url: PropTypes.string, poster: PropTypes.string };

/** First page of a PDF (pdf.js) with `Open ↗` and `Download`. */
export const PdfPreview = ({ url, width = 360 }) => {
  const canvasRef = useRef(null);
  const [state, setState] = useState("loading");
  const [pages, setPages] = useState(null);
  const size = useFileSize(url);
  const isPdf =
    /\.pdf(\?|$)/i.test(url ?? "") ||
    (url ?? "").startsWith("data:application/pdf");

  useEffect(() => {
    if (!isPdf || !url) return undefined;
    let alive = true;
    let task;
    loadPdfjs()
      .then((pdfjs) => {
        task = pdfjs.getDocument({ url, isEvalSupported: false });
        return task.promise;
      })
      .then(async (doc) => {
        if (!alive) return;
        setPages(doc.numPages);
        const page = await doc.getPage(1);
        const base = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale: (width * 2) / base.width });
        const canvas = canvasRef.current;
        if (!canvas || !alive) return;
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        await page.render({ canvasContext: canvas.getContext("2d"), viewport })
          .promise;
        if (alive) setState("ok");
      })
      .catch(() => alive && setState("error"));
    return () => {
      alive = false;
      task?.destroy?.();
    };
  }, [url, width, isPdf]);

  const name = fileNameOf(url);
  return (
    <Stack gap={0.75} sx={{ maxWidth: width }}>
      {isPdf && state !== "error" && (
        <Box
          sx={{
            position: "relative",
            width,
            minHeight: state === "ok" ? 0 : width * 1.29,
          }}
        >
          {state === "loading" && (
            <Skeleton
              variant="rounded"
              sx={{ position: "absolute", inset: 0, height: "100%" }}
            />
          )}
          <Box
            component="canvas"
            ref={canvasRef}
            sx={{
              width,
              display: state === "ok" ? "block" : "none",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
              bgcolor: "common.white",
            }}
          />
        </Box>
      )}
      {state === "error" && <Broken url={url} what="document" />}
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Iconify icon="mdi:file-pdf-box" width={16} sx={{ color: "red.600" }} />
        <Typography
          typography="s2"
          color="text.primary"
          noWrap
          sx={{ minWidth: 0 }}
        >
          {name}
        </Typography>
        <Typography typography="s2" color="text.secondary">
          {[
            size ? formatBytes(size) : null,
            pages ? `${pages} ${pages === 1 ? "page" : "pages"}` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Link href={url} target="_blank" rel="noreferrer" typography="s2">
          Open ↗
        </Link>
        <CustomTooltip
          show
          size="small"
          placement="top"
          title={`Download ${name}`}
        >
          <Link href={url} download={name} typography="s2">
            Download
          </Link>
        </CustomTooltip>
      </Stack>
    </Stack>
  );
};

PdfPreview.propTypes = { url: PropTypes.string, width: PropTypes.number };
