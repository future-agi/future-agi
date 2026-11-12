import { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  DialogContent,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import { ClosableDialog } from "src/components/custom-dialog";
import useAttachmentGrants from "./useAttachmentGrants";
import { unavailableLabels } from "./attachmentPlacement";

const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);

export function canRenderImage(item) {
  return (
    item?.reference?.modality === "image" &&
    IMAGE_TYPES.has(item.reference.mime_type)
  );
}

/** Coarse placeholder used for every image/screenshot that cannot be shown. */
export function MediaPlaceholder({ status, height = 120, children }) {
  return (
    <Stack
      role="status"
      alignItems="center"
      justifyContent="center"
      spacing={0.5}
      sx={{
        height,
        width: "100%",
        border: "1px dashed",
        borderColor: "divider",
        borderRadius: "4px",
        color: "text.secondary",
        px: 1,
        textAlign: "center",
      }}
    >
      <Typography variant="caption" color="text.secondary">
        {unavailableLabels[status] || unavailableLabels.unavailable}
      </Typography>
      {children}
    </Stack>
  );
}
MediaPlaceholder.propTypes = {
  status: PropTypes.string,
  height: PropTypes.number,
  children: PropTypes.node,
};

/**
 * Full-size view with its OWN fresh grant: gateway grants are short-lived and
 * responses are no-store, so the inline image's path cannot be reused later.
 */
export function ImageZoomDialog({ open, onClose, projectId, request, label }) {
  const { status, results, retry } = useAttachmentGrants(
    projectId,
    request ? [request] : [],
    { enabled: open },
  );
  const [failed, setFailed] = useState(false);
  const result = results[0];
  const ready = status === "ready" && result?.status === "ready" && !failed;
  return (
    <ClosableDialog
      open={open}
      onClose={onClose}
      closeLabel="Close image"
      maxWidth="lg"
      aria-label={`${label} (full size)`}
      TransitionProps={{ onExited: () => setFailed(false) }}
    >
      <Stack
        direction="row"
        alignItems="center"
        sx={{
          pl: 2,
          pr: 6,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography variant="body2" fontWeight="fontWeightMedium">
          {label}
        </Typography>
      </Stack>
      <DialogContent sx={{ p: 2, display: "flex", justifyContent: "center" }}>
        {ready ? (
          <Box
            component="img"
            src={result.gateway_path}
            alt={label}
            referrerPolicy="no-referrer"
            onError={() => setFailed(true)}
            sx={{
              maxWidth: "85vw",
              maxHeight: "80vh",
              objectFit: "contain",
              display: "block",
            }}
          />
        ) : status === "loading" ? (
          <Skeleton variant="rectangular" width={480} height={320} />
        ) : (
          <Box sx={{ width: 480 }}>
            <MediaPlaceholder
              status={failed ? "unavailable" : result?.status || "unavailable"}
              height={200}
            >
              <ButtonBase
                onClick={() => {
                  setFailed(false);
                  retry();
                }}
                sx={{ fontSize: 12, textDecoration: "underline" }}
              >
                Retry
              </ButtonBase>
            </MediaPlaceholder>
          </Box>
        )}
      </DialogContent>
    </ClosableDialog>
  );
}
ImageZoomDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  projectId: PropTypes.string,
  request: PropTypes.object,
  label: PropTypes.string,
};

/**
 * One inline image. `result` comes from the span's single batched resolve.
 * The image is a same-origin gateway request carrying the media-session
 * cookie; no provider or object URL is ever used.
 */
export default function AttachmentImage({
  projectId,
  item,
  request,
  result,
  loading,
  label,
  onRetry,
}) {
  const [failed, setFailed] = useState(false);
  const [zoom, setZoom] = useState(false);
  if (item.status !== "ready") return <MediaPlaceholder status={item.status} />;
  if (!canRenderImage(item)) return <MediaPlaceholder status="unavailable" />;
  if (loading) return <Skeleton variant="rectangular" height={120} />;
  // A failed batch resolve leaves no result: show it as unavailable with a
  // retry rather than an endless skeleton.
  if (!result || result.status !== "ready" || failed)
    return (
      <MediaPlaceholder
        status={failed || !result ? "unavailable" : result.status}
      >
        {onRetry && (
          <ButtonBase
            onClick={() => {
              setFailed(false);
              onRetry();
            }}
            sx={{ fontSize: 12, textDecoration: "underline" }}
          >
            Retry
          </ButtonBase>
        )}
      </MediaPlaceholder>
    );
  return (
    <>
      <ButtonBase
        onClick={() => setZoom(true)}
        aria-label={`Open ${label} at full size`}
        sx={{
          display: "block",
          width: "100%",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          overflow: "hidden",
          bgcolor: "background.default",
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
          },
        }}
      >
        <Box
          component="img"
          src={result.gateway_path}
          alt={label}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
          sx={{
            display: "block",
            width: "100%",
            height: 120,
            objectFit: "contain",
          }}
        />
      </ButtonBase>
      <ImageZoomDialog
        open={zoom}
        onClose={() => setZoom(false)}
        projectId={projectId}
        request={request}
        label={label}
      />
    </>
  );
}
AttachmentImage.propTypes = {
  projectId: PropTypes.string.isRequired,
  item: PropTypes.object.isRequired,
  request: PropTypes.object,
  result: PropTypes.object,
  loading: PropTypes.bool,
  label: PropTypes.string.isRequired,
  onRetry: PropTypes.func,
};
