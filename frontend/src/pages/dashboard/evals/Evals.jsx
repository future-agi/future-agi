import React from "react";
import { Box } from "@mui/material";
import { Helmet } from "react-helmet-async";
import EvalsListView from "src/sections/evals/components/EvalsListView";
import PageHeader from "src/components/page-header";

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
        <PageHeader
          title="Evaluations"
          info="Create and manage evaluations to measure your AI agent's performance — bias, toxicity, accuracy, and response quality."
          sx={{ mb: 0 }}
        />
        <EvalsListView />
      </Box>
    </>
  );
};

export default Evals;
