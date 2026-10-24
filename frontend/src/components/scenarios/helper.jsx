/* eslint-disable no-restricted-syntax -- ds: data colours (category encodings) */
import React from "react";
import ScenarioActionMenu from "./ScenariosActionMenu";
import ScenarioNameCellRenderer from "./CustomCellRenderers/ScenarioNameCellRenderer";
import ChipCellRenderer from "./CustomCellRenderers/ChipCellRenderer";
import { Box, Stack, Typography } from "@mui/material";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { format, isValid } from "date-fns";

export const getScenarioTypeColor = (type) => {
  switch (type?.toLowerCase()) {
    case "graph":
      return "#10B981"; // green
    case "dataset":
      return "#3B82F6"; // blue
    case "script":
      return "#F59E0B"; // orange
    default:
      return "#6B7280"; // gray
  }
};

export const getScenarioColumnDefs = (onEdit, onDelete, role) => [
  {
    headerName: "Name",
    field: "name",
    flex: 2, // takes majority of the space
    minWidth: 250,
    cellRenderer: ScenarioNameCellRenderer,
  },
  {
    headerName: "Agent Type",
    field: "agent_type",
    flex: 1,
    cellRenderer: ChipCellRenderer,
  },
  {
    headerName: "No of Datapoints",
    field: "dataset_rows",
    width: 140, // fixed width
    cellRenderer: ({ value }) => (
      <Box height={"100%"} display={"flex"} alignItems={"center"}>
        {value}
      </Box>
    ),
  },
  {
    headerName: "Created At",
    field: "created_at",
    width: 200,
    cellRenderer: ({ value }) => (
      <Stack
        sx={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "flex-start",
        }}
        direction="column"
        gap={0.5}
      >
        <Typography
          typography={"s1"}
          fontWeight={"fontWeightRegular"}
          color={"text.primary"}
        >
          {isValid(new Date(value))
            ? format(new Date(value), "dd-MM-yyyy")
            : "-"}
        </Typography>
        <Typography
          typography={"s3"}
          fontWeight={"fontWeightRegular"}
          color={"text.disabled"}
        >
          {isValid(new Date(value)) ? format(new Date(value), "hh:mm:ss") : "-"}
        </Typography>
      </Stack>
    ),
  },
  {
    headerName: "Scenario Type",
    field: "scenario_type",
    width: 200,
    cellRenderer: ChipCellRenderer,
  },
  ...(RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role] &&
  RolePermission.SIMULATION_AGENT[PERMISSIONS.DELETE][role]
    ? [
        {
          headerName: "Actions",
          field: "actions",
          width: 60,
          cellRenderer: (params) => (
            <Box height={"100%"} display={"flex"} alignItems={"center"}>
              <ScenarioActionMenu
                scenario={params.data}
                onEdit={onEdit}
                onDelete={onDelete}
              />
            </Box>
          ),
        },
      ]
    : []),
];
