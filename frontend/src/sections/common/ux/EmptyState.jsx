import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Chip, Link, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import Iconify from "src/components/iconify";

const ActionButton = ({ action, primary }) => {
  const props = {
    size: "small",
    variant: primary ? "contained" : "outlined",
    color: primary ? "primary" : "inherit",
    startIcon: action.icon ? <Iconify icon={action.icon} width={16} /> : null,
    disabled: action.disabled,
  };
  return action.href ? (
    <Button {...props} component={RouterLink} to={action.href}>
      {action.label}
    </Button>
  ) : (
    <Button {...props} onClick={action.onClick}>
      {action.label}
    </Button>
  );
};
ActionButton.propTypes = { action: PropTypes.object, primary: PropTypes.bool };

/**
 * EmptyState — an empty or filtered-out view that teaches: icon, `s1`
 * title, one sentence (≤ 25 words), ≤ 2 actions (the first is primary),
 * an optional docs link and the active filter chips with `Clear all`.
 *
 * Props (frozen):
 * @param {string} [props.icon="mdi:tray-remove"]
 * @param {string} props.title  `No experiments yet`
 * @param {React.ReactNode} [props.body]
 * @param {{label, onClick?, href?, icon?, disabled?}[]} [props.actions]
 * @param {string} [props.docsHref]
 * @param {{label: string, onDelete?: Function}[]} [props.filterChips]
 * @param {Function} [props.onClearFilters]  shows `Clear all`
 * @param {boolean} [props.compact]  inline (table body) instead of centred
 * @param {object} [props.sx]
 */
export default function EmptyState({
  icon = "mdi:tray-remove",
  title,
  body,
  actions = [],
  docsHref,
  filterChips = [],
  onClearFilters,
  compact = false,
  sx,
}) {
  return (
    <Stack
      alignItems="center"
      textAlign="center"
      gap={1}
      data-testid="empty-state"
      sx={{ py: compact ? 3 : 6, px: 2, maxWidth: 480, mx: "auto", ...sx }}
    >
      <Box
        sx={{
          width: 44,
          height: 44,
          borderRadius: "50%",
          display: "grid",
          placeItems: "center",
          bgcolor: "background.neutral",
          color: "text.secondary",
          mb: 0.5,
        }}
      >
        <Iconify icon={icon} width={22} />
      </Box>
      <Typography
        typography="s1"
        fontWeight="fontWeightSemiBold"
        color="text.primary"
      >
        {title}
      </Typography>
      {body && (
        <Typography typography="s2" color="text.secondary">
          {body}
        </Typography>
      )}
      {filterChips.length > 0 && (
        <Stack
          direction="row"
          flexWrap="wrap"
          justifyContent="center"
          gap={0.75}
          sx={{ mt: 0.5 }}
        >
          {filterChips.map((c) => (
            <Chip
              key={c.label}
              size="small"
              label={c.label}
              onDelete={c.onDelete}
            />
          ))}
          {onClearFilters && (
            <Button
              size="small"
              variant="text"
              onClick={onClearFilters}
              sx={{ typography: "s2" }}
            >
              Clear all
            </Button>
          )}
        </Stack>
      )}
      {(actions.length > 0 || docsHref) && (
        <Stack direction="row" alignItems="center" gap={1} sx={{ mt: 1 }}>
          {actions.slice(0, 2).map((a, i) => (
            <ActionButton key={a.label} action={a} primary={i === 0} />
          ))}
          {docsHref && (
            <Link
              href={docsHref}
              target="_blank"
              rel="noopener noreferrer"
              typography="s2"
              underline="hover"
              sx={{ ml: actions.length ? 0.5 : 0 }}
            >
              Learn more ↗
            </Link>
          )}
        </Stack>
      )}
    </Stack>
  );
}

EmptyState.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.node.isRequired,
  body: PropTypes.node,
  actions: PropTypes.arrayOf(PropTypes.object),
  docsHref: PropTypes.string,
  filterChips: PropTypes.arrayOf(PropTypes.object),
  onClearFilters: PropTypes.func,
  compact: PropTypes.bool,
  sx: PropTypes.object,
};
