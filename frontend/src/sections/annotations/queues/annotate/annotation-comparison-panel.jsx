import PropTypes from "prop-types";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  Collapse,
  Divider,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { fDateTime, fToNowStrict } from "src/utils/format-time";
import AnnotationHistory from "./annotation-history";
import { ALL_ANNOTATORS } from "./annotation-view-mode";

const VALUE_COLORS = {
  positive: "success",
  negative: "error",
  neutral: "default",
  empty: "default",
};

function neutralBorder(theme, opacity = 0.12) {
  return alpha(theme.palette.text.primary, opacity);
}

function quietSurface(theme, opacity = 0.035) {
  return alpha(
    theme.palette.text.primary,
    theme.palette.mode === "dark" ? opacity * 1.6 : opacity,
  );
}

function statusTone(theme, color = "warning") {
  const palette = theme.palette[color] || theme.palette.warning;
  const isDark = theme.palette.mode === "dark";
  return {
    text: isDark ? palette.light : palette.dark,
    bg: alpha(palette.main, isDark ? 0.14 : 0.07),
    bgHover: alpha(palette.main, isDark ? 0.18 : 0.1),
    border: alpha(palette.main, isDark ? 0.34 : 0.24),
  };
}

function statusChipSx(color) {
  return (theme) => {
    const tone = statusTone(theme, color);
    return {
      height: 20,
      fontSize: 11,
      borderRadius: 0.75,
      color: tone.text,
      borderColor: tone.border,
      bgcolor: tone.bg,
      fontWeight: 700,
    };
  };
}

function neutralChipSx(theme) {
  return {
    height: 20,
    fontSize: 11,
    borderRadius: 0.75,
    color: "text.secondary",
    borderColor: neutralBorder(theme, 0.12),
    bgcolor: quietSurface(theme, 0.03),
    fontWeight: 650,
  };
}

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
  const byId = annotatorRows.find(
    (row) => note?.annotator_id && String(row.id) === String(note.annotator_id),
  );
  if (byId) return byId.name;
  const byEmail = annotatorRows.find((row) => row.email && row.email === raw);
  return byEmail?.name || raw || "Unknown";
}

function noteBelongsToAnnotator(note, annotator) {
  if (!annotator) return true;
  if (note?.annotator_id) {
    return String(note.annotator_id) === String(annotator.id);
  }
  const raw = note?.annotator || "";
  return raw === annotator.email || raw === annotator.name;
}

function reviewAuthorName(comment) {
  return comment?.reviewer_name || comment?.reviewer_email || "Reviewer";
}

function formatReviewTime(value) {
  if (!value) return null;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;

  let exact = String(value);
  let relative = exact;
  try {
    exact = fDateTime(date) || exact;
  } catch {
    exact = String(value);
  }
  try {
    relative = fToNowStrict(date) || exact;
  } catch {
    relative = exact;
  }
  return { relative, exact };
}

function reviewThreadStatusLabel(status) {
  return (
    {
      open: "Open",
      reopened: "Reopened",
      addressed: "Addressed",
      resolved: "Resolved",
    }[status] || null
  );
}

function reviewThreadStatusColor(status) {
  return (
    {
      open: "warning",
      reopened: "warning",
      addressed: "info",
      resolved: "success",
    }[status] || "default"
  );
}

function shortId(value) {
  if (!value) return "";
  const text = String(value);
  return text.length > 8 ? text.slice(0, 8) : text;
}

function itemContextLabel(item, itemId) {
  const sourceType =
    item?.source_type || item?.sourceType || item?.source || item?.type;
  const typeLabel = sourceType
    ? String(sourceType).replaceAll("_", " ").toLowerCase()
    : "item";
  return `${typeLabel} ${shortId(item?.id || itemId)}`;
}

function ReviewStatusChip({ comment }) {
  const label = reviewThreadStatusLabel(comment?.thread_status);
  if (!label) return null;
  return (
    <Chip
      size="small"
      label={label}
      variant="outlined"
      sx={(theme) => ({
        ...statusChipSx(reviewThreadStatusColor(comment.thread_status))(theme),
        height: 18,
        fontSize: 10,
        ml: 0.75,
      })}
    />
  );
}

ReviewStatusChip.propTypes = {
  comment: PropTypes.object,
};

function workflowStatusCopy(reviewStatus, showReviewActions) {
  if (showReviewActions) {
    return {
      label: "Ready for review",
      description:
        "Approve the item or send exact feedback back to annotators.",
      color: "warning",
    };
  }
  if (reviewStatus === "approved") {
    return {
      label: "Approved",
      description: "Reviewer approved this item.",
      color: "success",
    };
  }
  if (reviewStatus === "rejected") {
    return {
      label: "Changes requested",
      description: "Annotators need to address reviewer feedback.",
      color: "error",
    };
  }
  if (reviewStatus === "pending_review") {
    return {
      label: "In review",
      description: "This item is waiting for a reviewer decision.",
      color: "warning",
    };
  }
  return {
    label: "Annotation",
    description: "Compare submissions and inspect discussion.",
    color: "default",
  };
}

function isOpenReviewStatus(status) {
  return status === "open" || status === "reopened";
}

function isBlockingReviewFeedback(comment) {
  return comment?.blocking || comment?.action === "request_changes";
}

function isOpenBlockingReviewFeedback(comment) {
  return (
    isBlockingReviewFeedback(comment) &&
    (!comment?.thread_status || isOpenReviewStatus(comment.thread_status))
  );
}

function reviewCommentTargetLabel(comment) {
  const parts = [];
  if (comment?.label_name) parts.push(comment.label_name);
  if (comment?.target_annotator_name || comment?.target_annotator_email) {
    parts.push(comment.target_annotator_name || comment.target_annotator_email);
  }
  return parts.join(" / ") || "Whole item";
}

function normalizeMentionMarkdown(text) {
  return String(text || "").replace(
    /@\[([^[\]]{1,100})\]\(user:[^)]+\)/g,
    "@$1",
  );
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

function isDiscussionComment(comment) {
  return comment?.action === "comment";
}

function groupReviewCommentsByLabel(reviewComments) {
  const map = new Map();
  for (const comment of reviewComments || []) {
    if (isDiscussionComment(comment)) continue;
    const labelId = comment?.label_id;
    if (!labelId || comment?.target_annotator_id) continue;
    const key = String(labelId);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(comment);
  }
  return map;
}

function groupReviewCommentsByScore(reviewComments) {
  const map = new Map();
  for (const comment of reviewComments || []) {
    if (isDiscussionComment(comment)) continue;
    const labelId = comment?.label_id;
    const targetAnnotatorId = comment?.target_annotator_id;
    if (!labelId || !targetAnnotatorId) continue;
    const key = `${labelId}:${targetAnnotatorId}`;
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(comment);
  }
  return map;
}

function buildFeedbackTarget({
  feedbackKey,
  labelById,
  annotatorById,
  annotationMap,
  currentUserId,
}) {
  if (!feedbackKey) return null;
  const [labelId, annotatorId] = feedbackKey.split(":");
  const label = labelById.get(String(labelId));
  const annotator = annotatorById.get(String(annotatorId));
  if (!label || !annotator) return null;
  const ann = annotationMap.get(`${annotatorId}:${labelId}`);
  return {
    key: feedbackKey,
    labelId,
    annotatorId,
    labelName: label.name,
    annotatorName: annotatorDisplayName(annotator, currentUserId),
    rawAnnotatorName: annotator.name || annotator.email || "annotator",
    value: formatAnnotationValue(
      ann?.value,
      ann?.label_type || label.type,
      ann?.label_settings || label.settings,
    ),
  };
}

function labelTypeText(type) {
  return String(type || "label").replaceAll("_", " ");
}

function reviewFeedbackCounts(comments) {
  return {
    open: (comments || []).filter(isOpenBlockingReviewFeedback).length,
    addressed: (comments || []).filter(
      (comment) =>
        isBlockingReviewFeedback(comment) &&
        comment?.thread_status === "addressed",
    ).length,
    resolved: (comments || []).filter(
      (comment) =>
        isBlockingReviewFeedback(comment) &&
        comment?.thread_status === "resolved",
    ).length,
  };
}

function isLabelScopeFocused(focusedScope, labelId) {
  if (!focusedScope || !labelId) return false;
  return (
    focusedScope === `label:${labelId}` ||
    String(focusedScope).startsWith(`${labelId}:`)
  );
}

function isScoreScopeFocused(focusedScope, labelId, annotatorId) {
  return (
    focusedScope &&
    String(focusedScope) === `${String(labelId)}:${String(annotatorId)}`
  );
}

function focusedScopeSx(isFocused) {
  if (!isFocused) return {};
  return {
    position: "relative",
    outline: "2px solid",
    outlineOffset: -2,
    outlineColor: (theme) => theme.palette.primary.main,
    boxShadow: (theme) =>
      `0 0 0 5px ${alpha(theme.palette.primary.main, theme.palette.mode === "dark" ? 0.28 : 0.16)}`,
    bgcolor: (theme) =>
      alpha(
        theme.palette.primary.main,
        theme.palette.mode === "dark" ? 0.1 : 0.06,
      ),
    scrollMarginBlock: 96,
    transition:
      "outline-color 180ms ease, box-shadow 180ms ease, background-color 180ms ease",
  };
}

function ReviewCommentStack({ comments = [], compact = false }) {
  if (!comments.length) return null;
  return (
    <Stack spacing={0.65} sx={{ mt: compact ? 0.75 : 1 }}>
      {comments.map((comment) => {
        const timestamp = formatReviewTime(comment?.created_at);
        return (
          <Box
            key={comment.id || comment.created_at}
            sx={(theme) => {
              const blocking = isOpenBlockingReviewFeedback(comment);
              const tone = statusTone(theme, "warning");
              return {
                p: compact ? 0.75 : 1,
                border: 1,
                borderColor: blocking ? tone.border : neutralBorder(theme, 0.1),
                borderRadius: 0.75,
                bgcolor: blocking ? tone.bg : quietSurface(theme, 0.025),
              };
            }}
          >
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <Typography variant="caption" color="text.secondary" noWrap>
                {reviewAuthorName(comment)}
              </Typography>
              <ReviewStatusChip comment={comment} />
              {timestamp && (
                <Tooltip title={timestamp.exact}>
                  <Typography
                    variant="caption"
                    color="text.disabled"
                    sx={{ ml: "auto", flexShrink: 0 }}
                  >
                    {timestamp.relative}
                  </Typography>
                </Tooltip>
              )}
            </Stack>
            <Typography
              variant={compact ? "caption" : "body2"}
              sx={{ display: "block", mt: 0.35, whiteSpace: "pre-wrap" }}
            >
              {normalizeMentionMarkdown(comment.comment)}
            </Typography>
          </Box>
        );
      })}
    </Stack>
  );
}

ReviewCommentStack.propTypes = {
  comments: PropTypes.array,
  compact: PropTypes.bool,
};

function TargetedFeedbackComposer({
  open,
  feedbackKey,
  label,
  annotator,
  displayValue,
  item,
  itemId,
  currentUserId,
  value,
  onChange,
  onRemove,
  onDone,
}) {
  const annotatorName = annotator.name || annotator.email || "annotator";
  return (
    <Collapse in={Boolean(open)} unmountOnExit>
      <Box
        data-feedback-key={feedbackKey}
        sx={{
          mt: 1,
          p: 1.25,
          border: 1,
          borderColor: (theme) => statusTone(theme, "warning").border,
          borderRadius: 0.75,
          bgcolor: (theme) => statusTone(theme, "warning").bg,
        }}
      >
        <Stack direction="row" flexWrap="wrap" gap={0.75} sx={{ mb: 1 }}>
          <Chip
            size="small"
            variant="outlined"
            label={itemContextLabel(item, itemId)}
            sx={(theme) => neutralChipSx(theme)}
          />
          <Chip
            size="small"
            variant="outlined"
            label={label.name}
            sx={(theme) => statusChipSx("warning")(theme)}
          />
          <Chip
            size="small"
            variant="outlined"
            label={annotatorDisplayName(annotator, currentUserId)}
            sx={(theme) => statusChipSx("info")(theme)}
          />
          <Chip
            size="small"
            variant="outlined"
            label={displayValue}
            sx={(theme) => ({ ...neutralChipSx(theme), maxWidth: "100%" })}
          />
        </Stack>
        <TextField
          fullWidth
          size="small"
          multiline
          minRows={2}
          maxRows={5}
          label={`Feedback for ${label.name} / ${annotatorName}`}
          placeholder={`Tell ${annotatorName} exactly what to change for this answer`}
          value={value}
          onChange={(event) => onChange(feedbackKey, event.target.value)}
        />
        <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
          <Button
            size="small"
            variant="text"
            color="inherit"
            onClick={() => onRemove(feedbackKey)}
          >
            Clear
          </Button>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onDone}
            sx={{
              ml: "auto",
              borderRadius: 0.75,
              borderColor: (theme) => neutralBorder(theme, 0.14),
              color: "text.primary",
              fontWeight: 700,
              "&:hover": {
                borderColor: (theme) => neutralBorder(theme, 0.22),
                bgcolor: (theme) => quietSurface(theme, 0.05),
              },
            }}
          >
            Done
          </Button>
        </Stack>
      </Box>
    </Collapse>
  );
}

TargetedFeedbackComposer.propTypes = {
  open: PropTypes.bool,
  feedbackKey: PropTypes.string.isRequired,
  label: PropTypes.object.isRequired,
  annotator: PropTypes.object.isRequired,
  displayValue: PropTypes.string.isRequired,
  item: PropTypes.object,
  itemId: PropTypes.string,
  currentUserId: PropTypes.string,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  onDone: PropTypes.func.isRequired,
};

function ScoreReviewCell({
  label,
  annotator,
  annotation,
  comments = [],
  feedbackDraft = "",
  isFeedbackOpen = false,
  showAnnotatorName = false,
  showReviewActions = false,
  currentUserId,
  item,
  itemId,
  onOpenFeedback,
  onFeedbackChange,
  onRemoveFeedback,
  onDoneFeedback,
}) {
  const feedbackKey = `${label.label_id}:${annotator.id}`;
  const displayValue = formatAnnotationValue(
    annotation?.value,
    annotation?.label_type || label.type,
    annotation?.label_settings || label.settings,
  );
  const tone = valueTone(annotation?.value, label.type);
  const counts = reviewFeedbackCounts(comments);
  const hasAnyFeedback = comments.length > 0;

  return (
    <Box sx={{ minWidth: 0 }}>
      {showAnnotatorName && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mb: 0.5 }}
          noWrap
        >
          {annotatorDisplayName(annotator, currentUserId)}
        </Typography>
      )}

      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Chip
          size="small"
          variant="outlined"
          label={displayValue}
          sx={(theme) => ({
            ...(tone === "empty"
              ? neutralChipSx(theme)
              : statusChipSx(VALUE_COLORS[tone])(theme)),
            maxWidth: "100%",
            minWidth: 0,
            "& .MuiChip-label": {
              overflow: "hidden",
              textOverflow: "ellipsis",
            },
          })}
        />
        {counts.open > 0 && (
          <Chip
            size="small"
            variant="outlined"
            label={`${counts.open} open`}
            sx={(theme) => statusChipSx("warning")(theme)}
          />
        )}
      </Stack>

      {annotation?.notes && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.65, lineHeight: 1.35 }}
        >
          Note: {annotation.notes}
        </Typography>
      )}

      {hasAnyFeedback && (
        <Box sx={{ mt: 0.75 }}>
          <Stack direction="row" flexWrap="wrap" gap={0.5}>
            {counts.addressed > 0 && (
              <Chip
                size="small"
                variant="outlined"
                label={`${counts.addressed} addressed`}
                sx={(theme) => statusChipSx("info")(theme)}
              />
            )}
            {counts.resolved > 0 && (
              <Chip
                size="small"
                variant="outlined"
                label={`${counts.resolved} resolved`}
                sx={(theme) => statusChipSx("success")(theme)}
              />
            )}
          </Stack>
          <ReviewCommentStack comments={comments} compact />
        </Box>
      )}

      {showReviewActions && (
        <>
          <Button
            size="small"
            color={feedbackDraft.trim() ? "warning" : "inherit"}
            variant={
              isFeedbackOpen || feedbackDraft.trim() ? "outlined" : "text"
            }
            onClick={() => onOpenFeedback(feedbackKey)}
            aria-label={`Open feedback for ${label.name} / ${
              annotator.name || annotator.email || "annotator"
            }`}
            startIcon={<Iconify icon="solar:pen-new-square-bold" width={14} />}
            sx={{ mt: 1, alignSelf: "flex-start" }}
          >
            {feedbackDraft.trim() ? "Edit feedback" : "Feedback"}
          </Button>
          <TargetedFeedbackComposer
            open={isFeedbackOpen}
            feedbackKey={feedbackKey}
            label={label}
            annotator={annotator}
            displayValue={displayValue}
            item={item}
            itemId={itemId}
            currentUserId={currentUserId}
            value={feedbackDraft}
            onChange={onFeedbackChange}
            onRemove={onRemoveFeedback}
            onDone={onDoneFeedback}
          />
        </>
      )}
    </Box>
  );
}

ScoreReviewCell.propTypes = {
  label: PropTypes.object.isRequired,
  annotator: PropTypes.object.isRequired,
  annotation: PropTypes.object,
  comments: PropTypes.array,
  feedbackDraft: PropTypes.string,
  isFeedbackOpen: PropTypes.bool,
  showAnnotatorName: PropTypes.bool,
  showReviewActions: PropTypes.bool,
  currentUserId: PropTypes.string,
  item: PropTypes.object,
  itemId: PropTypes.string,
  onOpenFeedback: PropTypes.func.isRequired,
  onFeedbackChange: PropTypes.func.isRequired,
  onRemoveFeedback: PropTypes.func.isRequired,
  onDoneFeedback: PropTypes.func.isRequired,
};

function ScoreReviewSurface({
  labels,
  visibleAnnotatorRows,
  annotationMap,
  reviewCommentsByLabel,
  reviewCommentsByScore,
  labelFeedback,
  activeFeedbackKey,
  focusedCommentScope,
  showReviewActions,
  currentUserId,
  item,
  itemId,
  onOpenFeedback,
  onFeedbackChange,
  onRemoveFeedback,
  onDoneFeedback,
}) {
  if (!visibleAnnotatorRows.length) {
    return (
      <Alert severity="info" icon={false} sx={{ mb: 1.5 }}>
        No annotator submissions are available for this item yet.
      </Alert>
    );
  }

  const isSingleAnnotator = visibleAnnotatorRows.length <= 1;
  const isWideMatrix = visibleAnnotatorRows.length > 6;
  const matrixColumnWidth = isWideMatrix ? 148 : 118;
  const matrixTemplateColumns = `minmax(150px, 190px) repeat(${visibleAnnotatorRows.length}, minmax(${matrixColumnWidth}px, 1fr))`;
  const matrixMinWidth = 190 + visibleAnnotatorRows.length * matrixColumnWidth;

  if (isSingleAnnotator) {
    const annotator = visibleAnnotatorRows[0];
    return (
      <Box sx={{ mb: 1.5 }}>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
          <Iconify icon="solar:user-check-rounded-bold" width={18} />
          <Typography variant="subtitle2" sx={{ flex: 1 }}>
            Answer review
          </Typography>
          <Chip
            size="small"
            variant="outlined"
            label={annotatorDisplayName(annotator, currentUserId)}
          />
        </Stack>
        <Stack spacing={1.25}>
          {labels.map((label) => {
            const feedbackKey = `${label.label_id}:${annotator.id}`;
            const annotation = annotationMap.get(
              `${annotator.id}:${label.label_id}`,
            );
            const labelReviewComments =
              reviewCommentsByLabel.get(String(label.label_id)) || [];
            const scoreReviewComments =
              reviewCommentsByScore.get(feedbackKey) || [];
            const isFocused = isLabelScopeFocused(
              focusedCommentScope,
              label.label_id,
            );
            return (
              <Box
                key={label.id || label.label_id}
                role="group"
                aria-label={`${label.name} answer review`}
                data-review-label-id={label.label_id}
                data-comment-focus={isFocused ? "true" : undefined}
                sx={(theme) => {
                  const hasOpenFeedback = scoreReviewComments.some(
                    isOpenBlockingReviewFeedback,
                  );
                  return {
                    border: 1,
                    borderColor: hasOpenFeedback
                      ? statusTone(theme, "warning").border
                      : neutralBorder(theme, 0.1),
                    borderRadius: 0.75,
                    bgcolor: "background.paper",
                    overflow: "hidden",
                    ...focusedScopeSx(isFocused),
                  };
                }}
              >
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{
                    px: 1.5,
                    py: 1,
                    bgcolor: "background.neutral",
                  }}
                >
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography variant="body2" fontWeight={700} noWrap>
                      {label.name}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {labelTypeText(label.type)}
                    </Typography>
                  </Box>
                  {label.required && (
                    <Chip
                      size="small"
                      variant="outlined"
                      label="Required"
                      sx={(theme) => statusChipSx("error")(theme)}
                    />
                  )}
                </Stack>
                <Box sx={{ p: 1.5 }}>
                  <ScoreReviewCell
                    label={label}
                    annotator={annotator}
                    annotation={annotation}
                    comments={scoreReviewComments}
                    feedbackDraft={labelFeedback[feedbackKey] || ""}
                    isFeedbackOpen={activeFeedbackKey === feedbackKey}
                    showAnnotatorName
                    showReviewActions={showReviewActions}
                    currentUserId={currentUserId}
                    item={item}
                    itemId={itemId}
                    onOpenFeedback={onOpenFeedback}
                    onFeedbackChange={onFeedbackChange}
                    onRemoveFeedback={onRemoveFeedback}
                    onDoneFeedback={onDoneFeedback}
                  />
                  {labelReviewComments.length > 0 && (
                    <Box sx={{ mt: 1.25 }}>
                      <Typography
                        variant="caption"
                        fontWeight={700}
                        sx={(theme) => ({
                          color: statusTone(theme, "warning").text,
                        })}
                      >
                        Label-level reviewer feedback
                      </Typography>
                      <ReviewCommentStack comments={labelReviewComments} />
                    </Box>
                  )}
                </Box>
              </Box>
            );
          })}
        </Stack>
      </Box>
    );
  }

  return (
    <Box sx={{ mb: 1.5 }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Iconify icon="solar:widget-4-bold" width={18} />
        <Typography variant="subtitle2" sx={{ flex: 1 }}>
          Score matrix
        </Typography>
        <Chip
          size="small"
          variant="outlined"
          label={`${visibleAnnotatorRows.length} annotators`}
          sx={(theme) => neutralChipSx(theme)}
        />
        {isWideMatrix && (
          <Chip
            size="small"
            variant="outlined"
            label="Scroll"
            sx={(theme) => statusChipSx("info")(theme)}
          />
        )}
      </Stack>
      <Box
        role="table"
        aria-label="Annotation score matrix"
        sx={{
          border: 1,
          borderColor: "divider",
          borderRadius: 0.75,
          bgcolor: "background.paper",
          overflowX: "auto",
          overflowY: "hidden",
        }}
      >
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: matrixTemplateColumns,
            minWidth: isWideMatrix ? matrixMinWidth : "100%",
          }}
        >
          <Box
            role="columnheader"
            sx={{
              position: "sticky",
              left: 0,
              zIndex: 2,
              p: 1.25,
              borderRight: 1,
              borderBottom: 1,
              borderColor: "divider",
              bgcolor: "background.neutral",
            }}
          >
            <Typography
              variant="caption"
              fontWeight={700}
              color="text.secondary"
            >
              Label
            </Typography>
          </Box>
          {visibleAnnotatorRows.map((annotator) => (
            <Box
              key={`header-${annotator.id}`}
              role="columnheader"
              sx={{
                p: 1.25,
                borderRight: 1,
                borderBottom: 1,
                borderColor: "divider",
                bgcolor: "background.neutral",
                minWidth: 0,
              }}
            >
              <Typography variant="body2" fontWeight={700} noWrap>
                {annotatorDisplayName(annotator, currentUserId)}
              </Typography>
              {annotator.email && (
                <Typography variant="caption" color="text.secondary" noWrap>
                  {annotator.email}
                </Typography>
              )}
            </Box>
          ))}

          {labels.map((label) => {
            const disagrees = hasDisagreement(
              label,
              visibleAnnotatorRows,
              annotationMap,
            );
            const labelReviewComments =
              reviewCommentsByLabel.get(String(label.label_id)) || [];
            const labelFocused =
              focusedCommentScope === `label:${label.label_id}`;
            return (
              <Fragment key={`row-${label.label_id}`}>
                <Box
                  key={`label-${label.label_id}`}
                  role="rowheader"
                  data-review-label-id={label.label_id}
                  data-comment-focus={labelFocused ? "true" : undefined}
                  sx={(theme) => {
                    const tone = statusTone(theme, "warning");
                    return {
                      position: "sticky",
                      left: 0,
                      zIndex: 1,
                      p: 1,
                      borderRight: 1,
                      borderBottom: 1,
                      borderColor: disagrees ? tone.border : "divider",
                      bgcolor: disagrees
                        ? tone.bg
                        : theme.palette.background.paper,
                      minWidth: 0,
                      ...focusedScopeSx(labelFocused),
                    };
                  }}
                >
                  <Stack spacing={0.75}>
                    <Typography variant="body2" fontWeight={700} noWrap>
                      {label.name}
                    </Typography>
                    <Stack direction="row" flexWrap="wrap" gap={0.5}>
                      <Chip
                        size="small"
                        variant="outlined"
                        label={labelTypeText(label.type)}
                        sx={(theme) => neutralChipSx(theme)}
                      />
                      {label.required && (
                        <Chip
                          size="small"
                          variant="outlined"
                          label="Required"
                          sx={(theme) => statusChipSx("error")(theme)}
                        />
                      )}
                      {disagrees && (
                        <Tooltip title="Annotators gave different values">
                          <Chip
                            size="small"
                            variant="outlined"
                            label="Disagreement"
                            sx={(theme) => statusChipSx("warning")(theme)}
                          />
                        </Tooltip>
                      )}
                    </Stack>
                    {labelReviewComments.length > 0 && (
                      <ReviewCommentStack
                        comments={labelReviewComments}
                        compact
                      />
                    )}
                  </Stack>
                </Box>
                {visibleAnnotatorRows.map((annotator) => {
                  const feedbackKey = `${label.label_id}:${annotator.id}`;
                  const annotation = annotationMap.get(
                    `${annotator.id}:${label.label_id}`,
                  );
                  const displayValue = formatAnnotationValue(
                    annotation?.value,
                    annotation?.label_type || label.type,
                    annotation?.label_settings || label.settings,
                  );
                  const scoreReviewComments =
                    reviewCommentsByScore.get(feedbackKey) || [];
                  const scoreFocused = isScoreScopeFocused(
                    focusedCommentScope,
                    label.label_id,
                    annotator.id,
                  );
                  return (
                    <Box
                      key={`cell-${feedbackKey}`}
                      role="cell"
                      data-review-score-key={feedbackKey}
                      data-comment-focus={scoreFocused ? "true" : undefined}
                      aria-label={`${label.name} / ${annotatorDisplayName(
                        annotator,
                        currentUserId,
                      )}: ${displayValue}`}
                      sx={(theme) => {
                        const hasOpenFeedback = scoreReviewComments.some(
                          isOpenBlockingReviewFeedback,
                        );
                        const tone = statusTone(theme, "warning");
                        return {
                          p: 1,
                          borderRight: 1,
                          borderBottom: 1,
                          borderColor: hasOpenFeedback
                            ? tone.border
                            : "divider",
                          bgcolor: hasOpenFeedback
                            ? tone.bg
                            : theme.palette.background.paper,
                          minWidth: 0,
                          ...focusedScopeSx(scoreFocused),
                        };
                      }}
                    >
                      <ScoreReviewCell
                        label={label}
                        annotator={annotator}
                        annotation={annotation}
                        comments={scoreReviewComments}
                        feedbackDraft={labelFeedback[feedbackKey] || ""}
                        isFeedbackOpen={activeFeedbackKey === feedbackKey}
                        showReviewActions={showReviewActions}
                        currentUserId={currentUserId}
                        item={item}
                        itemId={itemId}
                        onOpenFeedback={onOpenFeedback}
                        onFeedbackChange={onFeedbackChange}
                        onRemoveFeedback={onRemoveFeedback}
                        onDoneFeedback={onDoneFeedback}
                      />
                    </Box>
                  );
                })}
              </Fragment>
            );
          })}
        </Box>
      </Box>
    </Box>
  );
}

ScoreReviewSurface.propTypes = {
  labels: PropTypes.array.isRequired,
  visibleAnnotatorRows: PropTypes.array.isRequired,
  annotationMap: PropTypes.object.isRequired,
  reviewCommentsByLabel: PropTypes.object.isRequired,
  reviewCommentsByScore: PropTypes.object.isRequired,
  labelFeedback: PropTypes.object.isRequired,
  activeFeedbackKey: PropTypes.string,
  focusedCommentScope: PropTypes.string,
  showReviewActions: PropTypes.bool,
  currentUserId: PropTypes.string,
  item: PropTypes.object,
  itemId: PropTypes.string,
  onOpenFeedback: PropTypes.func.isRequired,
  onFeedbackChange: PropTypes.func.isRequired,
  onRemoveFeedback: PropTypes.func.isRequired,
  onDoneFeedback: PropTypes.func.isRequired,
};

function FeedbackDraftSummary({ feedbackDrafts, onRemove }) {
  if (!feedbackDrafts.length) return null;
  return (
    <Box
      sx={(theme) => {
        const tone = statusTone(theme, "warning");
        return {
          p: 1.25,
          border: 1,
          borderColor: tone.border,
          borderRadius: 0.75,
          bgcolor: tone.bg,
        };
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <Iconify icon="solar:target-bold" width={18} />
        <Typography variant="subtitle2" sx={{ flex: 1 }}>
          Feedback to send
        </Typography>
        <Chip
          size="small"
          variant="outlined"
          label={`${feedbackDrafts.length} targeted`}
          sx={(theme) => statusChipSx("warning")(theme)}
        />
      </Stack>
      <Stack spacing={0.75} sx={{ mt: 1 }}>
        {feedbackDrafts.map((target) => (
          <Box
            key={target.key}
            sx={{
              p: 1,
              border: 1,
              borderColor: "divider",
              borderRadius: 0.75,
              bgcolor: "background.paper",
            }}
          >
            <Stack direction="row" spacing={0.75} alignItems="center">
              <Typography
                variant="caption"
                fontWeight={700}
                sx={{ flex: 1, minWidth: 0 }}
                noWrap
              >
                {target.labelName} / {target.annotatorName}
              </Typography>
              <Button
                size="small"
                variant="text"
                color="inherit"
                onClick={() => onRemove(target.key)}
                sx={{
                  color: (theme) => statusTone(theme, "error").text,
                  fontWeight: 700,
                  "&:hover": {
                    bgcolor: (theme) => statusTone(theme, "error").bg,
                  },
                }}
              >
                Remove
              </Button>
            </Stack>
            <Typography variant="body2" sx={{ mt: 0.25 }}>
              {target.valueText}
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.25 }}
            >
              Current answer: {target.value}
            </Typography>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}

FeedbackDraftSummary.propTypes = {
  feedbackDrafts: PropTypes.array.isRequired,
  onRemove: PropTypes.func.isRequired,
};

function ItemNotesList({ notes, annotatorRows }) {
  return (
    <Box>
      <Typography
        variant="caption"
        fontWeight={600}
        color="text.secondary"
        sx={{ display: "block", mb: 1 }}
      >
        ITEM NOTES
      </Typography>
      {notes.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No item notes yet.
        </Typography>
      ) : (
        <Stack spacing={1}>
          {notes.map((note) => (
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
  );
}

ItemNotesList.propTypes = {
  notes: PropTypes.array.isRequired,
  annotatorRows: PropTypes.array.isRequired,
};

function ReviewActivityPanel({ comments }) {
  if (!comments.length) return null;
  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 0.75,
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 1.5, py: 1.25, bgcolor: "background.neutral" }}
      >
        <Iconify icon="solar:history-bold" width={18} />
        <Typography variant="subtitle2" sx={{ flex: 1 }}>
          Review activity
        </Typography>
        <Chip
          size="small"
          label={comments.length}
          variant="outlined"
          sx={(theme) => ({
            ...neutralChipSx(theme),
            height: 22,
            minWidth: 30,
          })}
        />
      </Stack>
      <Divider />
      <Box sx={{ p: 1.5 }}>
        <ReviewCommentStack comments={comments} />
      </Box>
    </Box>
  );
}

ReviewActivityPanel.propTypes = {
  comments: PropTypes.array.isRequired,
};

AnnotationComparisonPanel.propTypes = {
  item: PropTypes.object,
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
  reviewComments: PropTypes.array,
  onApprove: PropTypes.func,
  onReject: PropTypes.func,
  onDirtyChange: PropTypes.func,
  isPending: PropTypes.bool,
  showReviewActions: PropTypes.bool,
  focusedCommentScope: PropTypes.string,
};

export default function AnnotationComparisonPanel({
  item = null,
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
  reviewComments = [],
  onApprove,
  onReject,
  onDirtyChange,
  isPending = false,
  showReviewActions = false,
  focusedCommentScope = null,
}) {
  const [draftReviewNotes, setDraftReviewNotes] = useState("");
  const [labelFeedback, setLabelFeedback] = useState({});
  const [activeFeedbackKey, setActiveFeedbackKey] = useState(null);
  const onDirtyChangeRef = useRef(onDirtyChange);

  useEffect(() => {
    onDirtyChangeRef.current = onDirtyChange;
  }, [onDirtyChange]);

  useEffect(() => {
    setDraftReviewNotes("");
    setLabelFeedback({});
    setActiveFeedbackKey(null);
  }, [itemId, viewingAnnotatorId]);

  const annotatorRows = useMemo(
    () => buildAnnotatorRows(annotators, annotations),
    [annotators, annotations],
  );
  const selectedAnnotator = useMemo(
    () =>
      viewingAnnotatorId && viewingAnnotatorId !== ALL_ANNOTATORS
        ? annotatorRows.find(
            (annotator) => String(annotator.id) === String(viewingAnnotatorId),
          )
        : null,
    [annotatorRows, viewingAnnotatorId],
  );
  const visibleAnnotatorRows = useMemo(
    () => (selectedAnnotator ? [selectedAnnotator] : annotatorRows),
    [annotatorRows, selectedAnnotator],
  );
  const annotationMap = useMemo(
    () => buildAnnotationMap(annotations),
    [annotations],
  );
  const labelById = useMemo(
    () =>
      new Map((labels || []).map((label) => [String(label.label_id), label])),
    [labels],
  );
  const annotatorById = useMemo(
    () =>
      new Map(
        (annotatorRows || []).map((annotator) => [
          String(annotator.id),
          annotator,
        ]),
      ),
    [annotatorRows],
  );
  const visibleSpanNotes = useMemo(
    () =>
      selectedAnnotator
        ? spanNotes.filter((note) =>
            noteBelongsToAnnotator(note, selectedAnnotator),
          )
        : spanNotes,
    [selectedAnnotator, spanNotes],
  );
  const reviewCommentsByLabel = useMemo(
    () => groupReviewCommentsByLabel(reviewComments),
    [reviewComments],
  );
  const reviewCommentsByScore = useMemo(
    () => groupReviewCommentsByScore(reviewComments),
    [reviewComments],
  );
  const overallReviewComments = useMemo(
    () =>
      (reviewComments || []).filter(
        (comment) =>
          !isDiscussionComment(comment) &&
          !comment?.label_id &&
          !isOpenBlockingReviewFeedback(comment),
      ),
    [reviewComments],
  );
  const decisionReviewComments = useMemo(
    () =>
      (reviewComments || []).filter((comment) => !isDiscussionComment(comment)),
    [reviewComments],
  );
  const reviewActivityComments = useMemo(
    () =>
      decisionReviewComments.filter(
        (comment) => !isOpenBlockingReviewFeedback(comment),
      ),
    [decisionReviewComments],
  );
  const openBlockingFeedback = useMemo(
    () =>
      decisionReviewComments.filter((comment) =>
        isOpenBlockingReviewFeedback(comment),
      ),
    [decisionReviewComments],
  );
  const workflowCopy = useMemo(
    () => workflowStatusCopy(reviewStatus, showReviewActions),
    [reviewStatus, showReviewActions],
  );
  const hasRequestFeedback = useMemo(
    () =>
      Boolean(
        draftReviewNotes.trim() ||
          Object.values(labelFeedback).some((value) => String(value).trim()),
      ),
    [draftReviewNotes, labelFeedback],
  );

  useEffect(() => {
    onDirtyChange?.(hasRequestFeedback);
  }, [hasRequestFeedback, onDirtyChange]);

  useEffect(
    () => () => {
      onDirtyChangeRef.current?.(false);
    },
    [],
  );
  const feedbackDrafts = useMemo(
    () =>
      Object.entries(labelFeedback)
        .filter(([, value]) => String(value || "").trim())
        .map(([feedbackKey, value]) => ({
          ...buildFeedbackTarget({
            feedbackKey,
            labelById,
            annotatorById,
            annotationMap,
            currentUserId,
          }),
          valueText: String(value || "").trim(),
        }))
        .filter((target) => target?.key),
    [labelFeedback, labelById, annotatorById, annotationMap, currentUserId],
  );
  const feedbackDraftCount =
    feedbackDrafts.length + (draftReviewNotes.trim() ? 1 : 0);
  const isApproveDisabled =
    isPending || hasRequestFeedback || openBlockingFeedback.length > 0;
  const reviewActionHint = openBlockingFeedback.length
    ? "Resolve open requested changes before approving."
    : hasRequestFeedback
      ? "Request changes or clear feedback drafts before approving."
      : "Approve as-is or add feedback before requesting changes.";

  const buildReviewPayload = () => ({
    notes: draftReviewNotes,
    labelComments: Object.entries(labelFeedback)
      .map(([key, comment]) => {
        const [labelId, targetAnnotatorId] = key.split(":");
        return {
          label_id: labelId,
          target_annotator_id: targetAnnotatorId,
          comment: String(comment || "").trim(),
        };
      })
      .filter((entry) => entry.comment),
  });

  const handleLabelFeedbackChange = (feedbackKey, value) => {
    setLabelFeedback((prev) => ({
      ...prev,
      [feedbackKey]: value,
    }));
  };

  const handleRemoveLabelFeedback = (feedbackKey) => {
    setLabelFeedback((prev) => {
      const next = { ...prev };
      delete next[feedbackKey];
      return next;
    });
    if (activeFeedbackKey === feedbackKey) setActiveFeedbackKey(null);
  };

  return (
    <Box
      sx={{
        p: 3,
        overflow: "auto",
        height: "100%",
      }}
    >
      <Box
        data-review-item-summary="true"
        data-comment-focus={focusedCommentScope === "item" ? "true" : undefined}
        sx={{
          mb: 2,
          p: 1.25,
          border: 1,
          borderColor: "divider",
          borderRadius: 0.75,
          bgcolor: "background.neutral",
          ...focusedScopeSx(focusedCommentScope === "item"),
        }}
      >
        <Stack direction="row" alignItems="flex-start" spacing={1}>
          <Iconify icon="solar:clipboard-list-bold" width={20} />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="caption" color="text.secondary">
              {itemContextLabel(item, itemId)}
            </Typography>
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ mt: 0.25 }}
            >
              <Typography variant="subtitle2" noWrap>
                Review workflow
              </Typography>
              <Chip
                size="small"
                variant="outlined"
                label={workflowCopy.label}
                sx={(theme) => statusChipSx(workflowCopy.color)(theme)}
              />
            </Stack>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5 }}
            >
              {workflowCopy.description}
            </Typography>
          </Box>
        </Stack>
      </Box>

      {openBlockingFeedback.length > 0 && (
        <Box
          sx={{
            mb: 2,
            p: 1.25,
            border: 1,
            borderColor: (theme) => statusTone(theme, "warning").border,
            borderRadius: 0.75,
            bgcolor: (theme) => statusTone(theme, "warning").bg,
          }}
        >
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Iconify icon="solar:flag-bold" width={18} />
            <Typography variant="subtitle2" sx={{ flex: 1 }}>
              Open requested changes
            </Typography>
            <Chip
              size="small"
              variant="outlined"
              label={openBlockingFeedback.length}
              sx={(theme) => statusChipSx("warning")(theme)}
            />
          </Stack>
          <Stack spacing={0.75} sx={{ mt: 1 }}>
            {openBlockingFeedback.map((comment) => (
              <Box
                key={comment.id || comment.created_at}
                sx={{
                  p: 1,
                  border: 1,
                  borderColor: "divider",
                  borderRadius: 0.75,
                  bgcolor: "background.paper",
                }}
              >
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <Chip
                    size="small"
                    variant="outlined"
                    label={reviewCommentTargetLabel(comment)}
                    sx={(theme) => ({
                      ...statusChipSx("warning")(theme),
                      maxWidth: "100%",
                    })}
                  />
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ ml: "auto" }}
                  >
                    {reviewAuthorName(comment)}
                  </Typography>
                </Stack>
                <Typography variant="body2" sx={{ mt: 0.5 }}>
                  {normalizeMentionMarkdown(comment.comment)}
                </Typography>
              </Box>
            ))}
          </Stack>
        </Box>
      )}

      {overallReviewComments.length > 0 ? (
        <Alert severity="warning" icon={false} sx={{ mb: 2 }}>
          <Typography variant="caption" fontWeight={700} display="block">
            Reviewer feedback
          </Typography>
          <Stack spacing={0.75} sx={{ mt: 0.75 }}>
            {overallReviewComments.map((comment) => (
              <Box key={comment.id || comment.created_at}>
                <Stack direction="row" alignItems="center" spacing={0.5}>
                  <Typography variant="caption" color="text.secondary">
                    {reviewAuthorName(comment)}
                  </Typography>
                  <ReviewStatusChip comment={comment} />
                </Stack>
                <Typography variant="body2">
                  {normalizeMentionMarkdown(comment.comment)}
                </Typography>
              </Box>
            ))}
          </Stack>
        </Alert>
      ) : reviewNotes ? (
        <Alert severity="warning" icon={false} sx={{ mb: 2 }}>
          <Typography variant="caption" fontWeight={700} display="block">
            Reviewer feedback
          </Typography>
          <Typography variant="body2">{reviewNotes}</Typography>
        </Alert>
      ) : null}

      {showReviewActions && (
        <Box
          sx={(theme) => {
            const hasNotes = draftReviewNotes.trim();
            const tone = statusTone(theme, "warning");
            return {
              mb: 2,
              p: 1.25,
              border: 1,
              borderColor: hasNotes ? tone.border : neutralBorder(theme, 0.1),
              borderRadius: 0.75,
              bgcolor: hasNotes ? tone.bg : theme.palette.background.paper,
            };
          }}
        >
          <Typography
            variant="caption"
            fontWeight={700}
            color="text.secondary"
            sx={{ display: "block", mb: 0.75 }}
          >
            Whole-item feedback
          </Typography>
          <TextField
            fullWidth
            size="small"
            multiline
            minRows={2}
            maxRows={5}
            label="Whole item feedback"
            placeholder="Use this only when the whole item needs context. For one bad score, click Feedback on that annotator row."
            value={draftReviewNotes}
            onChange={(event) => setDraftReviewNotes(event.target.value)}
            helperText={
              hasRequestFeedback
                ? " "
                : "Request changes needs either whole-item feedback or targeted row feedback."
            }
          />
        </Box>
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
        <Stack
          role="group"
          aria-label="Viewing annotator"
          direction="row"
          flexWrap="wrap"
          gap={0.75}
        >
          <Button
            size="small"
            variant={
              (viewingAnnotatorId || ALL_ANNOTATORS) === ALL_ANNOTATORS
                ? "contained"
                : "outlined"
            }
            color="inherit"
            aria-pressed={
              (viewingAnnotatorId || ALL_ANNOTATORS) === ALL_ANNOTATORS
            }
            onClick={() => onViewingAnnotatorChange?.(ALL_ANNOTATORS)}
            sx={{ borderRadius: 0.75, minHeight: 30 }}
          >
            All annotators
          </Button>
          {annotatorRows.map((annotator) => {
            const isSelected =
              String(viewingAnnotatorId || "") === String(annotator.id);
            return (
              <Button
                key={annotator.id}
                size="small"
                variant={isSelected ? "contained" : "outlined"}
                color="inherit"
                aria-pressed={isSelected}
                onClick={() => onViewingAnnotatorChange?.(annotator.id)}
                sx={{
                  borderRadius: 0.75,
                  minHeight: 30,
                  maxWidth: "100%",
                  justifyContent: "flex-start",
                }}
              >
                {annotatorDisplayName(annotator, currentUserId)}
              </Button>
            );
          })}
        </Stack>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.75 }}
        >
          {selectedAnnotator
            ? `Showing only ${annotatorDisplayName(
                selectedAnnotator,
                currentUserId,
              )}. Choose All annotators to compare submissions side by side.`
            : "Compare submissions side by side. Open a single annotator to inspect one person in detail."}
        </Typography>
      </Box>

      <Divider sx={{ mb: 2 }} />

      <Stack spacing={1.5}>
        <ScoreReviewSurface
          labels={labels}
          visibleAnnotatorRows={visibleAnnotatorRows}
          annotationMap={annotationMap}
          reviewCommentsByLabel={reviewCommentsByLabel}
          reviewCommentsByScore={reviewCommentsByScore}
          labelFeedback={labelFeedback}
          activeFeedbackKey={activeFeedbackKey}
          focusedCommentScope={focusedCommentScope}
          showReviewActions={showReviewActions}
          currentUserId={currentUserId}
          item={item}
          itemId={itemId}
          onOpenFeedback={setActiveFeedbackKey}
          onFeedbackChange={handleLabelFeedbackChange}
          onRemoveFeedback={handleRemoveLabelFeedback}
          onDoneFeedback={() => setActiveFeedbackKey(null)}
        />

        {showReviewActions && (
          <FeedbackDraftSummary
            feedbackDrafts={feedbackDrafts}
            onRemove={handleRemoveLabelFeedback}
          />
        )}

        <ItemNotesList notes={visibleSpanNotes} annotatorRows={annotatorRows} />

        <ReviewActivityPanel comments={reviewActivityComments} />
      </Stack>

      <AnnotationHistory queueId={queueId} itemId={itemId} />

      {showReviewActions && (
        <Box
          sx={{
            position: "sticky",
            bottom: 0,
            mt: 2,
            px: 1.5,
            py: 1.5,
            borderTop: 1,
            borderColor: "divider",
            bgcolor: (theme) =>
              theme.palette.mode === "dark"
                ? alpha(theme.palette.background.paper, 0.96)
                : theme.palette.background.paper,
            boxShadow: (theme) =>
              theme.palette.mode === "dark"
                ? `0 -12px 30px ${alpha(theme.palette.common.black, 0.34)}`
                : `0 -10px 26px ${alpha(theme.palette.grey[600], 0.12)}`,
            zIndex: 2,
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
            <Iconify icon="solar:checklist-minimalistic-bold" width={18} />
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ flex: 1 }}
            >
              {hasRequestFeedback
                ? `${feedbackDraftCount} feedback ${
                    feedbackDraftCount === 1 ? "draft" : "drafts"
                  } ready. ${reviewActionHint}`
                : reviewActionHint}
            </Typography>
          </Stack>
          <Stack direction="row" spacing={1}>
            <Button
              variant="contained"
              color="inherit"
              fullWidth
              disabled={isApproveDisabled}
              onClick={() => onApprove?.({ notes: "", labelComments: [] })}
              startIcon={
                <Iconify icon="eva:checkmark-circle-2-fill" width={18} />
              }
              sx={{
                borderRadius: 0.75,
                bgcolor: (theme) =>
                  theme.palette.mode === "dark"
                    ? theme.palette.common.white
                    : theme.palette.grey[900],
                color: (theme) =>
                  theme.palette.mode === "dark"
                    ? theme.palette.grey[900]
                    : theme.palette.common.white,
                boxShadow: "none",
                fontWeight: 700,
                "&:hover": {
                  bgcolor: (theme) =>
                    theme.palette.mode === "dark"
                      ? alpha(theme.palette.common.white, 0.9)
                      : theme.palette.grey[800],
                  boxShadow: (theme) =>
                    `0 10px 20px ${alpha(theme.palette.text.primary, 0.16)}`,
                },
              }}
            >
              Approve
            </Button>
            <Button
              variant="outlined"
              fullWidth
              disabled={isPending || !hasRequestFeedback}
              onClick={() => onReject?.(buildReviewPayload())}
              startIcon={<Iconify icon="eva:close-circle-fill" width={18} />}
              sx={{
                borderRadius: 0.75,
                borderColor: (theme) => alpha(theme.palette.error.main, 0.3),
                bgcolor: (theme) =>
                  alpha(
                    theme.palette.error.main,
                    theme.palette.mode === "dark" ? 0.12 : 0.06,
                  ),
                color: (theme) =>
                  theme.palette.mode === "dark"
                    ? theme.palette.error.light
                    : theme.palette.error.dark,
                fontWeight: 700,
                "&:hover": {
                  borderColor: (theme) => alpha(theme.palette.error.main, 0.4),
                  bgcolor: (theme) =>
                    alpha(
                      theme.palette.error.main,
                      theme.palette.mode === "dark" ? 0.16 : 0.09,
                    ),
                },
              }}
            >
              Request changes
            </Button>
          </Stack>
        </Box>
      )}
    </Box>
  );
}
