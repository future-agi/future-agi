import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

export default function SectionCard({ title, subtitle, action, children, sx, dense }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        bgcolor: "background.paper",
        overflow: "hidden",
        ...sx,
      }}
    >
      {(title || action) && (
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          spacing={2}
          sx={{
            px: dense ? 1.5 : 2.5,
            py: dense ? 1 : 1.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box minWidth={0}>
            {/*
              component="div" so callers can pass a React element (a
              picker, a chip row, a compound layout) as title without
              producing invalid DOM — a div child inside a <p> triggers
              a browser auto-close and a hydration warning.
            */}
            <Typography component="div" sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>
              {title}
            </Typography>
            {subtitle && (
              <Typography component="div" sx={{ typography: "s2", color: "text.subtitle" }}>
                {subtitle}
              </Typography>
            )}
          </Box>
          {action}
        </Stack>
      )}
      {children}
    </Box>
  );
}
SectionCard.propTypes = {
  title: PropTypes.node, subtitle: PropTypes.node, action: PropTypes.node,
  children: PropTypes.node, sx: PropTypes.object, dense: PropTypes.bool,
};
