import { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { uuid } from "./contract";
import AttachmentImage, { canRenderImage } from "./AttachmentImage";
import AttachmentMedia, { AttachmentFile } from "./AttachmentMedia";
import ScreenshotFilmstrip from "./ScreenshotFilmstrip";
import useAttachmentGrants from "./useAttachmentGrants";
import {
  attachmentKind,
  collectScreenshots,
  groupByPosition,
  readSpanAttachments,
  spanParent,
} from "./attachmentPlacement";

function itemLabel(item, groupLabel) {
  const { modality, purpose } = item.reference;
  const noun =
    purpose === "conversation_recording"
      ? "recording"
      : modality === "document"
        ? "PDF"
        : modality;
  return `${groupLabel} ${noun}`;
}

/**
 * Attachments of one observation span, placed by message/tool position, plus
 * the trace's computer-use filmstrip when this span (or the root) has steps.
 *
 * Reads only the authorized `attachments` projection the trace API adds per
 * span; with the feature off that key is absent and this renders nothing. All
 * ready images of the span share ONE resolve request (one media-session
 * exchange); audio, video and files resolve only when the viewer asks.
 */
export default function SpanAttachments({
  projectId,
  span,
  allSpans,
  isRootSpan = false,
  traceStartTime,
  onSelectSpan,
}) {
  const validProject = uuid.safeParse(projectId).success;
  const parent = spanParent(span);
  const items = useMemo(() => readSpanAttachments(span), [span]);
  const frames = useMemo(
    () => (validProject ? collectScreenshots(allSpans) : []),
    [allSpans, validProject],
  );
  const groups = useMemo(
    () =>
      groupByPosition(
        items.filter((item) => attachmentKind(item.reference) !== "screenshot"),
      ),
    [items],
  );
  const requestFor = (item, representation) => ({
    occurrence_id: item.reference.occurrence_id,
    parent,
    representation,
  });
  const imageRequests = useMemo(
    () =>
      parent
        ? groups
            .flatMap((group) => group.items)
            .filter(
              (item) =>
                attachmentKind(item.reference) === "image" &&
                item.status === "ready" &&
                canRenderImage(item),
            )
            .map((item) => ({
              occurrence_id: item.reference.occurrence_id,
              parent,
              representation: "original",
            }))
        : [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [groups, parent?.trace_id, parent?.span_id],
  );
  const images = useAttachmentGrants(projectId, imageRequests, {
    enabled: validProject && imageRequests.length > 0,
  });
  const imageResults = useMemo(
    () =>
      new Map(
        images.results.map((result, i) => [
          imageRequests[i]?.occurrence_id,
          result,
        ]),
      ),
    [images.results, imageRequests],
  );

  const spanFrame = frames.findIndex(
    (frame) => frame.parent.span_id === parent?.span_id,
  );
  const showFilmstrip = frames.length > 0 && (spanFrame >= 0 || isRootSpan);
  if (!validProject || !parent || (!groups.length && !showFilmstrip))
    return null;

  return (
    <Stack spacing={1.5} sx={{ mt: 1.5 }} aria-label="Span attachments">
      {groups.length > 0 && (
        <Typography variant="body2" fontWeight="fontWeightMedium">
          Attachments
        </Typography>
      )}
      {groups.map((group) => {
        const groupImages = group.items.filter(
          (item) => attachmentKind(item.reference) === "image",
        );
        const others = group.items.filter(
          (item) => attachmentKind(item.reference) !== "image",
        );
        return (
          <Stack key={group.label} spacing={0.75}>
            <Typography variant="caption" color="text.secondary">
              {group.label}
            </Typography>
            {groupImages.length > 0 && (
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
                  gap: 1,
                }}
              >
                {groupImages.map((item) => (
                  <AttachmentImage
                    key={item.reference.occurrence_id}
                    projectId={projectId}
                    item={item}
                    request={requestFor(item, "original")}
                    result={imageResults.get(item.reference.occurrence_id)}
                    loading={
                      images.status === "loading" || images.status === "idle"
                    }
                    label={itemLabel(item, group.label)}
                    onRetry={images.retry}
                  />
                ))}
              </Box>
            )}
            {others.map((item) => {
              const kind = attachmentKind(item.reference);
              const Component =
                kind === "media" ? AttachmentMedia : AttachmentFile;
              return (
                <Component
                  key={item.reference.occurrence_id}
                  projectId={projectId}
                  item={item}
                  request={requestFor(
                    item,
                    kind === "media" ? "playable" : "original",
                  )}
                  label={itemLabel(item, group.label)}
                />
              );
            })}
          </Stack>
        );
      })}
      {showFilmstrip && (
        <ScreenshotFilmstrip
          projectId={projectId}
          frames={frames}
          initialIndex={Math.max(0, spanFrame)}
          currentSpanId={parent.span_id}
          traceStartTime={traceStartTime}
          onSelectSpan={onSelectSpan}
        />
      )}
    </Stack>
  );
}
SpanAttachments.propTypes = {
  projectId: PropTypes.string,
  span: PropTypes.object,
  allSpans: PropTypes.array,
  isRootSpan: PropTypes.bool,
  traceStartTime: PropTypes.oneOfType([
    PropTypes.string,
    PropTypes.number,
    PropTypes.instanceOf(Date),
  ]),
  onSelectSpan: PropTypes.func,
};
