import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  Divider,
  FormControl,
  MenuItem,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import AnnotationHistory from "./annotation-history";
import { ALL_ANNOTATORS } from "./annotation-view-mode";

const VALUE_COLORS = {
  positive: "success",
  negative: "error",
  neutral: "default",
  empty: "default",
};

function stableStringify(value) {
  if (value === null || value === undefined) return "";
  if (typeof value !== "object") return String(value);
  return JSON.stringify(value, Object.keys(value).sort());
}

function formatAnnotationValue(value, labelType, labelSettings) {
  if (value === null || value === undefined) return "No annotation";
  const settings = labelSettings || {};

  switch (labelType) {
    case "categorical": {
      const selected = value?.selected;
      if (Array.isArray(selected)) return selected.join(", ") || "No answer";
      return String(value || "No answer");
    }
    case "star": {
      const rating = value?.rating;
      const max = settings.no_of_stars || 5;
      return rating == null ? "No rating" : `${rating} / ${max}`;
    }
    case "thumbs_up_down": {
      const v = value?.value;
      if (v === "up") return "Yes";
      if (v === "down") return "No";
      return "No answer";
    }
    case "numeric": {
      const num = value?.value ?? value;
      return num == null ? "No value" : String(num);
    }
    case "text":
      return value?.text || "No text";
    default:
      return typeof value === "object" ? JSON.stringify(value) : String(value);
  }
}

function valueTone(value, labelType) {
  if (value === null || value === undefined) return "empty";
  if (labelType === "thumbs_up_down") {
    if (value?.value === "up") return "positive";
    if (value?.value === "down") return "negative";
  }
  return "neutral";
}

function annotatorDisplayName(annotator, currentUserId) {
  const name = annotator?.name || annotator?.email || "Unknown";
  const id = annotator?.user_id || annotator?.id;
  return String(id) === String(currentUserId) ? `${name} (you)` : name;
}

function buildAnnotatorRows(annotators, annotations) {
  const rows = new Map();

  for (const annotator of annotators || []) {
    if (!annotator?.user_id) continue;
    rows.set(String(annotator.user_id), {
      id: String(annotator.user_id),
      name: annotator.name || annotator.email || "Unknown",
      email: annotator.email || null,
    });
  }

  for (const ann of annotations || []) {
    if (!ann?.annotator) continue;
    const id = String(ann.annotator);
    if (!rows.has(id)) {
      rows.set(id, {
        id,
        name: ann.annotator_name || ann.annotator_email || "Unknown",
        email: ann.annotator_email || null,
      });
    }
  }

  return Array.from(rows.values());
}

function buildAnnotationMap(annotations) {
  const map = new Map();
  for (const ann of annotations || []) {
    if (!ann?.annotator || !ann?.label_id) continue;
    map.set(`${ann.annotator}:${ann.label_id}`, ann);
  }
  return map;
}

function noteOwnerName(note, annotatorRows) {
  const raw = note?.annotator || "";
  const byEmail = annotatorRows.find((row) => row.email && row.email === raw);
  return byEmail?.name || raw || "Unknown";
}

function hasDisagreement(label, annotatorRows, annotationMap) {
  const values = annotatorRows
    .map(
      (annotator) =>
        annotationMap.get(`${annotator.id}:${label.label_id}`)?.value,
    )
    .filter((value) => value !== null && value !== undefined)
    .map(stableStringify);
  return new Set(values).size > 1;
}

AnnotationComparisonPanel.propTypes = {
  labels: PropTypes.array,
  annotations: PropTypes.array,
  spanNotes: PropTypes.array,
  annotators: PropTypes.array,
  currentUserId: PropTypes.string,
  viewingAnnotatorId: PropTypes.string,
  onViewingAnnotatorChange: PropTypes.func,
  queueId: PropTypes.string,
  itemId: PropTypes.string,
  reviewStatus: PropTypes.string,
  reviewNotes: PropTypes.string,
  onApprove: PropTypes.func,
  onReject: PropTypes.func,
  isPending: PropTypes.bool,
  showReviewActions: PropTypes.bool,
};

export default function AnnotationComparisonPanel({
  labels = [],
  annotations = [],
  spanNotes = [],
  annotators = [],
  currentUserId = "",
  viewingAnnotatorId = ALL_ANNOTATORS,
  onViewingAnnotatorChange,
  queueId,
  itemId,
  reviewStatus,
  reviewNotes = "",
  onApprove,
  onReject,
  isPending = false,
  showReviewActions = false,
}) {
  const [draftReviewNotes, setDraftReviewNotes] = useState("");

  const annotatorRows = useMemo(
    () => buildAnnotatorRows(annotators, annotations),
    [annotators, annotations],
  );
  const annotationMap = useMemo(
    () => buildAnnotationMap(annotations),
    [annotations],
  );

  return (
    <Box
      sx={{
        p: 3,
        overflow: "auto",
        height: "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {showReviewActions && (
        <Alert severity="info" icon={false} sx={{ mb: 2 }}>
          Review all submissions, then approve or send the item back with
          feedback.
        </Alert>
      )}

      {reviewNotes && (
        <Alert severity="warning" icon={false} sx={{ mb: 2 }}>
          <Typography variant="caption" fontWeight={700} display="block">
            Reviewer feedback
          </Typography>
          <Typography variant="body2">{reviewNotes}</Typography>
        </Alert>
      )}

      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Typography variant="subtitle2" sx={{ flex: 1 }}>
          {showReviewActions ? "Review Annotations" : "Labels"}
        </Typography>
        {reviewStatus && (
          <Chip
            size="small"
            label={reviewStatus.replace("_", " ")}
            color={
              reviewStatus === "approved"
                ? "success"
                : reviewStatus === "rejected"
                  ? "error"
                  : "warning"
            }
          />
        )}
      </Stack>

      <Box sx={{ mb: 2 }}>
        <Typography
          variant="caption"
          fontWeight={600}
          sx={{ display: "block", mb: 0.75 }}
        >
          Viewing annotator
        </Typography>
        <FormControl fullWidth size="small">
          <Select
            value={viewingAnnotatorId || ALL_ANNOTATORS}
            onChange={(event) => onViewingAnnotatorChange?.(event.target.value)}
          >
            <MenuItem value={ALL_ANNOTATORS}>All annotators</MenuItem>
            {annotatorRows.map((annotator) => (
              <MenuItem key={annotator.id} value={annotator.id}>
                {annotatorDisplayName(annotator, currentUserId)}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.75 }}
        >
          Compare submissions side by side. Open a single annotator to edit your
          own work or inspect one person in detail.
        </Typography>
      </Box>

      <Divider sx={{ mb: 2 }} />

      <Stack spacing={1.5} sx={{ flex: 1 }}>
        {labels.map((label) => {
          const disagrees = hasDisagreement(
            label,
            annotatorRows,
            annotationMap,
          );
          return (
            <Box
              key={label.id || label.label_id}
              sx={{
                border: 1,
                borderColor: disagrees ? "warning.main" : "divider",
                borderRadius: 0.75,
                bgcolor: "background.paper",
                overflow: "hidden",
              }}
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{
                  px: 1.5,
                  py: 1,
                  bgcolor: (theme) =>
                    disagrees
                      ? alpha(theme.palette.warning.main, 0.16)
                      : theme.palette.background.neutral,
                }}
              >
                <Typography variant="body2" fontWeight={700} sx={{ flex: 1 }}>
                  {label.name}
                </Typography>
                {disagrees && (
                  <Tooltip title="Annotators gave different values">
                    <Chip
                      size="small"
                      color="warning"
                      variant="outlined"
                      label="Disagreement"
                      sx={{ height: 20, fontSize: 11 }}
                    />
                  </Tooltip>
                )}
              </Stack>

              <Stack divider={<Divider flexItem />} sx={{ px: 1.5 }}>
                {annotatorRows.map((annotator) => {
                  const ann = annotationMap.get(
                    `${annotator.id}:${label.label_id}`,
                  );
                  const displayValue = formatAnnotationValue(
                    ann?.value,
                    ann?.label_type || label.type,
                    ann?.label_settings || label.settings,
                  );
                  const tone = valueTone(ann?.value, label.type);
                  return (
                    <Box key={annotator.id} sx={{ py: 1 }}>
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={1}
                        sx={{ mb: ann?.notes ? 0.5 : 0 }}
                      >
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ flex: 1, minWidth: 0 }}
                          noWrap
                        >
                          {annotatorDisplayName(annotator, currentUserId)}
                        </Typography>
                        <Chip
                          size="small"
                          color={VALUE_COLORS[tone]}
                          variant={tone === "empty" ? "outlined" : "soft"}
                          label={displayValue}
                          sx={{
                            maxWidth: 180,
                            "& .MuiChip-label": {
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                            },
                          }}
                        />
                      </Stack>
                      {ann?.notes && (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: "block", lineHeight: 1.35 }}
                        >
                          Note: {ann.notes}
                        </Typography>
                      )}
                    </Box>
                  );
                })}
              </Stack>
            </Box>
          );
        })}

        <Box>
          <Typography
            variant="caption"
            fontWeight={600}
            color="text.secondary"
            sx={{ display: "block", mb: 1 }}
          >
            ITEM NOTES
          </Typography>
          {spanNotes.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No item notes yet.
            </Typography>
          ) : (
            <Stack spacing={1}>
              {spanNotes.map((note) => (
                <Box
                  key={note.id}
                  sx={{
                    p: 1.25,
                    border: 1,
                    borderColor: "divider",
                    borderRadius: 0.75,
                    bgcolor: "background.neutral",
                  }}
                >
                  <Typography variant="caption" color="text.secondary">
                    {noteOwnerName(note, annotatorRows)}
                  </Typography>
                  <Typography variant="body2" sx={{ mt: 0.25 }}>
                    {note.notes}
                  </Typography>
                </Box>
              ))}
            </Stack>
          )}
        </Box>
      </Stack>

      <AnnotationHistory queueId={queueId} itemId={itemId} />

      {showReviewActions && (
        <>
          <Divider sx={{ my: 2 }} />
          <TextField
            fullWidth
            size="small"
            multiline
            minRows={3}
            maxRows={5}
            label="Reviewer feedback"
            placeholder="Explain what should change before this item is approved..."
            value={draftReviewNotes}
            onChange={(event) => setDraftReviewNotes(event.target.value)}
            sx={{ mb: 2 }}
          />
          <Stack direction="row" spacing={1}>
            <Button
              variant="contained"
              color="success"
              fullWidth
              disabled={isPending}
              onClick={() => onApprove?.(draftReviewNotes)}
              startIcon={
                <Iconify icon="eva:checkmark-circle-2-fill" width={18} />
              }
            >
              Approve
            </Button>
            <Button
              variant="contained"
              color="error"
              fullWidth
              disabled={isPending}
              onClick={() => onReject?.(draftReviewNotes)}
              startIcon={<Iconify icon="eva:close-circle-fill" width={18} />}
            >
              Reject
            </Button>
          </Stack>
        </>
      )}
    </Box>
  );
}
