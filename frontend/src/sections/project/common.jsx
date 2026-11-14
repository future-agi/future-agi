import { Box, Skeleton, Stack, Typography } from "@mui/material";
import { format } from "date-fns";
import PropTypes from "prop-types";
import React from "react";
import { timeAgoFormatter } from "src/utils/dateTimeUtils";

import { formatNumberWithCommas } from "../projects/UsersView/common";
import SparklineCell from "./SparklineCell";
export const formatDateTime = (value) => {
  if (!value) return "";
  const date = new Date(value);
  if (isNaN(date.getTime())) return "";
  return format(date, "MM-dd-yyyy, HH:mm");
};
export const ProjectWithDate = ({ data }) => {
  const name = data?.name;
  const createdAt = data?.created_at;
  const formattedCreatedTime = formatDateTime(createdAt);
  return (
    <Stack
      display="flex"
      direction="column"
      justifyContent="center"
      height="100%"
      spacing={0.5}
      width="100%"
    >
      <Typography
        variant="s1"
        fontWeight="fontWeightMedium"
        color="text.primary"
      >
        {name}
      </Typography>

      <Typography
        variant="s3"
        fontWeight={"fontWeightRegular"}
        sx={{ color: "text.disabled" }}
      >
        Created at {formattedCreatedTime}
      </Typography>
    </Stack>
  );
};

ProjectWithDate.propTypes = {
  data: PropTypes.object,
};

export const ProjectDateModifiedCellRenderer = ({ data }) => {
  const updatedAt = data?.updated_at;
  const name = timeAgoFormatter(updatedAt);
  const formattedUpdatedDate = formatDateTime(updatedAt);

  return (
    <Stack
      display="flex"
      direction="column"
      justifyContent="center"
      height="100%"
      spacing={0.5}
      width="100%"
    >
      <Typography
        variant="s1"
        fontWeight="fontWeightRegular"
        color="text.primary"
      >
        {name}
      </Typography>

      <Typography
        variant="s3"
        fontWeight={"fontWeightRegular"}
        sx={{ color: "text.disabled" }}
      >
        {formattedUpdatedDate}
      </Typography>
    </Stack>
  );
};

ProjectDateModifiedCellRenderer.propTypes = {
  data: PropTypes.object,
};

const _LoadingSkeleton = () => {
  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",

        alignItems: "center",
      }}
    >
      <Skeleton
        variant="rectangular"
        width="80%"
        sx={{
          borderRadius: "4px",
        }}
        height={15}
      />
    </Box>
  );
};

export const getProjectColumnDefs = () => {
  return [
    {
      headerName: "Project Name",
      field: "name",
      flex: 3,
      sortable: true,
      cellRenderer: ProjectWithDate,
    },
    {
      headerName: "Issues",
      field: "issues",
      flex: 0.75,
      sortable: true,
      cellRenderer: (params) => formatNumberWithCommas(params.value),
    },
    {
      headerName: "Last 30 days volume",
      field: "last_30_days_vol",
      flex: 1.25,
      sortable: true,
      cellRenderer: SparklineCell,
    },
    {
      headerName: "Date Modified",
      field: "updated_at",
      flex: 1,
      sortable: true,
      cellRenderer: ProjectDateModifiedCellRenderer,
    },
  ];
};
