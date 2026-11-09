import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

/** A bordered block with a small heading, the building unit of the detail tabs. */
export default function Section({
  title,
  caption,
  action,
  children,
  testId,
  sx,
}) {
  return (
    <Box
      data-testid={testId}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        minWidth: 0,
        ...sx,
      }}
    >
      {(title || action) && (
        <Box
          sx={{
            px: 2,
            pt: 1.5,
            pb: caption ? 0.5 : 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 1,
          }}
        >
          <Typography
            typography="s1"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            {title}
          </Typography>
          {action}
        </Box>
      )}
      {caption && (
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ px: 2, pb: 1 }}
        >
          {caption}
        </Typography>
      )}
      <Box sx={{ px: 2, pb: 2 }}>{children}</Box>
    </Box>
  );
}

Section.propTypes = {
  title: PropTypes.node,
  caption: PropTypes.node,
  action: PropTypes.node,
  children: PropTypes.node,
  testId: PropTypes.string,
  sx: PropTypes.object,
};

/** Label / value rows: two columns on wide screens, stacked on phones. */
export function Facts({ rows }) {
  return (
    <Box
      component="dl"
      sx={{
        m: 0,
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "160px 1fr" },
        columnGap: 2,
        rowGap: { xs: 0.25, sm: 1 },
      }}
    >
      {rows.map(({ label, value, testId }) => (
        <React.Fragment key={label}>
          <Typography
            component="dt"
            typography="s2"
            color="text.secondary"
            sx={{ pt: { xs: 1, sm: 0.25 } }}
          >
            {label}
          </Typography>
          <Typography
            component="dd"
            typography="s1"
            color="text.primary"
            data-testid={testId}
            sx={{ m: 0, minWidth: 0, overflowWrap: "anywhere" }}
          >
            {value}
          </Typography>
        </React.Fragment>
      ))}
    </Box>
  );
}

Facts.propTypes = {
  rows: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.node,
      testId: PropTypes.string,
    }),
  ).isRequired,
};
