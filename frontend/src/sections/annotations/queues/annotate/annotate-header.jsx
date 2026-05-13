/* eslint-disable react/prop-types */
import PropTypes from "prop-types";
import {
  Badge,
  Box,
  Button,
  Chip,
  IconButton,
  LinearProgress,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";

function statusTone(theme, color = "info") {
  const paletteColor = theme.palette[color] || theme.palette.info;
  return {
    border: alpha(
      paletteColor.main,
      theme.palette.mode === "dark" ? 0.34 : 0.24,
    ),
    bg: alpha(paletteColor.main, theme.palette.mode === "dark" ? 0.13 : 0.055),
    text:
      theme.palette.mode === "dark"
        ? paletteColor.light || paletteColor.main
        : paletteColor.dark || paletteColor.main,
  };
}

function tooltipChipSx(color) {
  return (theme) => {
    const tone =
      color === "default"
        ? {
            border: alpha(theme.palette.text.primary, 0.14),
            bg: alpha(theme.palette.text.primary, 0.04),
            text: theme.palette.text.secondary,
          }
        : statusTone(theme, color);
    return {
      height: 22,
      fontSize: 11,
      fontWeight: 700,
      borderColor: tone.border,
      bgcolor: tone.bg,
      color: tone.text,
      "& .MuiChip-label": { px: 0.75 },
    };
  };
}

function commentsTooltipPaperSx(theme) {
  const borderColor = alpha(
    theme.palette.text.primary,
    theme.palette.mode === "dark" ? 0.16 : 0.12,
  );
  return {
    bgcolor: "background.paper",
    color: "text.primary",
    border: `1px solid ${borderColor}`,
    boxShadow:
      theme.palette.mode === "dark"
        ? `0 18px 42px ${alpha(theme.palette.common.black, 0.48)}`
        : `0 18px 42px ${alpha(theme.palette.grey[700], 0.16)}`,
    borderRadius: 1,
    p: 0,
    maxWidth: 340,
  };
}

function commentsTooltipArrowSx(theme) {
  return {
    color: theme.palette.background.paper,
    "&::before": {
      border: `1px solid ${alpha(
        theme.palette.text.primary,
        theme.palette.mode === "dark" ? 0.16 : 0.12,
      )}`,
    },
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
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <IconButton onClick={onBack} size="small">
          <Iconify icon="eva:arrow-back-fill" />
        </IconButton>
        <Typography variant="h6">{queueName || "Queue"}</Typography>
      </Stack>

      <Stack direction="row" alignItems="center" spacing={1.5}>
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
                  <Typography variant="subtitle2" fontWeight={700}>
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
                color={openFeedbackCount ? "warning" : "primary"}
                overlap="rectangular"
                sx={{
                  "& .MuiBadge-badge": {
                    fontWeight: 800,
                    bgcolor: (theme) =>
                      theme.palette[openFeedbackCount ? "warning" : "primary"]
                        .main,
                    color: (theme) => theme.palette.common.white,
                    boxShadow: (theme) =>
                      `0 0 0 2px ${theme.palette.background.paper}`,
                  },
                }}
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
                    borderRadius: 0.75,
                    minHeight: 32,
                    px: 1.25,
                    fontWeight: 700,
                    color: "text.primary",
                    borderColor: (theme) =>
                      commentTone
                        ? alpha(theme.palette[commentTone].main, 0.32)
                        : alpha(theme.palette.text.primary, 0.14),
                    bgcolor: (theme) =>
                      commentTone
                        ? alpha(
                            theme.palette[commentTone].main,
                            theme.palette.mode === "dark" ? 0.14 : 0.08,
                          )
                        : "transparent",
                    "&:hover": {
                      borderColor: (theme) =>
                        commentTone
                          ? alpha(theme.palette[commentTone].main, 0.42)
                          : alpha(theme.palette.text.primary, 0.22),
                      bgcolor: (theme) =>
                        commentTone
                          ? alpha(
                              theme.palette[commentTone].main,
                              theme.palette.mode === "dark" ? 0.18 : 0.11,
                            )
                          : alpha(
                              theme.palette.text.primary,
                              theme.palette.mode === "dark" ? 0.08 : 0.04,
                            ),
                    },
                  }}
                >
                  Comments
                </Button>
              </Badge>
            </span>
          </Tooltip>
        )}
        <Box sx={{ minWidth: 180 }}>
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
              bgcolor: (theme) => alpha(theme.palette.text.primary, 0.08),
              "& .MuiLinearProgress-bar": {
                borderRadius: 3,
                bgcolor: (theme) => theme.palette.text.primary,
              },
            }}
          />
        </Box>
        {!isReviewMode && (
          <Tooltip title="Press S to skip">
            <span>
              <Button
                variant="outlined"
                color="inherit"
                size="small"
                onClick={onSkip}
                disabled={isSkipping || isAssignedToOther || isSkipDisabled}
                startIcon={<Iconify icon="eva:skip-forward-fill" width={16} />}
                sx={{
                  borderRadius: 0.75,
                  color: "text.primary",
                  borderColor: (theme) =>
                    alpha(theme.palette.text.primary, 0.14),
                  fontWeight: 700,
                  "&:hover": {
                    borderColor: (theme) =>
                      alpha(theme.palette.text.primary, 0.22),
                    bgcolor: (theme) =>
                      alpha(
                        theme.palette.text.primary,
                        theme.palette.mode === "dark" ? 0.08 : 0.04,
                      ),
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
