import React from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

export default function SkillsNotice({ icon, children }) {
  return (
    <Stack
      direction="row"
      spacing={1}
      alignItems="flex-start"
      sx={{ p: 1.5, border: 1, borderColor: "divider", borderRadius: 1 }}
    >
      <Iconify
        icon={icon}
        width={16}
        sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
      />
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        {children}
      </Typography>
    </Stack>
  );
}

SkillsNotice.propTypes = {
  icon: PropTypes.string.isRequired,
  children: PropTypes.node.isRequired,
};
