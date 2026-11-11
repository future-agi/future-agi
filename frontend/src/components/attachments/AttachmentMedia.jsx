import { useCallback, useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import useAttachmentGrants from "./useAttachmentGrants";
import { MediaPlaceholder } from "./AttachmentImage";
import { unavailableLabels } from "./attachmentPlacement";

const PLAYABLE = {
  audio: new Set([
    "audio/wav",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/webm",
    "audio/flac",
  ]),
  video: new Set(["video/mp4", "video/webm"]),
};

/**
 * Native audio/video for one occurrence. No grant is requested and no byte is
 * fetched until the viewer asks; then the element streams Range requests from
 * the same-origin gateway (preload="metadata", never a whole-file download).
 * A renewed grant swaps the source in place and keeps position and play state.
 */
export default function AttachmentMedia({ projectId, request, item, label }) {
  const { modality, mime_type: mimeType } = item.reference;
  const [requested, setRequested] = useState(false);
  const [failed, setFailed] = useState(false);
  const elementRef = useRef(null);
  const { status, results, retry } = useAttachmentGrants(
    projectId,
    [request],
    { enabled: requested, renew: true },
  );
  const result = results[0];
  const path = status === "ready" && result?.status === "ready"
    ? result.gateway_path
    : null;

  useEffect(() => {
    const element = elementRef.current;
    if (!element || !path) return undefined;
    const position = element.currentTime || 0;
    const resume = element.src && !element.paused;
    const restore = () => {
      if (position) element.currentTime = position;
      if (resume) void element.play()?.catch?.(() => {});
    };
    element.addEventListener("loadedmetadata", restore, { once: true });
    element.src = path;
    element.load();
    return () => element.removeEventListener("loadedmetadata", restore);
  }, [path]);

  // A detached media element keeps playing and buffering, so stop it and drop
  // the grant-bearing source whenever React removes it.
  const attach = useCallback((node) => {
    const previous = elementRef.current;
    if (previous && previous !== node) {
      previous.pause();
      previous.removeAttribute("src");
      previous.load();
    }
    elementRef.current = node;
  }, []);

  const noun = modality === "video" ? "video" : "audio";
  if (item.status !== "ready")
    return <MediaPlaceholder status={item.status} height={64} />;
  if (!PLAYABLE[modality]?.has(mimeType))
    return <MediaPlaceholder status="unavailable" height={64} />;
  if (!requested)
    return (
      <Button
        size="small"
        color="inherit"
        variant="outlined"
        startIcon={<Iconify icon="solar:play-linear" width={14} />}
        onClick={() => setRequested(true)}
        aria-label={`Load ${noun}: ${label}`}
        sx={{ alignSelf: "flex-start" }}
      >
        {modality === "video" ? "Load video" : "Load audio"}
      </Button>
    );
  const unavailable =
    failed ||
    status === "unavailable" ||
    (status === "ready" && result?.status !== "ready");
  return (
    <Stack spacing={0.5}>
      {unavailable ? (
        <MediaPlaceholder
          status={failed ? "unavailable" : result?.status || "unavailable"}
          height={64}
        >
          <Button
            size="small"
            color="inherit"
            onClick={() => {
              setFailed(false);
              retry();
            }}
          >
            Retry
          </Button>
        </MediaPlaceholder>
      ) : (
        <Box
          component={modality === "video" ? "video" : "audio"}
          ref={attach}
          controls
          preload="metadata"
          aria-label={label}
          onError={() => setFailed(true)}
          sx={{
            width: "100%",
            maxHeight: modality === "video" ? 320 : undefined,
            borderRadius: "4px",
            bgcolor: modality === "video" ? "common.black" : undefined,
          }}
        />
      )}
      {status === "loading" && (
        <Typography variant="caption" color="text.secondary" role="status">
          {`Preparing ${noun}…`}
        </Typography>
      )}
    </Stack>
  );
}
AttachmentMedia.propTypes = {
  projectId: PropTypes.string.isRequired,
  request: PropTypes.object.isRequired,
  item: PropTypes.object.isRequired,
  label: PropTypes.string.isRequired,
};

const fileNames = { "application/pdf": "pdf", "application/octet-stream": "bin" };

/**
 * A file or PDF chip. Nothing is rendered inline: there is no sandboxed PDF
 * derivative yet, and the original is never put in an iframe/object/embed.
 * Download mints a fresh grant only on click and saves via a same-origin
 * `download` link, so the browser never navigates to the bytes.
 */
export function AttachmentFile({ projectId, request, item, label }) {
  const [requested, setRequested] = useState(false);
  const { status, results, retry } = useAttachmentGrants(
    projectId,
    [request],
    { enabled: requested },
  );
  const result = results[0];
  const isPdf = item.reference.mime_type === "application/pdf";

  useEffect(() => {
    if (!requested || status === "loading" || status === "idle") return;
    if (status === "ready" && result?.status === "ready") {
      const link = document.createElement("a");
      link.href = result.gateway_path;
      link.download = `attachment-${item.reference.occurrence_id.slice(0, 8)}.${fileNames[item.reference.mime_type] || "bin"}`;
      link.rel = "noopener noreferrer";
      link.referrerPolicy = "no-referrer";
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      link.remove();
    }
    // A completed download releases the grant; a denial stays visible.
    if (status === "ready" && result?.status === "ready") setRequested(false);
  }, [requested, status, result, item.reference]);

  const blocked = item.status !== "ready";
  const denied =
    requested &&
    (status === "unavailable" ||
      (status === "ready" && result && result.status !== "ready"));
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{
        px: 1.5,
        py: 1,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
      }}
    >
      <Iconify
        icon={isPdf ? "solar:document-text-linear" : "solar:file-text-linear"}
        width={18}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Stack sx={{ minWidth: 0, flex: 1 }}>
        <Typography variant="body2" noWrap>
          {isPdf ? "PDF document" : "File"}
        </Typography>
        <Typography variant="caption" color="text.secondary" noWrap>
          {blocked
            ? unavailableLabels[item.status] || unavailableLabels.unavailable
            : denied
              ? unavailableLabels[result?.status] || unavailableLabels.unavailable
              : "Preview unavailable · download to inspect"}
        </Typography>
      </Stack>
      <Button
        size="small"
        color="inherit"
        variant="outlined"
        disabled={blocked || (requested && status === "loading")}
        startIcon={
          <Iconify icon="solar:download-minimalistic-linear" width={14} />
        }
        onClick={() => (requested ? retry() : setRequested(true))}
        aria-label={`Download ${label}`}
      >
        Download
      </Button>
    </Stack>
  );
}
AttachmentFile.propTypes = AttachmentMedia.propTypes;
