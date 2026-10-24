import { Box, Typography } from "@mui/material";
import React, { useCallback, useEffect, useState } from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";

const ExperimentHeader = (props) => {
  const { column } = props;

  const [sortState, setSortState] = useState(() => column.getSort());

  const onSortChanged = useCallback(() => {
    const sort = column.getSort();
    if (sort === undefined) return;
    setSortState(sort);
  }, [setSortState]);

  useEffect(() => {
    column.addEventListener("sortChanged", onSortChanged);

    return () => {
      column.removeEventListener("sortChanged", onSortChanged);
    };
  }, []);

  const renderIcon = () => {
    switch (props?.column?.colId) {
      case "experimentDatasetName":
        return (
          <Iconify
            icon="solar:notes-linear"
            sx={{ width: "20px", height: "20px", color: "text.primary" }}
          />
        );

      case "averageResponseTime":
        return (
          <Iconify
            icon="solar:clock-circle-linear"
            sx={{ width: "20px", height: "20px", color: "text.primary" }}
          />
        );
      case "promptToken":
      case "completionToken":
      case "totalToken":
        return (
          <SvgColor
            src={`/assets/icons/ic_tokens_experiment.svg`}
            sx={{ width: "20px", height: "20px", color: "text.primary" }}
          />
        );
      case "rank":
        // return (
        //   <Iconify
        //     icon="solar:star-linear"
        //     sx={{ width: "20px", height: "20px", color: "text.primary" }}
        //   />
        // );
        return null;
      default:
        return (
          <Iconify
            icon="solar:check-circle-linear"
            sx={{ width: "20px", height: "20px", color: "info.success" }}
          />
        );
    }
  };

  const renderSortIcon = () => {
    if (sortState === "asc") {
      return (
        <Iconify
          icon="solar:arrow-up-linear"
          width={20}
          sx={{ color: "text.secondary" }}
        />
      );
    }
    if (sortState === "desc") {
      return (
        <Iconify
          icon="solar:arrow-down-linear"
          width={20}
          sx={{ color: "text.secondary" }}
        />
      );
    }
  };
  return (
    <Box
      onClick={() => props?.progressSort(true)}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        cursor: "pointer",
        flexDirection: "row",

        textAlign: "center",
        width: "100%",
      }}
    >
      <Box sx={{ flexShrink: 0 }}>{renderIcon()}</Box>

      <Typography
        variant="s2_1"
        fontWeight={"fontWeightMedium"}
        color={"text.primary"}
        sx={{
          textOverflow: "ellipsis",
          overflow: "hidden",
          whiteSpace: "nowrap",
          paddingX: 1,
        }}
      >
        {props?.column?.colId !== "rank" ? props?.displayName : <></>}
      </Typography>

      {renderSortIcon()}
    </Box>
  );
};

ExperimentHeader.propTypes = {
  displayName: PropTypes.string,
  column: PropTypes.object,
  progressSort: PropTypes.func,
};

export default ExperimentHeader;
