import StatusBadge from "src/components/status-badge/StatusBadge";
import {
  Box,
  CircularProgress,
  Table,
  TableBody,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import CircularProgressWithLabel from "src/components/circular-progress-with-label/CircularProgressWithLabel";
import {
  getScorePercentage,
  interpolateColorBasedOnScore,
} from "src/utils/utils";
import { format } from "date-fns";
import ShortString from "src/components/ShortString/ShortString";
import CustomTableCell from "src/components/table/custom-table-cell";

const PromptTemplateExploreTable = ({
  datasetPointData,
  fetchNextPage,
  isLoading,
  isFetchingNextPage,
  columns,
  setSelectedRow,
}) => {
  const enabledColumns = columns?.filter((col) => col?.enabled);

  const scrollContainerRef = useScrollEnd(() => {
    if (isFetchingNextPage || isLoading) {
      return;
    }
    fetchNextPage();
  }, [isFetchingNextPage, isLoading]);

  const renderCell = (col, row) => {
    const value = col.value;

    if (value === "input" || value === "output" || value === "rightAnswer") {
      return (
        <CustomTableCell>
          <ShortString sx={{ minWidth: "200px" }} maxLength={70}>
            {row?.[value]}
          </ShortString>
        </CustomTableCell>
      );
    }

    if (value === "dateCreated") {
      return (
        <CustomTableCell>
          {format(new Date(row?.[value]), "yyyy-MM-dd")}
        </CustomTableCell>
      );
    }

    const score = row?.[value];

    return (
      <CustomTableCell
        sx={{
          padding: "8px",
          fontSize: "12px",
        }}
      >
        <Box sx={{ display: "flex", justifyContent: "center" }}>
          {score ? (
            <CircularProgressWithLabel
              color={interpolateColorBasedOnScore(score)}
              value={getScorePercentage(score)}
            />
          ) : (
            <StatusBadge status="Processing" role="warning" />
          )}
        </Box>
      </CustomTableCell>
    );
  };

  return (
    <Box sx={{ height: "calc(100vh - 250px)", width: "100%" }}>
      <TableContainer
        style={{ width: "100%", height: "100%", overflow: "auto" }}
        ref={scrollContainerRef}
      >
        <Table stickyHeader>
          <TableHead>
            <TableRow>
              {enabledColumns?.map(({ label, value }) => (
                <CustomTableCell sx={{ minWidth: "140px" }} key={value}>
                  {label}
                </CustomTableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {datasetPointData?.map((row) => {
              return (
                <TableRow
                  key={row.id}
                  sx={{
                    "&:hover": {
                      cursor: "pointer",
                      backgroundColor: "action.hover",
                    },
                  }}
                  onClick={() => setSelectedRow(row)}
                >
                  {enabledColumns?.map((col) => renderCell(col, row))}
                </TableRow>
              );
            })}
            {isFetchingNextPage ? (
              <TableRow>
                <CustomTableCell colSpan={6} sx={{ textAlign: "center" }}>
                  <CircularProgress size={20} />
                </CustomTableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
};

PromptTemplateExploreTable.propTypes = {
  datasetPointData: PropTypes.array,
  fetchNextPage: PropTypes.func,
  isLoading: PropTypes.bool,
  isFetchingNextPage: PropTypes.bool,
  columns: PropTypes.array,
  setSelectedRow: PropTypes.func,
};

export default PromptTemplateExploreTable;
