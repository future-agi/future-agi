import React from "react";
import { Box, Button } from "@mui/material";
import { Helmet } from "react-helmet-async";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import PersonaListView from "src/sections/persona/PersonaListView";

const Personas = () => {
  return (
    <>
      <Helmet>
        <title>Personas</title>
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
          title="Personas"
          info="Create and manage personas — activate or deactivate them to control scenario generation for your voice and chat simulations."
          sx={{ mb: 0 }}
          actions={
            <Button
              size="small"
              variant="outlined"
              component="a"
              href="https://docs.futureagi.com/docs/simulation/concepts/personas"
              target="_blank"
              rel="noopener noreferrer"
              startIcon={
                <Iconify icon="solar:document-text-linear" width={16} />
              }
            >
              View docs
            </Button>
          }
        />
        <PersonaListView />
      </Box>
    </>
  );
};

export default Personas;
