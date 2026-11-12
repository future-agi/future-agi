import React from "react";
import { Box } from "@mui/material";
import { Helmet } from "react-helmet-async";
import DevelopView from "src/sections/develop/DevelopView";
import { DATASETS_TITLE } from "src/sections/develop-detail/datasetTitle";
import PageHeader from "src/components/page-header";

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
        <PageHeader
          title="Datasets"
          description="Build test data, then run prompts, evals and experiments against it."
          info="Manage datasets across your development lifecycle — create, update, and use them to evaluate prompts and workflows."
          sx={{ mb: 0 }}
        />
        <DevelopView />
      </Box>
    </>
  );
};

export default Develop;
