import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import { TablePaginationCustom } from "src/components/table";
import React from "react";
import PropTypes from "prop-types";

import JobTableItem from "./JobTableItem";
import JobListLoading from "./JobListLoading";
import NoJobList from "./NoJobList";

const JobListTable = ({
  tableData,
  selectedTab,
  setSelectedRow,
  setDeleteConnectionId,
  page,
  onPageChange,
  rowsPerPage,
  onRowsPerPageChange,
  isLoading,
}) => {
  const showJobActions = selectedTab !== "deleted";

  const renderNoData = () => {
    if (!isLoading && !tableData?.results?.length) {
      return <NoJobList />;
    }

    return <></>;
  };

  return (
    <Box
      sx={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "auto",
      }}
    >
      <TableContainer sx={{ flex: 1, overflow: "auto", marginBottom: "90px" }}>
        <Table stickyHeader sx={{ flex: 1, overflow: "auto" }}>
          <TableHead>
            <TableRow>
              <TableCell>Model Name</TableCell>
              <TableCell>Connected Source</TableCell>
              <TableCell>Created At</TableCell>
              {showJobActions ? (
                <TableCell>
                  <Box sx={{ display: "flex", justifyContent: "center" }}>
                    Job Actions
                  </Box>
                </TableCell>
              ) : null}
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading ? (
              <JobListLoading />
            ) : (
              tableData?.results?.map((row) => (
                <JobTableItem
                  onItemClick={() => {
                    if (row.deleted) {
                      return;
                    }
                    setSelectedRow(row);
                  }}
                  key={row.id}
                  row={row}
                  onDeleteClick={() => setDeleteConnectionId(row.id)}
                />
              ))
            )}
            {renderNoData()}
          </TableBody>
        </Table>
      </TableContainer>
      <TablePaginationCustom
        sx={{ position: "absolute", bottom: 0, left: 0, right: 0 }}
        rowsPerPageOptions={[10, 20, 30]}
        count={tableData?.count || 0}
        page={page - 1}
        onPageChange={(e, newPage) => onPageChange(newPage + 1)}
        rowsPerPage={rowsPerPage}
        onRowsPerPageChange={(e) => onRowsPerPageChange(e.target.value)}
      />
    </Box>
  );
};

JobListTable.propTypes = {
  tableData: PropTypes.object,
  selectedTab: PropTypes.string,
  setSelectedRow: PropTypes.func,
  setDeleteConnectionId: PropTypes.func,
  page: PropTypes.number,
  onPageChange: PropTypes.func,
  rowsPerPage: PropTypes.number,
  onRowsPerPageChange: PropTypes.func,
  isLoading: PropTypes.bool,
};

export default JobListTable;
