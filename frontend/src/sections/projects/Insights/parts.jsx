import React from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Chip,
  Link,
  Paper,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import { Link as RouterLink } from "react-router-dom";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { formatCount, formatPercent } from "./insightsFormat";
import { attributePills, facetChips } from "./insightsLenses";
import {
  isBorderline,
  notableDirection,
  notableShort,
  notableText,
  notableTooltip,
} from "./insightsNotable";

// Small building blocks shared by the report, the drawers and the new-report
// flow.

const BillingLink = () => (
  <Link component={RouterLink} to={paths.dashboard.settings.billing}>
    Open billing
  </Link>
);

/** An error, with the way to billing when it was refused for credits. */
export function ErrorText({ message, billing }) {
  return (
    <>
      {message}
      {billing && (
        <>
          {" "}
          <BillingLink />
        </>
      )}
    </>
  );
}

ErrorText.propTypes = {
  message: PropTypes.node,
  billing: PropTypes.bool,
};

// The smallest sample a report analyses.
const MIN_SAMPLE = 10;

/**
 * Why a report can't run on the credits left, at full contrast, with the ways
 * forward: a sample the credits pay for, and billing, where the plan or the
 * budget limit that holds it back is changed.
 */
export function CreditLimit({ credits, onSample }) {
  const fits = credits.maxSample;
  return (
    <Typography variant="body2" color="text.primary" role="status">
      {credits.message ||
        "The credits left this month don't cover this report."}{" "}
      {fits >= MIN_SAMPLE && onSample && (
        <>
          <Link component="button" type="button" onClick={() => onSample(fits)}>
            Analyse a sample of {formatCount(fits)} instead
          </Link>
          {" · "}
        </>
      )}
      <BillingLink />
    </Typography>
  );
}

CreditLimit.propTypes = {
  credits: PropTypes.shape({
    message: PropTypes.string,
    maxSample: PropTypes.number,
  }).isRequired,
  onSample: PropTypes.func,
};

export function Swatch({ color, size = 10 }) {
  return (
    <Box
      aria-hidden
      sx={{
        width: size,
        height: size,
        borderRadius: "3px",
        bgcolor: color || "grey.400",
        flexShrink: 0,
      }}
    />
  );
}

Swatch.propTypes = { color: PropTypes.string, size: PropTypes.number };

// `flush` drops the body padding, for a table that runs edge to edge.
export function SectionCard({
  title,
  subtitle,
  action,
  children,
  sx,
  id,
  flush,
}) {
  return (
    <Paper
      id={id}
      variant="outlined"
      sx={{ p: flush ? 0 : 2.5, borderRadius: 1.5, overflow: "hidden", ...sx }}
    >
      {(title || action) && (
        <Stack
          direction="row"
          alignItems="flex-start"
          justifyContent="space-between"
          spacing={2}
          sx={flush ? { px: 2.5, pt: 2.5, pb: 1.5 } : { mb: children ? 2 : 0 }}
        >
          <Box sx={{ minWidth: 0 }}>
            {title && (
              <Typography variant="subtitle1" component="h2">
                {title}
              </Typography>
            )}
            {subtitle && (
              <Typography variant="body2" color="text.secondary">
                {subtitle}
              </Typography>
            )}
          </Box>
          {action}
        </Stack>
      )}
      {children}
    </Paper>
  );
}

SectionCard.propTypes = {
  title: PropTypes.node,
  subtitle: PropTypes.node,
  action: PropTypes.node,
  children: PropTypes.node,
  sx: PropTypes.object,
  id: PropTypes.string,
  flush: PropTypes.bool,
};

/** A category as a small outlined chip that opens it. */
export function CategoryLink({ category, color, onClick, maxWidth = 260 }) {
  if (!category) return null;
  return (
    <Tooltip
      describeChild
      arrow
      title={
        <>
          <Box component="span" sx={{ display: "block", fontWeight: 600 }}>
            {category.name}
          </Box>
          {category.description}
        </>
      }
      enterDelay={400}
    >
      <Box
        component="button"
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onClick(category.id);
        }}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.75,
          maxWidth,
          height: 22,
          px: 0.75,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.75,
          bgcolor: "background.paper",
          color: "text.primary",
          font: "inherit",
          fontSize: 12,
          cursor: "pointer",
          "&:hover": { borderColor: "text.disabled" },
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: 1,
          },
        }}
      >
        <Swatch color={color} size={8} />
        <Box
          component="span"
          sx={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {category.name}
        </Box>
      </Box>
    </Tooltip>
  );
}

CategoryLink.propTypes = {
  category: PropTypes.object,
  color: PropTypes.string,
  onClick: PropTypes.func.isRequired,
  maxWidth: PropTypes.number,
};

// The theme's warning yellow is unreadable as text on a light surface, so the
// borderline tone uses its darker icon step there.
export const cautionColor = (theme) =>
  theme.palette.mode === "dark"
    ? theme.palette.warning.light
    : theme.palette.warning.icon ||
      theme.palette.warning.darker ||
      theme.palette.warning.dark;

export const toneColor = (theme, tone) =>
  ({
    worse: theme.palette.error.main,
    borderline: cautionColor(theme),
    better: theme.palette.success.dark,
    bad: theme.palette.error.main,
    good: theme.palette.success.dark,
  })[tone] || theme.palette.text.secondary;

export function ToneChip({ label, tone = "neutral", icon, title, sx }) {
  const theme = useTheme();
  const color = toneColor(theme, tone);
  const chip = (
    <Chip
      size="small"
      label={label}
      icon={icon ? <Iconify icon={icon} width={13} /> : undefined}
      sx={{
        height: 22,
        maxWidth: "100%",
        borderRadius: 0.75,
        fontSize: 12,
        fontWeight: 500,
        color,
        bgcolor: alpha(color, tone === "neutral" ? 0.08 : 0.1),
        "& .MuiChip-icon": { color, ml: 0.5, mr: -0.25 },
        "& .MuiChip-label": { px: 0.75 },
        ...sx,
      }}
    />
  );
  return title ? (
    <Tooltip describeChild title={title} arrow>
      {chip}
    </Tooltip>
  ) : (
    chip
  );
}

ToneChip.propTypes = {
  label: PropTypes.node.isRequired,
  tone: PropTypes.string,
  icon: PropTypes.string,
  title: PropTypes.node,
  sx: PropTypes.object,
};

const DIRECTION_ICON = {
  worse: "mdi:arrow-top-right",
  better: "mdi:arrow-bottom-right",
  neutral: "mdi:swap-horizontal",
};

const noteTone = (note) => {
  const direction = notableDirection(note);
  if (direction === "neutral") return "neutral";
  if (direction === "better") return "better";
  return isBorderline(note) ? "borderline" : "worse";
};

const directionIcon = (note) => {
  const direction = notableDirection(note);
  if (direction === "neutral") return DIRECTION_ICON.neutral;
  return note.value > note.rest ? "mdi:arrow-up" : "mdi:arrow-down";
};

/** A notable difference as a compact chip ("Unresolved 0% vs 59%"). */
export function NotableChip({ note }) {
  return (
    <ToneChip
      label={notableShort(note)}
      tone={noteTone(note)}
      icon={directionIcon(note)}
      title={
        <>
          <Box component="span" sx={{ display: "block", fontWeight: 600 }}>
            {notableText(note)}
          </Box>
          {notableTooltip(note)}
        </>
      }
    />
  );
}

NotableChip.propTypes = { note: PropTypes.object.isRequired };

/** A notable difference as a sentence with its direction marked. */
export function NotableLine({ note }) {
  const theme = useTheme();
  const tone = noteTone(note);
  return (
    <Stack direction="row" spacing={1} alignItems="flex-start">
      <Iconify
        icon={directionIcon(note)}
        width={16}
        sx={{ color: toneColor(theme, tone), mt: "3px", flexShrink: 0 }}
      />
      <Box>
        <Typography variant="body2">{notableText(note)}</Typography>
        <Typography variant="caption" color="text.secondary">
          {notableTooltip(note)}
        </Typography>
      </Box>
    </Stack>
  );
}

NotableLine.propTypes = { note: PropTypes.object.isRequired };

export function FacetChips({ facets, dense }) {
  const chips = facetChips(facets);
  if (!chips.length) return null;
  return (
    <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap">
      {chips.map((chip) => (
        <ToneChip
          key={chip.key}
          label={chip.label}
          tone={chip.tone}
          sx={dense ? { height: 20, fontSize: 11 } : undefined}
        />
      ))}
    </Stack>
  );
}

FacetChips.propTypes = { facets: PropTypes.object, dense: PropTypes.bool };

export function AttributePills({ attributes }) {
  const pills = attributePills(attributes);
  if (!pills.length) return null;
  return (
    <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap">
      {pills.map((pill) => (
        <Box
          key={pill.key}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 10,
            fontSize: 11,
            lineHeight: "18px",
            overflow: "hidden",
          }}
        >
          <Box
            component="span"
            sx={{ px: 0.75, color: "text.secondary", whiteSpace: "nowrap" }}
          >
            {pill.label}
          </Box>
          <Box
            component="span"
            sx={{
              px: 0.75,
              fontWeight: 600,
              bgcolor:
                pill.tone === "on"
                  ? "success.lighter"
                  : pill.tone === "off"
                    ? "background.neutral"
                    : "action.hover",
              color: pill.tone === "on" ? "success.darker" : "text.primary",
            }}
          >
            {pill.value}
          </Box>
        </Box>
      ))}
    </Stack>
  );
}

AttributePills.propTypes = { attributes: PropTypes.object };

/**
 * One stacked bar of shares. Segments are separated by a 2px surface gap and
 * each carries its own tooltip; clicking one opens its category.
 */
export function StackedBar({ segments, height = 12, onSegmentClick, label }) {
  const visible = segments.filter((s) => s.count > 0);
  return (
    <Box
      role="img"
      aria-label={
        label ||
        visible.map((s) => `${s.name} ${formatPercent(s.share)}`).join(", ")
      }
      sx={{ display: "flex", gap: "2px", height, width: "100%" }}
    >
      {visible.map((segment) => (
        <Tooltip
          describeChild
          key={segment.id}
          arrow
          title={`${segment.name} · ${formatCount(segment.count)} (${formatPercent(segment.share)})`}
        >
          <Box
            onClick={
              onSegmentClick && !segment.members?.length
                ? () => onSegmentClick(segment.id)
                : undefined
            }
            sx={{
              flexGrow: segment.share,
              flexBasis: 0,
              minWidth: 4,
              bgcolor: segment.color,
              borderRadius: "3px",
              cursor:
                onSegmentClick && !segment.members?.length
                  ? "pointer"
                  : "default",
              transition: "opacity 120ms",
              "&:hover": { opacity: 0.85 },
            }}
          />
        </Tooltip>
      ))}
    </Box>
  );
}

StackedBar.propTypes = {
  segments: PropTypes.array.isRequired,
  height: PropTypes.number,
  onSegmentClick: PropTypes.func,
  label: PropTypes.string,
};

export function Legend({ segments, onClick, dense }) {
  return (
    <Stack
      direction="row"
      useFlexGap
      flexWrap="wrap"
      columnGap={dense ? 1.5 : 2}
      rowGap={0.5}
    >
      {segments
        .filter((s) => s.count > 0)
        .map((segment) => {
          const clickable = onClick && !segment.members?.length;
          return (
            <Stack
              key={segment.id}
              direction="row"
              spacing={0.75}
              alignItems="center"
              component={clickable ? "button" : "div"}
              type={clickable ? "button" : undefined}
              onClick={clickable ? () => onClick(segment.id) : undefined}
              sx={{
                minWidth: 0,
                maxWidth: dense ? 220 : 320,
                p: 0,
                border: 0,
                bgcolor: "transparent",
                font: "inherit",
                textAlign: "left",
                cursor: clickable ? "pointer" : "default",
                color: "text.primary",
                "&:hover .legend-name": clickable
                  ? { textDecoration: "underline" }
                  : undefined,
              }}
            >
              <Swatch color={segment.color} size={dense ? 8 : 10} />
              <Typography
                className="legend-name"
                variant={dense ? "caption" : "body2"}
                noWrap
                sx={{ minWidth: 0 }}
              >
                {segment.name}
              </Typography>
              <Typography
                variant={dense ? "caption" : "body2"}
                color="text.secondary"
                sx={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}
              >
                {formatPercent(segment.share)}
              </Typography>
            </Stack>
          );
        })}
    </Stack>
  );
}

Legend.propTypes = {
  segments: PropTypes.array.isRequired,
  onClick: PropTypes.func,
  dense: PropTypes.bool,
};

export function ErrorState({ message, onRetry }) {
  return (
    <Alert
      severity="error"
      action={
        onRetry && (
          <Button color="inherit" size="small" onClick={onRetry}>
            Retry
          </Button>
        )
      }
    >
      {message}
    </Alert>
  );
}

ErrorState.propTypes = { message: PropTypes.node, onRetry: PropTypes.func };

/** A labelled number for stat grids. */
export function Stat({ label, value, hint, tone }) {
  const theme = useTheme();
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block" }}
        noWrap
      >
        {label}
      </Typography>
      <Typography
        variant="subtitle1"
        sx={{
          fontVariantNumeric: "tabular-nums",
          color: tone === "warning" ? cautionColor(theme) : "text.primary",
        }}
      >
        {value}
      </Typography>
      {hint && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block" }}
        >
          {hint}
        </Typography>
      )}
    </Box>
  );
}

Stat.propTypes = {
  label: PropTypes.node.isRequired,
  value: PropTypes.node,
  hint: PropTypes.node,
  tone: PropTypes.string,
};
