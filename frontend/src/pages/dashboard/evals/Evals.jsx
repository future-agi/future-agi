import React from "react";
import { Box, Tooltip, Typography } from "@mui/material";
import { Helmet } from "react-helmet-async";
import EvalsListView from "src/sections/evals/components/EvalsListView";
import Iconify from "src/components/iconify";

const Evals = () => {
  return (
    <>
      <Helmet>
        <title>Evaluations</title>
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
          <Typography variant="h6" fontWeight={600}>
            Evaluations
          </Typography>
          <Tooltip
            title="Create and manage evaluations to measure your AI agent's performance — bias, toxicity, accuracy, and response quality."
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
        <EvalsListView />
      </Box>
    </>
  );
};

export default Evals;
