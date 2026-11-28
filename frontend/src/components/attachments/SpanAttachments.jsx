import {
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useState,
} from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { SpanAttachmentsContext } from "./spanAttachmentsContext";
import InlineClipPlayer from "./InlineClipPlayer";
import { clipPlacement } from "./clipPlacement";
import { uuid } from "./contract";
import AttachmentImage, { canRenderImage } from "./AttachmentImage";
import AttachmentMedia, { AttachmentFile } from "./AttachmentMedia";
import ScreenshotFilmstrip from "./ScreenshotFilmstrip";
import useAttachmentGrants from "./useAttachmentGrants";
import {
  attachmentKind,
  collectScreenshots,
  groupByPosition,
  inlineImagesFor,
  positionLabel,
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

const imageGrid = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
  gap: 1,
};

/**
 * Shared state for one observation span's attachments.
 *
 * Wrap the span preview in it so chat messages can show their own images in
 * place (`MessageAttachments`) while `SpanAttachments` lists the rest. Every
 * ready image of the span shares ONE resolve request (one media-session
 * exchange), wherever it renders. Reads only the authorized `attachments`
 * projection the trace API adds per span; with the feature off that key is
 * absent and nothing renders or is requested.
 */
export function SpanAttachmentsProvider({
  projectId,
  span,
  enabled = true,
  traceMedia = false,
  callRecording = false,
  children,
}) {
  const active = enabled && uuid.safeParse(projectId).success;
  const parent = spanParent(span);
  const traceId = parent?.trace_id;
  const spanId = parent?.span_id;
  const items = useMemo(
    () => (active ? readSpanAttachments(span) : []),
    [active, span],
  );
  const [clipId, setClipId] = useState(null);
  const clips = useMemo(
    () =>
      active && traceMedia && spanId && traceId
        ? clipPlacement({ ...span?.metadata, ...span?.span_attributes }, items)
        : null,
    [span, items, active, traceMedia, spanId, traceId],
  );
  const imageRequests = useMemo(
    () =>
      traceId && spanId
        ? groupByPosition(
            items.filter(
              (item) =>
                attachmentKind(item.reference) === "image" &&
                item.status === "ready" &&
                canRenderImage(item),
            ),
          )
            .flatMap((group) => group.items)
            .map((item) => ({
              occurrence_id: item.reference.occurrence_id,
              parent: { trace_id: traceId, span_id: spanId },
              representation: "original",
            }))
        : [],
    [items, traceId, spanId],
  );
  const {
    status: imageStatus,
    results: grantResults,
    retry: retryImages,
  } = useAttachmentGrants(projectId, imageRequests, {
    enabled: active && imageRequests.length > 0,
  });
  const images = useMemo(
    () => ({ status: imageStatus, retry: retryImages }),
    [imageStatus, retryImages],
  );
  const imageResults = useMemo(
    () =>
      new Map(
        grantResults.map((result, i) => [
          imageRequests[i]?.occurrence_id,
          result,
        ]),
      ),
    [grantResults, imageRequests],
  );
  // Occurrences a mounted chat message shows in place, with a mount count.
  const [placed, setPlaced] = useState(() => new Map());
  const place = useCallback((ids) => {
    const change = (delta) =>
      setPlaced((previous) => {
        const next = new Map(previous);
        for (const id of ids) {
          const count = (next.get(id) || 0) + delta;
          if (count > 0) next.set(id, count);
          else next.delete(id);
        }
        return next;
      });
    change(1);
    return () => change(-1);
  }, []);

  const value = useMemo(
    () => ({
      active,
      projectId,
      parent: traceId && spanId ? { trace_id: traceId, span_id: spanId } : null,
      items,
      clips,
      callRecording,
      clipId,
      setClipId,
      images,
      imageResults,
      placed,
      place,
    }),
    [
      active,
      projectId,
      traceId,
      spanId,
      items,
      clips,
      callRecording,
      clipId,
      setClipId,
      images,
      imageResults,
      placed,
      place,
    ],
  );
  return (
    <SpanAttachmentsContext.Provider value={value}>
      {children}
    </SpanAttachmentsContext.Provider>
  );
}
SpanAttachmentsProvider.propTypes = {
  projectId: PropTypes.string,
  span: PropTypes.object,
  enabled: PropTypes.bool,
  traceMedia: PropTypes.bool,
  // The trace drawer plays this trace's call recording in its Conversation tab.
  callRecording: PropTypes.bool,
  children: PropTypes.node,
};

export function ClipControl({ item, label }) {
  const { projectId, parent, clipId, setClipId } = useContext(
    SpanAttachmentsContext,
  );
  const id = item.reference.occurrence_id;
  useLayoutEffect(
    () => () => setClipId((current) => (current === id ? null : current)),
    [id, setClipId],
  );
  return (
    <InlineClipPlayer
      projectId={projectId}
      parent={parent}
      item={item}
      label={label}
      active={clipId === id}
      onActivate={setClipId}
    />
  );
}
ClipControl.propTypes = {
  item: PropTypes.object.isRequired,
  label: PropTypes.string.isRequired,
};

function SpanImage({ context, item, label }) {
  const { projectId, parent, images, imageResults } = context;
  return (
    <AttachmentImage
      projectId={projectId}
      item={item}
      request={{
        occurrence_id: item.reference.occurrence_id,
        parent,
        representation: "original",
      }}
      result={imageResults.get(item.reference.occurrence_id)}
      loading={images.status === "loading" || images.status === "idle"}
      label={label}
      onRetry={images.retry}
    />
  );
}
SpanImage.propTypes = {
  context: PropTypes.object.isRequired,
  item: PropTypes.object.isRequired,
  label: PropTypes.string.isRequired,
};

/**
 * The images of one rendered chat message, at its position. Renders nothing
 * outside a `SpanAttachmentsProvider` or when no image is placed on it; an
 * image shown here is left out of the span's Attachments list.
 */
export function MessageAttachments({ direction, messageIndex, role }) {
  const context = useContext(SpanAttachmentsContext);
  const items = context?.active && context.parent ? context.items : null;
  const inline = useMemo(
    () =>
      items ? inlineImagesFor(items, { direction, messageIndex, role }) : [],
    [items, direction, messageIndex, role],
  );
  const ids = inline.map((item) => item.reference.occurrence_id).join(",");
  const place = context?.place;
  useLayoutEffect(() => {
    if (!place || !ids) return undefined;
    return place(ids.split(","));
  }, [place, ids]);
  if (!inline.length) return null;
  return (
    <Box
      data-search-skip="true"
      aria-label="Message images"
      sx={{ ...imageGrid, px: 1.5, pb: 1 }}
    >
      {inline.map((item) => (
        <SpanImage
          key={item.reference.occurrence_id}
          context={context}
          item={item}
          label={itemLabel(item, positionLabel(item.reference))}
        />
      ))}
    </Box>
  );
}
MessageAttachments.propTypes = {
  direction: PropTypes.oneOf(["input", "output"]).isRequired,
  messageIndex: PropTypes.number.isRequired,
  role: PropTypes.string,
};

/**
 * Attachments of one observation span that no chat message shows in place,
 * grouped by message/tool position, plus the trace's computer-use filmstrip
 * when this span (or the root) has steps. Audio, video and files resolve only
 * when the viewer asks.
 */
export default function SpanAttachments(props) {
  const context = useContext(SpanAttachmentsContext);
  if (context) return <SpanAttachmentsBody {...props} context={context} />;
  return (
    <SpanAttachmentsProvider projectId={props.projectId} span={props.span}>
      <SpanAttachmentsConsumer {...props} />
    </SpanAttachmentsProvider>
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

function SpanAttachmentsConsumer(props) {
  const context = useContext(SpanAttachmentsContext);
  return <SpanAttachmentsBody {...props} context={context} />;
}

function SpanAttachmentsBody({
  context,
  allSpans,
  isRootSpan = false,
  traceStartTime,
  onSelectSpan,
}) {
  const { active, projectId, parent, items, placed, clips, callRecording } =
    context;
  const frames = useMemo(
    () => (active ? collectScreenshots(allSpans) : []),
    [allSpans, active],
  );
  const groups = useMemo(
    () =>
      groupByPosition(
        items.filter(
          (item) =>
            attachmentKind(item.reference) !== "screenshot" &&
            !placed.has(item.reference.occurrence_id) &&
            !(
              callRecording &&
              item.reference.modality === "audio" &&
              item.reference.purpose === "conversation_recording"
            ) &&
            !clips?.placed.has(item.reference.occurrence_id),
        ),
      ),
    [items, placed, clips, callRecording],
  );

  const spanFrame = frames.findIndex(
    (frame) => frame.parent.span_id === parent?.span_id,
  );
  const showFilmstrip = frames.length > 0 && (spanFrame >= 0 || isRootSpan);
  if (!active || !parent || (!groups.length && !showFilmstrip)) return null;

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
              <Box sx={imageGrid}>
                {groupImages.map((item) => (
                  <SpanImage
                    key={item.reference.occurrence_id}
                    context={context}
                    item={item}
                    label={itemLabel(item, group.label)}
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
                  request={{
                    occurrence_id: item.reference.occurrence_id,
                    parent,
                    representation: kind === "media" ? "playable" : "original",
                  }}
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
SpanAttachmentsBody.propTypes = {
  context: PropTypes.object.isRequired,
  allSpans: PropTypes.array,
  isRootSpan: PropTypes.bool,
  traceStartTime: PropTypes.oneOfType([
    PropTypes.string,
    PropTypes.number,
    PropTypes.instanceOf(Date),
  ]),
  onSelectSpan: PropTypes.func,
};
