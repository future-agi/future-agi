import React from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { readableToken } from "../onboarding-home.constants";

const observeActionHref = (action) => {
  if (!action || action.blocked || !action.routeAvailable || !action.href) {
    return null;
  }
  return action.href;
};

export function ObservePanelHeader({
  eyebrow,
  title,
  description,
  chips = [],
}) {
  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={0.75} flexWrap="wrap">
        <Chip size="small" label={eyebrow} />
        {chips.map((chip) => (
          <Chip
            key={chip}
            size="small"
            variant="outlined"
            label={readableToken(chip)}
            sx={{ textTransform: "capitalize" }}
          />
        ))}
      </Stack>
      <Stack spacing={0.5}>
        <Typography variant="h6">{title}</Typography>
        <Typography variant="body2" color="text.secondary">
          {description}
        </Typography>
      </Stack>
    </Stack>
  );
}

ObservePanelHeader.propTypes = {
  chips: PropTypes.arrayOf(PropTypes.string),
  description: PropTypes.string.isRequired,
  eyebrow: PropTypes.string.isRequired,
  title: PropTypes.string.isRequired,
};

export function ObservePanelActions({
  action,
  fallbackAction,
  onPrimaryClick,
  onFallbackClick,
  onCheckAgain,
  isChecking = false,
  primaryVariant = "contained",
}) {
  const primaryHref = observeActionHref(action);
  const fallbackHref = observeActionHref(fallbackAction);

  return (
    <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
      <Button
        variant={primaryVariant}
        component={primaryHref ? RouterLink : "button"}
        href={primaryHref || undefined}
        disabled={!primaryHref}
        onClick={() => onPrimaryClick?.(action)}
        startIcon={<Iconify icon="mdi:arrow-right" width={18} />}
      >
        {action?.ctaLabel || "Open"}
      </Button>
      {fallbackAction ? (
        <Button
          variant="outlined"
          component={fallbackHref ? RouterLink : "button"}
          href={fallbackHref || undefined}
          disabled={!fallbackHref}
          onClick={() => onFallbackClick?.(fallbackAction)}
        >
          {fallbackAction.ctaLabel || "Fallback"}
        </Button>
      ) : null}
      {onCheckAgain ? (
        <Button
          variant="text"
          onClick={onCheckAgain}
          disabled={isChecking}
          startIcon={<Iconify icon="mdi:refresh" width={18} />}
        >
          Check again
        </Button>
      ) : null}
    </Stack>
  );
}

ObservePanelActions.propTypes = {
  action: PropTypes.object,
  fallbackAction: PropTypes.object,
  isChecking: PropTypes.bool,
  onCheckAgain: PropTypes.func,
  onFallbackClick: PropTypes.func,
  onPrimaryClick: PropTypes.func,
  primaryVariant: PropTypes.oneOf(["contained", "outlined", "text"]),
};
