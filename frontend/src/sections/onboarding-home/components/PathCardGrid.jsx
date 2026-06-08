import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";

const pathStatusLabel = (path, { isPreviewOnly, isSelected }) => {
  if (isPreviewOnly) return "Preview only";
  if (isSelected) return "Current setup";
  if (!path.isAvailable) return "Not available yet";
  return "Available";
};

const blockedReasonLabel = (reason) => {
  if (!reason) return null;
  if (reason === "route_not_implemented")
    return "This setup path is not ready yet.";
  if (reason === "feature_disabled") return "This setup path is disabled.";
  if (reason === "permission_limited")
    return "You need workspace write access.";
  return "This setup path is not available yet.";
};

const pathButtonLabel = (path) => {
  if (path.id === "sample") return "Preview only";
  if (path.status === "selected") return "Current setup";
  if (!path.isAvailable) return "Unavailable";
  return "Choose this";
};

export default function PathCardGrid({
  description = "Switch to another product area if this is not the setup you need.",
  isChangingPath = false,
  paths = [],
  title = "Change setup path",
  onPathClick,
}) {
  if (!paths.length) return null;

  return (
    <Box
      data-testid="onboarding-path-card-grid"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
      }}
    >
      <Stack spacing={1.25}>
        <Stack spacing={0.25}>
          <Typography variant="subtitle2">{title}</Typography>
          <Typography variant="body2" color="text.secondary">
            {description}
          </Typography>
        </Stack>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))" },
            gap: 1,
          }}
        >
          {paths.map((path) => {
            const isSelected = path.status === "selected";
            const isPreviewOnly = path.id === "sample";
            const isDisabled =
              isPreviewOnly ||
              !path.isAvailable ||
              isSelected ||
              isChangingPath;
            const href =
              !onPathClick && path.isAvailable && !isPreviewOnly && path.href
                ? path.href
                : null;
            return (
              <Box
                key={path.id}
                data-testid={`onboarding-path-card-${path.id}`}
                sx={{
                  minHeight: 132,
                  border: "1px solid",
                  borderColor: path.isAvailable ? "divider" : "action.disabled",
                  borderRadius: 1,
                  p: 1.5,
                  bgcolor:
                    path.status === "selected" ? "action.hover" : "inherit",
                  opacity: path.isAvailable ? 1 : 0.64,
                }}
              >
                <Stack spacing={1} sx={{ height: "100%" }}>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Typography variant="subtitle2">{path.label}</Typography>
                    <Chip
                      size="small"
                      label={pathStatusLabel(path, {
                        isPreviewOnly,
                        isSelected,
                      })}
                    />
                  </Stack>
                  <Typography variant="body2" color="text.secondary">
                    {path.description}
                  </Typography>
                  {blockedReasonLabel(path.blockedReason) ? (
                    <Typography variant="caption" color="text.secondary">
                      {blockedReasonLabel(path.blockedReason)}
                    </Typography>
                  ) : null}
                  <Box sx={{ flexGrow: 1 }} />
                  <Button
                    size="small"
                    variant="text"
                    component={href ? RouterLink : "button"}
                    href={href || undefined}
                    disabled={isDisabled}
                    onClick={() => {
                      if (!isPreviewOnly) onPathClick?.(path);
                    }}
                    endIcon={<Iconify icon="mdi:arrow-right" width={16} />}
                    sx={{ alignSelf: "flex-start", px: 0.5 }}
                  >
                    {pathButtonLabel(path)}
                  </Button>
                </Stack>
              </Box>
            );
          })}
        </Box>
      </Stack>
    </Box>
  );
}

PathCardGrid.propTypes = {
  description: PropTypes.string,
  isChangingPath: PropTypes.bool,
  onPathClick: PropTypes.func,
  paths: PropTypes.array,
  title: PropTypes.string,
};
