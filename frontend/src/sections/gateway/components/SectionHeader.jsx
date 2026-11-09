import React from "react";
import PropTypes from "prop-types";
import { Box, Typography, Button, Stack, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

const SectionHeader = ({
  icon,
  title,
  subtitle,
  actions,
  statusChip,
  children,
}) => (
  <Stack
    direction="row"
    justifyContent="space-between"
    alignItems="center"
    mb={3}
  >
    <Box sx={{ minWidth: 0 }}>
      <Stack direction="row" alignItems="center" spacing={1.5}>
        {icon && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 36,
              height: 36,
              borderRadius: "50%",
              bgcolor: (theme) =>
                theme.palette.mode === "dark" ? "grey.800" : "primary.lighter",
              color: "primary.main",
              flexShrink: 0,
            }}
          >
            <Iconify icon={icon} width={20} />
          </Box>
        )}
        <Typography variant="h4" sx={{ wordBreak: "break-word" }}>
          {title}
        </Typography>
        {statusChip}
      </Stack>
      {subtitle && (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ mt: 0.5, ml: icon ? 6 : 0 }}
        >
          {subtitle}
        </Typography>
      )}
    </Box>

    {(actions || children) && (
      <Stack direction="row" spacing={1} flexShrink={0} sx={{ ml: 2 }}>
        {actions?.map((action, idx) => {
          const button = (
            <Button
              key={action.label || idx}
              variant={action.variant || "outlined"}
              size={action.size || "medium"}
              color={action.color || "primary"}
              startIcon={
                action.icon ? (
                  <Iconify icon={action.icon} width={20} />
                ) : undefined
              }
              onClick={action.onClick}
              disabled={action.disabled}
            >
              {action.label}
            </Button>
          );
          // A disabled button fires no events, so a tooltip that says why
          // (e.g. "Owner or Admin only") sits on a wrapping span.
          return action.tooltip ? (
            <Tooltip key={action.label || idx} title={action.tooltip}>
              <span style={{ display: "inline-flex" }}>{button}</span>
            </Tooltip>
          ) : (
            button
          );
        })}
        {children}
      </Stack>
    )}
  </Stack>
);

SectionHeader.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.string.isRequired,
  subtitle: PropTypes.string,
  actions: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      variant: PropTypes.string,
      size: PropTypes.string,
      color: PropTypes.string,
      icon: PropTypes.string,
      onClick: PropTypes.func,
      disabled: PropTypes.bool,
      tooltip: PropTypes.string,
    }),
  ),
  statusChip: PropTypes.node,
  children: PropTypes.node,
};

export default SectionHeader;
