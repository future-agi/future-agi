import React, { useId } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

// A titled section of a gateway settings page: the outlined panel the
// central Billing page uses for AI Gateway credits (radius 2, 16/24 px
// padding), a one-line description and at most a couple of actions.
export default function SettingsPanel({
  title,
  description,
  actions,
  children,
  testId,
  sx,
}) {
  const titleId = useId();
  return (
    <Paper
      variant="outlined"
      component="section"
      aria-labelledby={titleId}
      data-testid={testId}
      sx={{
        p: { xs: 2, sm: 3 },
        borderRadius: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        minWidth: 0,
        ...sx,
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", sm: "center" }}
        gap={1.5}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography
            id={titleId}
            component="h2"
            variant="subtitle1"
            fontWeight={600}
          >
            {title}
          </Typography>
          {description && (
            <Typography typography="s1" color="text.secondary">
              {description}
            </Typography>
          )}
        </Box>
        {actions && (
          <Stack direction="row" gap={1} flexWrap="wrap" flexShrink={0}>
            {actions}
          </Stack>
        )}
      </Stack>
      {children}
    </Paper>
  );
}

SettingsPanel.propTypes = {
  title: PropTypes.node.isRequired,
  description: PropTypes.node,
  actions: PropTypes.node,
  children: PropTypes.node,
  testId: PropTypes.string,
  sx: PropTypes.object,
};
