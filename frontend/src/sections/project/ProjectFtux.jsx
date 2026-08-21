import { Box, Typography } from "@mui/material";
import React, { useEffect, useState } from "react";
import { ShowComponent } from "src/components/show";
import { useLocation } from "react-router-dom";

import NewExperiment from "./NewProject/NewExperiment";
import NewObserve from "./NewProject/NewObserve";
import { PROJECT_FTUX_COPY } from "./common";

const ProjectFtux = () => {
  const location = useLocation();
  const currentPath = location.pathname;
  const [isObserve, setIsObserve] = useState(currentPath.includes("observe"));

  useEffect(() => {
    const isObserve = currentPath.includes("observe");
    setIsObserve(isObserve);
  }, [currentPath]);

  const copy = isObserve
    ? PROJECT_FTUX_COPY.observe
    : PROJECT_FTUX_COPY.experiment;

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        paddingX: "16px",
        paddingTop: "12px",
        paddingBottom: "12px",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
      }}
    >
      <Box
        sx={{ display: "flex", flexDirection: "column", alignItems: "center" }}
      >
        <Box
          component={"img"}
          sx={{ height: "51px", width: "51px" }}
          src="/favicon/logo.svg"
        />
        <Box sx={{ height: "10px" }} />
        <Typography fontSize="20px" fontWeight={700} color="text.primary">
          {copy.title}
        </Typography>
        <Box sx={{ height: "5px" }} />
        <Typography fontSize="14px" color="text.secondary">
          {copy.description}
        </Typography>
        <Box sx={{ height: "20px" }} />
      </Box>
      <Box sx={{ height: "27px" }} />
      <ShowComponent condition={!isObserve}>
        <NewExperiment />
      </ShowComponent>
      <ShowComponent condition={isObserve}>
        <NewObserve />
      </ShowComponent>
    </Box>
  );
};

export default ProjectFtux;
