import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import RecordingAttachmentPlayer from "../attachments/RecordingAttachmentPlayer";
import { readRecordingPreview } from "../attachments/client";
import { conversationTimeline, clock } from "./conversationTimeline";

function SelectedRecording({ projectId, selection, ambiguous }) {
  const [attempt, setAttempt] = useState(0),
    [projection, setProjection] = useState(null);
  const [failure, setFailure] = useState(null),
    [active, setActive] = useState(null);
  const [canSeek, setCanSeek] = useState(false);
  const engine = useRef(null),
    unsubscribe = useRef(null),
    currentRows = useRef([]);
  useEffect(() => () => unsubscribe.current?.(), []);
  const onInstance = useCallback(({ multitrack }) => {
    unsubscribe.current?.();
    engine.current = multitrack;
    setCanSeek(Boolean(multitrack));
    unsubscribe.current = multitrack?.on("timeupdate", (seconds) => {
      // Prefer the explicit selection for simultaneous events. Never move focus.
      const hit = currentRows.current
        .filter((row) => row.seekMs !== null && row.seekMs <= seconds * 1000)
        .at(-1);
      setActive((prev) =>
        currentRows.current.some(
          (row) => row.key === prev && row.seekMs === hit?.seekMs,
        )
          ? prev
          : hit?.key ?? null,
      );
    });
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setProjection(null);
    setFailure(null);
    readRecordingPreview(
      projectId,
      selection.parent,
      selection.item,
      controller.signal,
    )
      .then((value) => {
        if (!controller.signal.aborted) setProjection(value);
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setFailure(error.retryable ? "retry" : "unavailable");
      });
    return () => controller.abort();
  }, [attempt, projectId, selection]);
  const rows = useMemo(
    () =>
      conversationTimeline({ ...selection, ambiguous }, projection?.manifest),
    [selection, ambiguous, projection],
  );
  useEffect(() => {
    currentRows.current = rows;
  }, [rows]);
  const canRefresh =
    failure === "retry" ||
    (!failure && projection && projection.state !== "failed");
  return (
    <>
      <Box
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 1,
          bgcolor: "background.paper",
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        {projection?.state === "ready" ? (
          <RecordingAttachmentPlayer
            variant="trace"
            projectId={projectId}
            attachment={projection}
            onInstance={onInstance}
          />
        ) : (
          <Stack direction="row" alignItems="center" spacing={1} sx={{ p: 2 }}>
            <Typography variant="body2" role="status">
              {failure || projection?.state === "failed"
                ? "Recording unavailable"
                : projection
                  ? "Recording is processing"
                  : "Preparing recording…"}
            </Typography>
            {canRefresh ? (
              <Button size="small" onClick={() => setAttempt((x) => x + 1)}>
                {failure ? "Retry" : "Refresh"}
              </Button>
            ) : null}
          </Stack>
        )}
      </Box>
      <Box sx={{ px: 2, py: 1.5 }}>
        <Typography variant="subtitle2" component="h3">
          Conversation timeline
        </Typography>
        {!rows.length && (
          <Typography variant="body2" sx={{ mt: 1, color: "text.secondary" }}>
            No transcript or activity captured.
          </Typography>
        )}
        <Box component="ol" sx={{ listStyle: "none", m: 0, p: 0 }}>
          {rows.map((r, index) => {
            const time =
              r.sortMs === null
                ? null
                : `${r.sortMs < 0 ? "−" : ""}${clock(Math.abs(r.sortMs))}`;
            const estimated = r.clockLabel === "Trace time";
            const content = (
              <>
                <Typography
                  variant="caption"
                  sx={{
                    color: "text.secondary",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {time && `${time} · ${r.clockLabel}`}
                </Typography>
                <Box>
                  <Typography component="span" variant="body2" fontWeight={600}>
                    {r.actor}
                  </Typography>
                  {r.actor !== r.kind && r.kind !== "Turn" && (
                    <Typography variant="caption" color="text.secondary">
                      {" "}
                      · {r.kind}
                    </Typography>
                  )}
                  <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>
                    {r.text}
                  </Typography>
                </Box>
                {r.durationMs !== undefined && (
                  <Typography variant="caption" color="text.secondary">
                    {r.durationMs} ms
                  </Typography>
                )}
              </>
            );
            const sx = {
              display: "grid",
              gridTemplateColumns: "minmax(5em, 20%) minmax(0, 1fr) auto",
              gap: 1,
              width: "100%",
              textAlign: "left",
              alignItems: "baseline",
              py: 1,
              px: 0.5,
              borderRadius: 0,
              textTransform: "none",
              color: "text.primary",
            };
            return (
              <Box
                component="li"
                key={r.key}
                sx={{ borderBottom: 1, borderColor: "divider" }}
              >
                {r.sortMs === null &&
                  (index === 0 || rows[index - 1].sortMs !== null) && (
                    <Typography variant="overline">
                      Time not captured
                    </Typography>
                  )}
                {r.seekMs === null || !canSeek ? (
                  <Box sx={sx}>{content}</Box>
                ) : (
                  <Button
                    color="inherit"
                    aria-pressed={active === r.key}
                    aria-label={`${estimated ? "Seek near" : "Seek to"} ${time}${estimated ? " (estimated from session start)" : ""}, ${r.actor}, ${r.text}`}
                    onClick={() => {
                      if (engine.current) {
                        engine.current.setTime(r.seekMs / 1000);
                        setActive(r.key);
                      }
                    }}
                    sx={{
                      ...sx,
                      bgcolor: active === r.key ? "action.selected" : undefined,
                    }}
                  >
                    {content}
                  </Button>
                )}
              </Box>
            );
          })}
        </Box>
      </Box>
    </>
  );
}
SelectedRecording.propTypes = {
  projectId: PropTypes.string.isRequired,
  selection: PropTypes.object.isRequired,
  ambiguous: PropTypes.bool,
};
export default function ConversationTab({ projectId, recordings }) {
  const [key, setKey] = useState(
    recordings.length === 1 ? recordings[0].key : null,
  );
  const selection = recordings.find((r) => r.key === key);
  return (
    <>
      {(recordings.length > 1 || !selection) && (
        <TextField
          select
          fullWidth
          size="small"
          label="Recording"
          SelectProps={{ native: true }}
          value={selection?.key || ""}
          onChange={(e) => setKey(e.target.value)}
          sx={{ my: 1 }}
        >
          {!selection && <option value="">Choose recording</option>}
          {recordings.map((r, i) => (
            <option key={r.key} value={r.key}>
              {i + 1} · {r.owner.observation_span.name || "Unnamed span"}
            </option>
          ))}
        </TextField>
      )}
      {selection && (
        <SelectedRecording
          key={`${selection.key}/${selection.item.status}/${selection.item.capture_complete}`}
          projectId={projectId}
          selection={selection}
          ambiguous={
            recordings.filter(
              (r) => r.parent.span_id === selection.parent.span_id,
            ).length > 1
          }
        />
      )}
    </>
  );
}
ConversationTab.propTypes = {
  projectId: PropTypes.string.isRequired,
  recordings: PropTypes.array.isRequired,
};
