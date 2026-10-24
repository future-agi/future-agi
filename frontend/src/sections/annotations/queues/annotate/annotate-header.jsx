/* eslint-disable react/prop-types */
import PropTypes from "prop-types";
import {
  Badge,
  Box,
  Button,
  Chip,
  FormControlLabel,
  LinearProgress,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { statusColors } from "src/utils/runStatus";

const STATUS_ROLE = {
  warning: "warning",
  info: "info",
  success: "success",
  error: "danger",
};

const COMMENT_ROLE = { warning: "warning", primary: "discovery" };

function statusTone(theme, color = "info") {
  const c = statusColors(theme, STATUS_ROLE[color] || "info");
  return { border: c.border, bg: c.bg, text: c.fg };
}

function tooltipChipSx(color) {
  return (theme) => {
    const tone =
      color === "default"
        ? {
            border: statusColors(theme, "neutral").border,
            bg: statusColors(theme, "neutral").bg,
            text: statusColors(theme, "neutral").fg,
          }
        : statusTone(theme, color);
    return {
      height: 22,
      fontSize: 12,
      fontWeight: 600,
      borderColor: tone.border,
      bgcolor: tone.bg,
      color: tone.text,
      "& .MuiChip-label": { px: 0.75 },
    };
  };
}

function commentsTooltipPaperSx(theme) {
  const borderColor = theme.palette.divider;
  return {
    bgcolor: "background.paper",
    color: "text.primary",
    border: `1px solid ${borderColor}`,
    borderRadius: 1,
    p: 0,
    maxWidth: 340,
  };
}

function commentsTooltipArrowSx(theme) {
  return {
    color: theme.palette.background.paper,
    "&::before": {
      border: `1px solid ${theme.palette.divider}`,
    },
  };
}

function commentBadgeSx(hasOpenFeedback) {
  return (theme) => {
    const tone = statusColors(theme, hasOpenFeedback ? "warning" : "info");

    return {
      "& .MuiBadge-badge": {
        minWidth: 22,
        height: 22,
        px: 0.5,
        fontWeight: 600,
        border: `1px solid ${theme.palette.background.paper}`,
        bgcolor: tone.solid,
        color: tone.onSolid,
        boxShadow: `0 0 0 2px ${theme.palette.background.paper}`,
      },
    };
  };
}

AnnotateHeader.propTypes = {
  queueName: PropTypes.string,
  progress: PropTypes.shape({
    total: PropTypes.number,
    completed: PropTypes.number,
    user_progress: PropTypes.shape({
      total: PropTypes.number,
      completed: PropTypes.number,
    }),
    userProgress: PropTypes.shape({
      total: PropTypes.number,
      completed: PropTypes.number,
    }),
  }),
  onBack: PropTypes.func.isRequired,
  onSkip: PropTypes.func.isRequired,
  isSkipping: PropTypes.bool,
  isReviewMode: PropTypes.bool,
  isAssignedToOther: PropTypes.bool,
  isSkipDisabled: PropTypes.bool,
  onOpenComments: PropTypes.func,
  commentsDisabled: PropTypes.bool,
  commentBadgeCount: PropTypes.number,
  activeCommentCount: PropTypes.number,
  openFeedbackCount: PropTypes.number,
  addressedFeedbackCount: PropTypes.number,
  resolvedFeedbackCount: PropTypes.number,
  showCompletedToggle: PropTypes.bool,
  includeCompleted: PropTypes.bool,
  onIncludeCompletedChange: PropTypes.func,
  completedToggleDisabled: PropTypes.bool,
  isItemCompleted: PropTypes.bool,
  completedByCurrentUser: PropTypes.bool,
};

export default function AnnotateHeader({
  queueName,
  progress,
  onBack,
  onSkip,
  isSkipping,
  isReviewMode,
  isAssignedToOther,
  isSkipDisabled = false,
  onOpenComments,
  commentsDisabled = false,
  commentBadgeCount = 0,
  activeCommentCount = 0,
  openFeedbackCount = 0,
  addressedFeedbackCount = 0,
  resolvedFeedbackCount = 0,
  showCompletedToggle = false,
  includeCompleted = false,
  onIncludeCompletedChange,
  completedToggleDisabled = false,
  isItemCompleted = false,
  completedByCurrentUser = false,
}) {
  const userProgress = progress?.user_progress;
  const hasUserProgress = userProgress && userProgress.total > 0;
  const badgeCount = Math.max(0, Number(commentBadgeCount || 0));
  const commentTone = openFeedbackCount
    ? "warning"
    : badgeCount
      ? "primary"
      : null;

  // Show user's own progress if they have assigned items, otherwise overall
  const displayTotal = hasUserProgress
    ? userProgress.total
    : progress?.total ?? 0;
  const displayCompleted = hasUserProgress
    ? userProgress.completed
    : progress?.completed ?? 0;
  const pct =
    displayTotal > 0 ? Math.round((displayCompleted / displayTotal) * 100) : 0;
  const progressLabel = hasUserProgress ? "Your Progress" : "Overall Progress";

  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      sx={{
        px: 3,
        py: 1.5,
        borderBottom: 1,
        borderColor: "divider",
        gap: 1.5,
        flexWrap: "wrap",
        "@media (min-width:1000px)": {
          flexWrap: "nowrap",
        },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ minWidth: 0, flex: "1 1 260px" }}
      >
        <Button
          size="small"
          color="inherit"
          onClick={onBack}
          startIcon={<Iconify icon="solar:arrow-left-linear" width={16} />}
          sx={{
            color: "text.primary",
            flexShrink: 0,
          }}
        >
          Back to Queue
        </Button>
        <Typography variant="h6" noWrap sx={{ minWidth: 0, flexShrink: 1 }}>
          {queueName || "Queue"}
        </Typography>
        {isItemCompleted && (
          <Tooltip
            title={
              completedByCurrentUser
                ? "You already completed this item. Saving will update your annotation."
                : "This item is already completed."
            }
          >
            <Chip
              size="small"
              variant="outlined"
              icon={<Iconify icon="solar:check-circle-bold" width={14} />}
              label={completedByCurrentUser ? "Done by you" : "Done"}
              sx={(theme) => {
                const tone = statusTone(theme, "success");
                return {
                  flexShrink: 0,
                  borderColor: tone.border,
                  bgcolor: tone.bg,
                  color: tone.text,
                  "& .MuiChip-icon": {
                    color: tone.text,
                    ml: 0.75,
                    mr: -0.25,
                  },
                };
              }}
            />
          </Tooltip>
        )}
      </Stack>

      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        useFlexGap
        sx={{
          minWidth: 0,
          flex: "1 1 100%",
          flexWrap: "wrap",
          justifyContent: "flex-end",
          "@media (min-width:1000px)": {
            flex: "0 0 auto",
            flexWrap: "nowrap",
          },
        }}
      >
        {showCompletedToggle && (
          <Tooltip
            title={
              includeCompleted
                ? "Previous and Next include completed items."
                : "Previous and Next skip completed items."
            }
          >
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={includeCompleted}
                  onChange={onIncludeCompletedChange}
                  disabled={completedToggleDisabled}
                  inputProps={{ "aria-label": "show completed items" }}
                />
              }
              label="Show completed"
              sx={{
                m: 0,
                color: "text.secondary",
                "& .MuiFormControlLabel-label": {
                  fontSize: 12,
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                },
              }}
            />
          </Tooltip>
        )}
        {onOpenComments && (
          <Tooltip
            arrow
            placement="bottom"
            componentsProps={{
              tooltip: { sx: commentsTooltipPaperSx },
              arrow: { sx: commentsTooltipArrowSx },
            }}
            title={
              <Stack spacing={1} sx={{ p: 1.25, minWidth: 260 }}>
                <Stack spacing={0.25}>
                  <Typography variant="subtitle2" fontWeight={600}>
                    Item comments
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    Open collaboration for this item.
                  </Typography>
                </Stack>
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                    gap: 0.75,
                  }}
                >
                  <Chip
                    size="small"
                    variant="outlined"
                    sx={tooltipChipSx(activeCommentCount ? "info" : "default")}
                    label={`${activeCommentCount} active`}
                  />
                  <Chip
                    size="small"
                    variant="outlined"
                    sx={tooltipChipSx(
                      openFeedbackCount ? "warning" : "default",
                    )}
                    label={`${openFeedbackCount} open`}
                  />
                  <Chip
                    size="small"
                    variant="outlined"
                    sx={tooltipChipSx(
                      addressedFeedbackCount ? "info" : "default",
                    )}
                    label={`${addressedFeedbackCount} addressed`}
                  />
                  <Chip
                    size="small"
                    variant="outlined"
                    sx={tooltipChipSx(
                      resolvedFeedbackCount ? "success" : "default",
                    )}
                    label={`${resolvedFeedbackCount} resolved`}
                  />
                </Box>
              </Stack>
            }
          >
            <span>
              <Badge
                badgeContent={badgeCount || null}
                overlap="rectangular"
                sx={commentBadgeSx(Boolean(openFeedbackCount))}
              >
                <Button
                  size="small"
                  variant="outlined"
                  color="inherit"
                  onClick={onOpenComments}
                  disabled={commentsDisabled}
                  startIcon={
                    <Iconify icon="solar:chat-round-dots-bold" width={16} />
                  }
                  sx={{
                    color: "text.primary",
                    borderColor: (theme) =>
                      commentTone
                        ? statusColors(theme, COMMENT_ROLE[commentTone]).border
                        : theme.palette.border.default,
                    bgcolor: (theme) =>
                      commentTone
                        ? statusColors(theme, COMMENT_ROLE[commentTone]).bg
                        : "transparent",
                    "&:hover": {
                      borderColor: (theme) =>
                        commentTone
                          ? statusColors(theme, COMMENT_ROLE[commentTone]).fg
                          : theme.palette.border.hover,
                      bgcolor: (theme) =>
                        commentTone
                          ? statusColors(theme, COMMENT_ROLE[commentTone]).bg
                          : theme.palette.action.hover,
                    },
                  }}
                >
                  Comments
                </Button>
              </Badge>
            </span>
          </Tooltip>
        )}
        <Box sx={{ minWidth: 150, flex: "0 1 172px" }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            sx={{ mb: 0.5 }}
          >
            <Typography variant="caption" color="text.secondary">
              {progressLabel}
            </Typography>
            <Typography variant="caption" fontWeight={600}>
              {displayCompleted}/{displayTotal} ({pct}%)
            </Typography>
          </Stack>
          <LinearProgress
            variant="determinate"
            value={pct}
            sx={{
              height: 6,
              borderRadius: 3,
              bgcolor: "action.selected",
              "& .MuiLinearProgress-bar": {
                borderRadius: 3,
                bgcolor: (theme) => theme.palette.text.primary,
              },
            }}
          />
        </Box>
        {!isReviewMode && (
          <Tooltip
            title={
              isItemCompleted
                ? "Completed items cannot be skipped"
                : "Press S to skip"
            }
          >
            <span>
              <Button
                variant="outlined"
                color="inherit"
                size="small"
                onClick={onSkip}
                disabled={
                  isSkipping ||
                  isAssignedToOther ||
                  isSkipDisabled ||
                  isItemCompleted
                }
                startIcon={<Iconify icon="solar:skip-next-linear" width={16} />}
                sx={{
                  color: "text.primary",
                  borderColor: "border.default",
                  "&:hover": {
                    borderColor: "border.hover",
                    bgcolor: "action.hover",
                  },
                }}
              >
                Skip
              </Button>
            </span>
          </Tooltip>
        )}
      </Stack>
    </Stack>
  );
}
