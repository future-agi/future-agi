import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import { listAttachments } from "./client";
import AttachmentStatus from "./AttachmentStatus";

// Current page only; no preload, total count, media grant, or byte fetch.
export default function AttachmentsList({ projectId, parent, onSelect }) {
  return (
    <ScopedList
      key={`${projectId}/${parent?.trace_id}/${parent?.span_id}`}
      projectId={projectId}
      parent={parent}
      onSelect={onSelect}
    />
  );
}
AttachmentsList.propTypes = {
  projectId: PropTypes.string,
  parent: PropTypes.object,
  onSelect: PropTypes.func,
};

function ScopedList({ projectId, parent, onSelect }) {
  const [cursor, setCursor] = useState(null);
  const [page, setPage] = useState(null);
  const [status, setStatus] = useState("loading");
  const traceId = parent?.trace_id;
  const spanId = parent?.span_id;
  useEffect(() => {
    const controller = new AbortController();
    setPage(null);
    setStatus("loading");
    listAttachments(
      projectId,
      { trace_id: traceId, span_id: spanId },
      cursor,
      controller.signal,
    )
      .then((result) => {
        if (!controller.signal.aborted) {
          setPage(result);
          setStatus("ready");
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setStatus("not_found");
      });
    return () => controller.abort();
  }, [projectId, traceId, spanId, cursor]);
  return (
    <Stack spacing={1} sx={{ p: 2 }} aria-label="Attachments">
      <Typography variant="body2" fontWeight="fontWeightMedium">
        Attachments
      </Typography>
      {status !== "ready" ? (
        <AttachmentStatus status={status} />
      ) : (
        <>
          {page.items.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              No attachments available
            </Typography>
          )}
          {page.items.map((item) => (
            <Stack
              key={item.reference.occurrence_id}
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              spacing={2}
            >
              <Typography variant="body2">
                {item.reference.modality === "audio"
                  ? "Audio recording"
                  : "Attachment"}
              </Typography>
              {item.state === "ready" &&
              item.binding === "bound" &&
              onSelect ? (
                <Button
                  color="inherit"
                  size="small"
                  onClick={() => onSelect(item)}
                >
                  Open recording
                </Button>
              ) : (
                <Typography variant="caption" color="text.secondary">
                  {{
                    pending_upload: "Uploading",
                    importing: "Importing",
                    validating: "Validating",
                    expired: "Expired",
                    deleted: "Deleted",
                    failed: "Failed",
                  }[item.state] || "Unavailable"}
                </Typography>
              )}
            </Stack>
          ))}
          {page.next_cursor && (
            <Button
              color="inherit"
              onClick={() => {
                setPage(null);
                setStatus("loading");
                setCursor(page.next_cursor);
              }}
            >
              Next attachments
            </Button>
          )}
        </>
      )}
    </Stack>
  );
}
ScopedList.propTypes = AttachmentsList.propTypes;
