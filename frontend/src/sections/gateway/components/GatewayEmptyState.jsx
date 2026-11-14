import React from "react";
import PropTypes from "prop-types";
import { Button } from "@mui/material";
import EmptyState from "src/components/empty-state";
import Iconify from "src/components/iconify";

const VARIANTS = {
  "no-gateway": {
    icon: "solar:server-minimalistic-linear",
    title: "Gateway is starting up",
    description:
      "The Agent Command Center gateway is being provisioned. This usually takes a few seconds. If this persists, check that the gateway container is running.",
    ctaLabel: "Retry",
    ctaIcon: "solar:refresh-linear",
  },
  "no-data": {
    icon: "solar:database-linear",
    title: "No data yet",
    description:
      "Data will appear here once requests start flowing through the gateway.",
  },
  "no-results": {
    icon: "solar:filter-linear",
    title: "No results found",
    description: "Try adjusting your filters or search query.",
    ctaLabel: "Clear filters",
    ctaIcon: "solar:filter-linear",
  },
};

// Renders the Design System EmptyState; the variants keep their copy.
const GatewayEmptyState = ({
  variant = "no-data",
  onAction,
  actionLabel,
  title,
  description,
}) => {
  const preset = VARIANTS[variant] || VARIANTS["no-data"];
  const buttonLabel = actionLabel || preset.ctaLabel;
  const isFilter = variant === "no-results";
  return (
    <EmptyState
      variant={isFilter ? "no-results" : "first-run"}
      icon={preset.icon}
      title={title || preset.title}
      description={description || preset.description}
      onClearFilters={isFilter ? onAction : undefined}
      action={
        !isFilter && onAction && buttonLabel ? (
          <Button
            size="small"
            variant="outlined"
            startIcon={
              preset.ctaIcon ? (
                <Iconify icon={preset.ctaIcon} width={16} />
              ) : null
            }
            onClick={onAction}
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
  onAction: PropTypes.func,
  actionLabel: PropTypes.string,
  title: PropTypes.string,
  description: PropTypes.string,
};

export default GatewayEmptyState;
