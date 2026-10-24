import { Box, Button } from "@mui/material";
import React from "react";
import { useNavigate } from "react-router";
import Iconify from "src/components/iconify";
import IndividualDevelopExperimentSelect from "./IndividualDevelopExperimentSelect";

const IndividualExperimentRow = () => {
  const navigate = useNavigate();

  return (
    <Box
      sx={{
        paddingTop: 2,
        paddingX: 1,
        display: "flex",
        gap: 2,
        alignItems: "center",
      }}
    >
      <Button
        size="small"
        startIcon={
          <Iconify
            icon="solar:alt-arrow-left-linear"
            width="24px"
            sx={{ color: "primary.main" }}
          />
        }
        onClick={() => navigate(-1)}
      >
        Back
      </Button>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <IndividualDevelopExperimentSelect />
      </Box>
    </Box>
  );
};

export default IndividualExperimentRow;
