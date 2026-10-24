import {
  Box,
  InputAdornment,
  LinearProgress,
  Tab,
  Tabs,
  TextField,
} from "@mui/material";
import React, { useState } from "react";
import PropTypes from "prop-types";
import Label from "src/components/label";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import Iconify from "src/components/iconify";

import JobListTable from "./JobListTable";
import JobDetailDrawer from "./JobDetailDrawer";
import DeleteJobModal from "./DeleteJobModal";

const JobStatusView = () => {
  const [searchQuery, setSearchQuery] = useState("");

  const [selectedTab, setSelectedTab] = useState("all");

  const [selectedRow, setSelectedRow] = useState(null);

  const [deleteConnectionId, setDeleteConnectionId] = useState(null);

  const [page, setPage] = useState(1);

  const [rowsPerPage, onRowsPerPageChange] = useState(10);

  const handleChange = (event, newValue) => {
    setSelectedTab(newValue);
  };

  const { isPending: isLoadingCount, data: countData } = useQuery({
    queryFn: () => axios.get(endpoints.connections.getConnectionCount),
    queryKey: ["connection-count"],
    select: (d) => d?.data?.result,
  });

  const status = selectedTab === "all" ? null : selectedTab;

  const { isPending: isLoadingTableData, data: tableData } = useQuery({
    queryFn: () =>
      axios.get(endpoints.connections.getConnectionList, {
        params: {
          status,
          search: searchQuery?.length ? searchQuery : null,
          page: page,
        },
      }),
    queryKey: ["connection-list", status, searchQuery, page],
    select: (d) => d?.data?.result,
  });

  if (isLoadingCount) {
    return <LinearProgress />;
  }

  return (
    <>
      <Box
        sx={{
          padding: 2,
          height: "100%",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Tabs
          sx={{ borderBottom: 1, borderColor: "divider" }}
          value={selectedTab}
          onChange={handleChange}
          aria-label="basic tabs example"
          textColor="primary"
        >
          <Tab
            label="All"
            value="all"
            icon={<Label variant="filled">{countData.all}</Label>}
            iconPosition="end"
          />
          <Tab
            label="Active"
            value="active"
            icon={
              <Label color="success" variant="soft">
                {countData.active}
              </Label>
            }
            iconPosition="end"
          />
          <Tab
            label="Inactive"
            value="inactive"
            icon={
              <Label color="error" variant="soft">
                {countData.inactive}
              </Label>
            }
            iconPosition="end"
          />
          <Tab
            label="Deleted"
            value="deleted"
            icon={<Label variant="soft">{countData.deleted}</Label>}
            iconPosition="end"
          />
        </Tabs>
        <Box>
          <TextField
            fullWidth
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            sx={{ flex: 1, paddingY: "20px" }}
            placeholder="Search by Model Name"
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon="solar:magnifier-linear"
                    width={16}
                    sx={{ color: "text.disabled" }}
                  />
                </InputAdornment>
              ),
            }}
          />
        </Box>

        <JobListTable
          tableData={tableData}
          selectedTab={selectedTab}
          setSelectedRow={setSelectedRow}
          setDeleteConnectionId={setDeleteConnectionId}
          page={page}
          onPageChange={setPage}
          rowsPerPage={rowsPerPage}
          onRowsPerPageChange={onRowsPerPageChange}
          isLoading={isLoadingTableData}
        />
        <JobDetailDrawer
          show={Boolean(selectedRow)}
          onClose={() => setSelectedRow(null)}
          selectedRow={selectedRow}
        />
        <DeleteJobModal
          open={Boolean(deleteConnectionId)}
          onClose={() => setDeleteConnectionId(null)}
          connectionId={deleteConnectionId}
        />
      </Box>
    </>
  );
};

function CustomTabPanel(props) {
  const { children, value, panelValue, loading, ...other } = props;

  return (
    <div
      style={{ height: "100%" }}
      hidden={panelValue !== value}
      role="tabpanel"
      {...other}
    >
      {loading ? <LinearProgress /> : null}
      {value === panelValue && !loading && children}
    </div>
  );
}

CustomTabPanel.propTypes = {
  children: PropTypes.node,
  panelValue: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  loading: PropTypes.bool,
};

export default JobStatusView;
