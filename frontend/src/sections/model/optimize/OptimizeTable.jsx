import StatusBadge from "src/components/status-badge/StatusBadge";
import React, { useState } from "react";
import PropTypes from "prop-types";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import { useParams } from "src/routes/hooks";
import {
  Box,
  Button,
  CircularProgress,
  Table,
  TableBody,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import { format } from "date-fns";
import Iconify from "src/components/iconify";
import { EnvironmentNumberMapper } from "src/utils/constant";
import { useNavigate } from "react-router";
import CustomTableCell from "src/components/table/custom-table-cell";

import OptimizeRunningModel from "./OptimizeRunningModel";

const OptimizeTable = ({
  optimizeData,
  fetchNextPage,
  isLoading,
  isFetchingNextPage,
  columns,
  onViewClick,
}) => {
  const scrollContainerRef = useScrollEnd(() => {
    if (isFetchingNextPage || isLoading) {
      return;
    }
    fetchNextPage();
  }, [isFetchingNextPage, isLoading]);

  const enabledColumns = columns?.filter((col) => col?.enabled);

  const { id } = useParams();

  const navigate = useNavigate();

  const [isWarningOpen, setIsWarningOpen] = useState(false);

  const renderCol = (row, value) => {
    if (value === "createdAt") {
      return (
        <CustomTableCell>
          {format(new Date(row?.[value]), "yyyy-MM-dd")}
        </CustomTableCell>
      );
    }

    if (value === "status") {
      return row?.[value] === "Completed" ? (
        <CustomTableCell>
          <StatusBadge status="Completed" role="success" />
        </CustomTableCell>
      ) : (
        <CustomTableCell>
          <StatusBadge status="Running" role="warning" />
        </CustomTableCell>
      );
    }

    if (value === "environment") {
      return (
        <CustomTableCell>
          {EnvironmentNumberMapper[row?.[value]]}
        </CustomTableCell>
      );
    }

    return <CustomTableCell>{row?.[value]}</CustomTableCell>;
  };

  return (
    <>
      <OptimizeRunningModel
        open={isWarningOpen}
        onClose={() => setIsWarningOpen(false)}
      />
      <Box sx={{ height: "calc(100vh - 190px)", width: "100%" }}>
        <TableContainer
          style={{ width: "100%", height: "100%", overflow: "auto" }}
          ref={scrollContainerRef}
        >
          <Table stickyHeader>
            <TableHead>
              <TableRow>
                {enabledColumns?.map(({ label, value }) => (
                  <CustomTableCell key={value}>{label}</CustomTableCell>
                ))}
                <CustomTableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {optimizeData?.map((row) => {
                return (
                  <TableRow
                    key={row.id}
                    onClick={() => {
                      if (
                        row?.status === "Running" &&
                        row?.optimizeType === "PromptTemplate"
                      ) {
                        return setIsWarningOpen(true);
                      }
                      navigate(`/dashboard/models/${id}/optimize/${row.id}`, {
                        state: { pathLabel: row?.name },
                      });
                      // trackEvent(Events.optimizeDetailPageExplore, {
                      //   "Optimization Name": row?.name,
                      //   "Optimization Id": row?.id,
                      //   "Optimization Type": row?.optimizeType,
                      // });
                    }}
                    sx={{
                      "&:hover": {
                        cursor: "pointer",
                        backgroundColor: "action.hover",
                      },
                    }}
                  >
                    {enabledColumns?.map(({ value }) => renderCol(row, value))}
                    <CustomTableCell>
                      <Button
                        className="row-hover-action"
                        onClick={(e) => {
                          e.stopPropagation();
                          onViewClick(row);
                          // trackEvent(Events.optimizePageViewOptimizationInfo, {
                          //   "Optimization Name": row?.name,
                          //   "Optimization Id": row?.id,
                          // });
                        }}
                        startIcon={
                          <Iconify icon="solar:info-circle-bold" width="16px" />
                        }
                        variant="outlined"
                        size="small"
                        color="primary"
                      >
                        Info
                      </Button>
                    </CustomTableCell>
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
    </>
  );
};

export default OptimizeTable;

OptimizeTable.propTypes = {
  optimizeData: PropTypes.array,
  fetchNextPage: PropTypes.func,
  isLoading: PropTypes.bool,
  isFetchingNextPage: PropTypes.bool,
  columns: PropTypes.array,
  onViewClick: PropTypes.func,
};
