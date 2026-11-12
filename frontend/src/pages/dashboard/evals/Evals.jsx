import React from "react";
import { Box } from "@mui/material";
import { Helmet } from "react-helmet-async";
import EvalsListView from "src/sections/evals/components/EvalsListView";
import EvalsPageHeader from "src/sections/evals/EvalsPageHeader";

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
        <EvalsPageHeader currentTab="evaluators" />
        <EvalsListView />
      </Box>
    </>
  );
};

export default Evals;
