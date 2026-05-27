import React from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { readableToken } from "../onboarding-home.constants";

const actionHref = (dailyAction) => {
  if (!dailyAction) return null;
  if (dailyAction.routeAvailable && dailyAction.route) return dailyAction.route;
  return dailyAction.fallbackRoute || null;
};

const windowLabel = (dailyQuality) => {
  const end = dailyQuality?.window?.endAt;
  if (!end) return "Current review";
  return `Review through ${new Date(end).toLocaleDateString()}`;
};

function ProductCard({ card }) {
  return (
    <Box
      data-testid={`daily-quality-product-card-${card.path}`}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 1.5,
        minHeight: 112,
      }}
    >
      <Stack spacing={0.75}>
        <Stack direction="row" justifyContent="space-between" gap={1}>
          <Typography variant="subtitle2">{card.label}</Typography>
          <Chip
            size="small"
            variant="outlined"
            label={readableToken(card.status)}
          />
        </Stack>
        <Typography variant="h5">{card.metric}</Typography>
        <Typography variant="body2" color="text.secondary">
          {card.summary}
        </Typography>
        {card.change ? (
          <Typography variant="caption" color="text.secondary">
            {card.change}
          </Typography>
        ) : null}
      </Stack>
    </Box>
  );
}

ProductCard.propTypes = {
  card: PropTypes.object.isRequired,
};

export default function DailyQualityHome({
  dailyQuality,
  recommendedAction,
  canAct = true,
  onActionClick,
  onSignalReview,
  onWeeklyReviewOpen,
}) {
  const topSignal = dailyQuality?.topSignal;
  const primaryAction = dailyQuality?.primaryAction;
  const weeklyReview = dailyQuality?.weeklyReview;
  const href = actionHref(primaryAction);
  const isWriteBlocked = !canAct && primaryAction?.requiresPermission;

  const handlePrimaryClick = () => {
    onActionClick?.(recommendedAction, primaryAction);
    if (topSignal) {
      onSignalReview?.(topSignal, primaryAction);
    }
  };

  return (
    <Stack data-testid="onboarding-daily-quality" spacing={2}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
        <Chip size="small" label={windowLabel(dailyQuality)} />
        <Chip
          size="small"
          color={topSignal ? "warning" : "default"}
          variant={topSignal ? "filled" : "outlined"}
          label={readableToken(dailyQuality?.mode)}
        />
      </Stack>

      {topSignal ? (
        <Box
          data-testid="daily-quality-top-signal"
          sx={{
            border: "1px solid",
            borderColor: "warning.main",
            borderRadius: 1,
            p: 2,
            bgcolor: "background.paper",
          }}
        >
          <Stack spacing={1.25}>
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              flexWrap="wrap"
            >
              <Typography variant="subtitle2">Top signal</Typography>
              <Chip size="small" label={readableToken(topSignal.type)} />
              <Chip
                size="small"
                color={topSignal.severity === "critical" ? "error" : "warning"}
                label={readableToken(topSignal.severity)}
              />
            </Stack>
            <Stack spacing={0.5}>
              <Typography variant="h6">{topSignal.title}</Typography>
              <Typography variant="body2" color="text.secondary">
                {topSignal.body}
              </Typography>
            </Stack>
          </Stack>
        </Box>
      ) : (
        <Alert
          data-testid="daily-quality-empty-state"
          severity={
            dailyQuality?.mode === "permission_limited" ? "info" : "success"
          }
          sx={{ borderRadius: 1 }}
        >
          {dailyQuality?.mode === "permission_limited"
            ? "A workspace admin needs to unlock the next quality setup action."
            : "No new quality signal needs review right now."}
        </Alert>
      )}

      {primaryAction ? (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "primary.main",
            borderRadius: 1,
            p: 2,
            bgcolor: "background.paper",
          }}
        >
          <Stack spacing={1}>
            <Typography variant="subtitle2">Primary action</Typography>
            <Typography variant="h6">{primaryAction.label}</Typography>
            <Typography variant="body2" color="text.secondary">
              {primaryAction.body}
            </Typography>
            {!primaryAction.routeAvailable ? (
              <Alert severity="info" sx={{ borderRadius: 1 }}>
                Opening the nearest available route.
              </Alert>
            ) : null}
            <Button
              data-testid="daily-quality-primary-action"
              variant="contained"
              component={href ? RouterLink : "button"}
              href={href || undefined}
              disabled={!href || isWriteBlocked}
              onClick={handlePrimaryClick}
              startIcon={<Iconify icon="mdi:arrow-right" width={18} />}
              sx={{ alignSelf: "flex-start" }}
            >
              {primaryAction.label}
            </Button>
          </Stack>
        </Box>
      ) : null}

      {weeklyReview?.due ? (
        <Box
          data-testid="weekly-quality-review"
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            p: 2,
            bgcolor: "background.paper",
          }}
        >
          <Stack spacing={1}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="subtitle2">Weekly team review</Typography>
              <Chip size="small" color="info" label="Due" />
            </Stack>
            <Typography variant="body2" color="text.secondary">
              {weeklyReview.summary}
            </Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap">
              <Chip
                size="small"
                variant="outlined"
                label={`${weeklyReview.unresolvedCount} unresolved`}
              />
              <Chip
                size="small"
                variant="outlined"
                label={`${weeklyReview.completedCount} completed`}
              />
            </Stack>
            <Button
              data-testid="weekly-quality-review-action"
              variant="outlined"
              component={RouterLink}
              href={weeklyReview.route}
              onClick={() => onWeeklyReviewOpen?.(weeklyReview)}
              startIcon={<Iconify icon="mdi:calendar-check" width={18} />}
              sx={{ alignSelf: "flex-start" }}
            >
              {weeklyReview.actionLabel || "Open weekly review"}
            </Button>
          </Stack>
        </Box>
      ) : null}

      {dailyQuality?.productCards?.length ? (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "1fr",
              md: "repeat(3, minmax(0, 1fr))",
            },
            gap: 1.5,
          }}
        >
          {dailyQuality.productCards.map((card) => (
            <ProductCard key={`${card.path}:${card.status}`} card={card} />
          ))}
        </Box>
      ) : null}
    </Stack>
  );
}

DailyQualityHome.propTypes = {
  canAct: PropTypes.bool,
  dailyQuality: PropTypes.object,
  onActionClick: PropTypes.func,
  onSignalReview: PropTypes.func,
  onWeeklyReviewOpen: PropTypes.func,
  recommendedAction: PropTypes.object,
};
