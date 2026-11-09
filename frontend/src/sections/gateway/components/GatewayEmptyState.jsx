import React from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";

import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";

import { GATEWAY_ICON_NAMES, gatewayIconSrc } from "../shell/gateway-icons";

// The one empty state of the AI Gateway: Future AGI's EmptyLayout (the
// Datasets empty state: 68 px bordered tile, gradient glyph, m3 title, s1
// body) with at most one action. The presets keep the wording the gateway
// pages already use.
const VARIANTS = {
  "no-gateway": {
    icon: "gateway",
    title: "Gateway page unavailable",
    description:
      "We couldn't load the gateway page. Retry in a moment. If this continues, contact your administrator.",
    ctaLabel: "Retry",
  },
  "no-data": {
    icon: "inbox",
    title: "No data yet",
    description:
      "Data will appear here once requests start flowing through the gateway.",
  },
  "no-results": {
    icon: "search",
    title: "No results found",
    description: "Try adjusting your filters or search query.",
    ctaLabel: "Clear Filters",
  },
};

const GatewayEmptyState = ({
  variant = "no-data",
  icon,
  onAction,
  actionLabel,
  actionDisabled = false,
  title,
  description,
  titleComponent,
  errorMessage,
  sx,
}) => {
  const preset = VARIANTS[variant] || VARIANTS["no-data"];
  const buttonLabel = actionLabel || preset.ctaLabel;

  return (
    <EmptyLayout
      icon={gatewayIconSrc(icon || preset.icon)}
      title={title || preset.title}
      titleComponent={titleComponent}
      description={
        <>
          {description || preset.description}
          {typeof errorMessage === "string" && errorMessage.trim() && (
            <Typography
              component="span"
              typography="s2"
              color="status.danger.fg"
              sx={{ display: "block", mt: 1 }}
            >
              {errorMessage}
            </Typography>
          )}
        </>
      }
      sx={{ py: { xs: 6, sm: 8 }, px: 2, height: "auto", flex: 1, ...sx }}
      action={
        onAction && buttonLabel ? (
          <Button
            variant="outlined"
            color="primary"
            onClick={onAction}
            disabled={actionDisabled}
          >
            {buttonLabel}
          </Button>
        ) : null
      }
    />
  );
};

GatewayEmptyState.propTypes = {
  variant: PropTypes.oneOf(["no-gateway", "no-data", "no-results"]),
  icon: PropTypes.oneOf(GATEWAY_ICON_NAMES),
  onAction: PropTypes.func,
  actionLabel: PropTypes.string,
  actionDisabled: PropTypes.bool,
  title: PropTypes.string,
  description: PropTypes.node,
  // Optional heading element for the title (EmptyLayout keeps a div unset).
  titleComponent: PropTypes.elementType,
  errorMessage: PropTypes.string,
  sx: PropTypes.object,
};

export default GatewayEmptyState;
