import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Chip, Stack, TextField, Typography } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import notify from "src/sections/common/ux/toast";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import { annotateKeys } from "src/api/annotation-queues/annotation-queues";
import { postCorrection, refreshCorrections } from "src/api/develop/review";
import {
  changed,
  isSubmitOf,
} from "src/sections/develop-detail/Review/correctionModel";

const asText = (v) =>
  v == null ? "" : typeof v === "string" ? v : JSON.stringify(v, null, 2);

/**
 * The expected answer of a review item, with `Correct` when the queue lets
 * reviewers correct it (W4-3): an inline editor with a word diff against
 * the current value. On Submit (the workspace's annotation submit) a
 * changed value is suggested to the dataset; the item then shows
 * `Correction suggested`. Absent when corrections are off.
 */
export default function CorrectExpectedField({ context }) {
  const c = context?.corrections;
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const latest = useRef(null);
  latest.current = { editing, draft, c };

  useEffect(() => {
    setEditing(false);
    setDraft("");
  }, [c?.item_id]);

  useEffect(
    () =>
      queryClient.getMutationCache().subscribe((event) => {
        const s = latest.current;
        if (!s?.editing || !s.c || !isSubmitOf(event, s.c)) return;
        if (!changed(asText(s.c.current), s.draft)) return;
        const at = s.c;
        postCorrection(at.dataset_id, {
          row_id: at.row_id,
          column_id: at.column_id,
          value: s.draft,
          source: {
            queue_id: at.queue_id,
            queue_name: at.queue_name,
            item_id: at.item_id,
          },
        })
          .then(() => {
            refreshCorrections(queryClient);
            queryClient.invalidateQueries({
              queryKey: annotateKeys.detail(at.queue_id, at.item_id),
            });
            notify(`Correction suggested · ${at.column_name}`, {
              variant: "success",
              key: "correction-suggested",
            });
          })
          .catch((e) =>
            notify(
              (typeof e?.result === "string" && e.result) ||
                "Could not suggest the correction.",
              { variant: "error", key: "correction-suggested" },
            ),
          );
        setEditing(false);
      }),
    [queryClient],
  );

  if (!c) return null;
  const current = asText(c.current);
  const suggestion = c.suggestion;
  return (
    <Box
      data-testid="correct-expected"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        p: 1.5,
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 0.75 }}>
        <Typography variant="subtitle2" sx={{ flex: 1 }}>
          Expected answer
          <Typography
            component="span"
            variant="caption"
            color="text.secondary"
            sx={{ ml: 0.75 }}
          >
            {c.column_name}
          </Typography>
        </Typography>
        {suggestion ? (
          <Chip
            size="small"
            color={suggestion.status === "dismissed" ? "default" : "primary"}
            variant="outlined"
            label={
              suggestion.status === "applied"
                ? "Correction saved"
                : suggestion.status === "dismissed"
                  ? "Correction dismissed"
                  : "Correction suggested"
            }
            sx={{ borderRadius: 0.5 }}
          />
        ) : null}
        {c.can_correct && !editing ? (
          <Button
            size="small"
            variant="outlined"
            startIcon={<Iconify icon="mdi:pencil-outline" width={14} />}
            onClick={() => {
              setDraft(
                suggestion?.status === "suggested"
                  ? asText(suggestion.after)
                  : current,
              );
              setEditing(true);
            }}
          >
            Correct
          </Button>
        ) : null}
      </Stack>
      {editing ? (
        <Stack spacing={1}>
          <TextField
            multiline
            minRows={3}
            fullWidth
            size="small"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            inputProps={{ "aria-label": `Corrected ${c.column_name}` }}
            onKeyDown={(e) => e.stopPropagation()}
          />
          {changed(current, draft) ? (
            <Box
              sx={{
                px: 1,
                py: 0.75,
                borderRadius: 0.5,
                bgcolor: "background.neutral",
              }}
            >
              <TextDiff from={current} to={draft} />
            </Box>
          ) : null}
          <Stack direction="row" alignItems="center" gap={1}>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ flex: 1 }}
            >
              {changed(current, draft)
                ? "Submit suggests this correction to the dataset."
                : "Edit the text to suggest a correction."}
            </Typography>
            <Button
              size="small"
              color="inherit"
              onClick={() => setEditing(false)}
            >
              Cancel
            </Button>
          </Stack>
        </Stack>
      ) : suggestion?.status === "suggested" ? (
        <Box
          sx={{
            px: 1,
            py: 0.75,
            borderRadius: 0.5,
            bgcolor: "background.neutral",
          }}
        >
          <TextDiff from={current} to={asText(suggestion.after)} />
        </Box>
      ) : (
        <ValueViewer value={c.current} emptyLabel="No expected answer yet" />
      )}
    </Box>
  );
}

CorrectExpectedField.propTypes = {
  // annotate-detail `review_context` (its `corrections` block, W4 C1)
  context: PropTypes.object,
};
