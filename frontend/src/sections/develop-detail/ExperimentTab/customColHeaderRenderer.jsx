import { Box, Typography } from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
const headerConfig = {
  name: {
    icon: (
      <SvgColor
        sx={{ width: "20px" }}
        src="/assets/icons/navbar/ic_experiment.svg"
      />
    ),
    label: "Experiment Name",
  },
  status: {
    icon: (
      <SvgColor
        sx={{ width: "20px" }}
        src="/assets/icons/ic_queued_header.svg"
      />
    ),
    label: "Status",
  },
  created_at: {
    icon: <Iconify icon="solar:clock-circle-linear" />,
    label: "Created At",
  },
  actions: {
    icon: (
      <SvgColor sx={{ width: "20px" }} src="/assets/icons/ic_actions.svg" />
    ),
    label: "Actions",
  },
};
const renderHeaderContent = (icon, label) => (
  <Box
    sx={{
      display: "flex",
      flexDirection: "row",
      alignItems: "center",
      gap: 1,
      color: "text.primary",
    }}
  >
    {icon}
    <Typography variant="s2_1" fontWeight={"fontWeightMedium"}>
      {label}
    </Typography>
  </Box>
);

const customColHeaderRenderer = (params) => {
  const fieldName = params?.column?.colId;
  const displayName = params?.displayName;

  if (headerConfig[fieldName]) {
    const { icon, label } = headerConfig[fieldName];
    return renderHeaderContent(icon, label);
  }

  return renderHeaderContent(
    <SvgColor src="/assets/icons/ic_col_header.svg" />,
    displayName,
  );
};

export default customColHeaderRenderer;
