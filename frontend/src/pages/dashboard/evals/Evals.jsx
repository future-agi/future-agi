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
          description="Create, test and reuse evals that score responses for accuracy, safety, tone and more."
          sx={{ mb: 0 }}
        />
        <EvalsListView />
      </Box>
    </>
  );
};

export default Evals;
