import PropTypes from "prop-types";
import { useContext } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { SettingsLayoutContext } from "./settingsLayout";

/**
 * A settings section on the agent page (Constraints, Connections): its name
 * and one short line on the left, its cards on the right.
 */
export default function SettingsSection({ title, sub, children }) {
  const stacked = useContext(SettingsLayoutContext) === "stacked";
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: stacked
          ? "1fr"
          : { xs: "1fr", lg: "220px minmax(0, 1fr)" },
        columnGap: 4,
        rowGap: 1.25,
        py: 2.5,
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box>
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          {title}
        </Typography>
        {sub && (
          <Typography
            component="div"
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
          >
            {sub}
          </Typography>
        )}
      </Box>
      <Stack spacing={1.5} sx={{ minWidth: 0 }}>
        {children}
      </Stack>
    </Box>
  );
}
SettingsSection.propTypes = {
  title: PropTypes.node,
  sub: PropTypes.node,
  children: PropTypes.node,
};
