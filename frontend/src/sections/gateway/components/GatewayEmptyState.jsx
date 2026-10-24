import React from "react";
import PropTypes from "prop-types";
import { Box, Typography, Button } from "@mui/material";
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
    ctaLabel: "Clear Filters",
    ctaIcon: "solar:filter-linear",
  },
};

const GatewayEmptyState = ({
  variant = "no-data",
  onAction,
  actionLabel,
  title,
  description,
}) => {
  const preset = VARIANTS[variant] || VARIANTS["no-data"];
  const buttonLabel = actionLabel || preset.ctaLabel;

  return (
    <Box
      display="flex"
      flexDirection="column"
      alignItems="center"
      justifyContent="center"
      py={8}
      px={3}
    >
      <Iconify
        icon={preset.icon}
        width={56}
        sx={{ color: "text.disabled", mb: 2 }}
      />
      <Typography variant="h6" color="text.secondary" mb={1} textAlign="center">
        {title || preset.title}
      </Typography>
      <Typography
        variant="body2"
        color="text.secondary"
        mb={3}
        textAlign="center"
        sx={{ maxWidth: 400 }}
      >
        {description || preset.description}
      </Typography>
      {onAction && buttonLabel && (
        <Button
          variant="outlined"
          onClick={onAction}
          startIcon={
            preset.ctaIcon ? (
              <Iconify icon={preset.ctaIcon} width={20} />
            ) : undefined
          }
        >
          {buttonLabel}
        </Button>
      )}
    </Box>
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
