import React from "react";
import { Box, Tooltip, Typography } from "@mui/material";
import { Helmet } from "react-helmet-async";
import DevelopView from "src/sections/develop/DevelopView";
import Iconify from "src/components/iconify";
import { DATASETS_TITLE } from "src/sections/develop-detail/datasetTitle";

const Develop = () => {
  return (
    <>
      <Helmet>
        <title>{DATASETS_TITLE}</title>
      </Helmet>
      <Box
        sx={{
          backgroundColor: "background.paper",
          height: "100%",
          p: 2,
          display: "flex",
          flexDirection: "column",
          gap: 1.5,
          overflow: "hidden",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <Typography variant="h6" component="h1" fontWeight={600}>
            Datasets
          </Typography>
          <Tooltip
            title="Manage datasets across your development lifecycle — create, update, and use them to evaluate prompts and workflows."
            arrow
            placement="right"
          >
            <Box sx={{ display: "flex", cursor: "help" }}>
              <Iconify
                icon="solar:info-circle-line-duotone"
                width={16}
                sx={{ color: "text.disabled" }}
              />
            </Box>
          </Tooltip>
        </Box>
        <DevelopView />
      </Box>
    </>
  );
};

export default Develop;
