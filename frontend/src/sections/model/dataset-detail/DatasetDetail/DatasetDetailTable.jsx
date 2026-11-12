import StatusBadge from "src/components/status-badge/StatusBadge";
import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import SecondaryCheckbox from "src/components/secondary-checkbox/SecondaryCheckbox";
import { format } from "date-fns";
import ModelInputOutputCell from "../../ModelInputOutputCell";
import CircularProgressWithLabel from "src/components/circular-progress-with-label/CircularProgressWithLabel";
import {
  getScorePercentage,
  interpolateColorBasedOnScore,
} from "src/utils/utils";
import { useScrollEnd } from "src/hooks/use-scroll-end";

const DatasetDetailTable = ({
  datasetPointData,
  fetchNextPage,
  isLoading,
  isFetchingNextPage,
  columns,
  datasetSelectMode,
  selectedDataPoints,
  setSelectedDataPoints,
  allSelectedDataPoints,
  setAllSelectedDataPoints,
  totalCount,
  setViewDataPoint,
  setSelectedImages,
}) => {
  const enabledColumns = columns?.filter((col) => col?.enabled);

  const scrollContainerRef = useScrollEnd(() => {
    if (isFetchingNextPage || isLoading) return;
    fetchNextPage();
  }, [isFetchingNextPage, isLoading]);

  const onImageClick = (row, curUrl) => {
    const images = [];
    let defaultIdx = 0;
    row.input
      ?.filter((o) => o["url"] !== undefined)
      ?.forEach((url) => {
        if (curUrl === url.url) defaultIdx = images.length;
        images.push({ src: url.url });
      });
    row.output
      ?.filter((o) => o["url"] !== undefined)
      ?.forEach((url) => {
        if (curUrl === url.url) defaultIdx = images.length;
        images.push({ src: url.url });
      });
    setSelectedImages({ images, defaultIdx });
  };

  const renderCell = (col, row) => {
    const value = col.value;

    if (value === "input") {
      return (
        <ModelInputOutputCell
          content={row.input}
          contentType={row.inputType}
          onImageClick={(url) => onImageClick(row, url)}
        />
      );
    }
    if (value === "output") {
      return (
        <ModelInputOutputCell
          content={row.output}
          contentType={row.outputType}
          onImageClick={(url) => onImageClick(row, url)}
        />
      );
    }
    if (value === "dateCreated") {
      return (
        <TableCell>{format(new Date(row?.[value]), "yyyy-MM-dd")}</TableCell>
      );
    }

    const score = row?.[value];

    return (
      <TableCell
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
      </TableCell>
    );
  };

  return (
    <Box sx={{ height: "calc(100vh - 140px)", width: "100%" }}>
      <TableContainer
        style={{ width: "100%", height: "100%", overflow: "auto" }}
        ref={scrollContainerRef}
      >
        <Table stickyHeader>
          <TableHead>
            <TableRow>
              {datasetSelectMode && (
                <TableCell sx={{ width: "20px" }}>
                  <SecondaryCheckbox
                    onClick={(e) => e.stopPropagation()}
                    checked={
                      (allSelectedDataPoints ||
                        allSelectedDataPoints?.length === totalCount) &&
                      totalCount !== 0
                    }
                    onChange={(e, checked) => {
                      e.stopPropagation();
                      setSelectedDataPoints([]);
                      setAllSelectedDataPoints(checked);
                    }}
                    sx={{ padding: 0 }}
                  />
                </TableCell>
              )}
              {enabledColumns?.map(({ label, value }) => (
                <TableCell key={value}>{label}</TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {datasetPointData?.map((row) => {
              const isSelected = Boolean(
                allSelectedDataPoints ||
                  selectedDataPoints.find((d) => d === row.id),
              );

              return (
                <TableRow
                  key={row.id}
                  onClick={() => setViewDataPoint(row)}
                  sx={{
                    "&:hover": {
                      cursor: "pointer",
                      backgroundColor: "action.hover",
                    },
                  }}
                >
                  {datasetSelectMode && (
                    <TableCell>
                      <SecondaryCheckbox
                        checked={isSelected}
                        sx={{ padding: 0 }}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e, checked) => {
                          e.stopPropagation();
                          if (checked) {
                            setSelectedDataPoints((p) => [...p, row.id]);
                          } else {
                            if (allSelectedDataPoints) {
                              setAllSelectedDataPoints(false);
                              setSelectedDataPoints(
                                datasetPointData?.reduce((acc, curr) => {
                                  if (curr.id !== row.id) {
                                    acc.push(curr.id);
                                  }
                                  return acc;
                                }, []),
                              );
                            } else {
                              setSelectedDataPoints((prevState) =>
                                prevState.filter((d) => d !== row.id),
                              );
                            }
                          }
                        }}
                      />
                    </TableCell>
                  )}

                  {enabledColumns?.map((col) => renderCell(col, row))}
                </TableRow>
              );
            })}
            {isFetchingNextPage && (
              <TableRow>
                <TableCell colSpan={6} sx={{ textAlign: "center" }}>
                  <CircularProgress size={20} />
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
};

DatasetDetailTable.propTypes = {
  datasetPointData: PropTypes.array,
  fetchNextPage: PropTypes.func,
  isLoading: PropTypes.bool,
  isFetchingNextPage: PropTypes.bool,
  columns: PropTypes.array,

  datasetSelectMode: PropTypes.any,
  selectedDataPoints: PropTypes.array,
  setSelectedDataPoints: PropTypes.func,
  allSelectedDataPoints: PropTypes.bool,
  setAllSelectedDataPoints: PropTypes.func,
  totalCount: PropTypes.number,
  setViewDataPoint: PropTypes.func,

  setSelectedImages: PropTypes.func,
};

export default DatasetDetailTable;
