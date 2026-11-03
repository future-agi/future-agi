import { Box, Drawer, IconButton, Tab, Tabs, Typography } from "@mui/material";
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useInfiniteQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useScrollEnd } from "src/hooks/use-scroll-end";

import JobQueryItem from "./JobQueryItem";
import NoJobQuery from "./NoJobQuery";
import LoadingJobQueries from "./LoadingJobQueries";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const JobDetailDrawer = ({ show, onClose, selectedRow }) => {
  const covered = useDrawerCovered(show);

  return (
    <Drawer
      sx={coveredDrawerSx(covered)}
      anchor="right"
      open={show}
      onClose={onClose}
      BackdropProps={{ invisible: true }}
      PaperProps={{
        sx: {
          height: "85vh",
          width: "480px",
          position: "fixed",
          zIndex: 9999,
          top: "95px",
          right: 30,
          borderRadius: "8px",
          backgroundColor: "background.paper",
        },
      }}
    >
      <JobDetailContent
        show={show}
        onClose={onClose}
        selectedRow={selectedRow}
      />
    </Drawer>
  );
};

JobDetailDrawer.propTypes = {
  show: PropTypes.bool,
  onClose: PropTypes.func,
  selectedRow: PropTypes.object,
};

const JobDetailContent = ({ show, selectedRow, onClose }) => {
  const [selectedTab, setSelectedTab] = useState("all");

  const [tableRuns, setTableRuns] = useState(null);

  const handleChange = (event, newValue) => {
    setSelectedTab(newValue);
  };

  const statusQuery = selectedTab === "all" ? null : selectedTab;

  const {
    data: jobQueryData,
    isLoading,
    isFetchingNextPage,
    fetchNextPage,
  } = useInfiniteQuery({
    queryFn: (p) =>
      axios.post(
        endpoints.connections.getConnectionJobs,
        {
          connectionId: selectedRow.id,
        },
        { params: { status: statusQuery, limit: 10, page: p.pageParam } },
      ),
    queryKey: ["job-queries", statusQuery, selectedRow?.id],
    getNextPageParam: (o) => {
      return o?.data?.result?.next ? o?.data?.result?.current_page + 1 : null;
    },
    initialPageParam: 1,
    enabled: show,
  });

  const jobQueries = jobQueryData?.pages?.reduce(
    (acc, curr) => [...acc, ...curr.data.result.results],
    [],
  );

  useEffect(() => {
    if (
      statusQuery === null &&
      jobQueryData?.pages?.length &&
      jobQueryData?.pages?.[0]?.data?.result?.count
    ) {
      setTableRuns(jobQueryData?.pages[0]?.data?.result?.count);
    }
  }, [jobQueryData, statusQuery]);

  const scrollContainerRef = useScrollEnd(() => {
    if (isFetchingNextPage || isLoading) {
      return;
    }
    fetchNextPage();
  }, [isFetchingNextPage, isLoading]);

  const showTableName =
    selectedRow?.sourceConfig?.definitionsName === "BigQuery";

  return (
    <Box sx={{ padding: 2 }}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          position: "absolute",
          right: 16,
          top: 16,
        }}
      >
        <IconButton onClick={() => onClose()}>
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </Box>
      <Box sx={{ gap: "14px", display: "flex", flexDirection: "column" }}>
        <DetailInfo
          label="Model Name:"
          value={selectedRow?.aiModel?.userModelId}
        />
        <DetailInfo
          label="Model Type:"
          value={selectedRow?.aiModel?.modelType}
        />
        {showTableName ? (
          <DetailInfo
            label="Table Name:"
            value={selectedRow?.sourceConfig?.tableId}
          />
        ) : null}
        {showTableName ? (
          <DetailInfo label="Table Runs:" value={tableRuns} />
        ) : null}
      </Box>
      <Tabs
        sx={{ paddingTop: "28px" }}
        value={selectedTab}
        onChange={handleChange}
        aria-label="basic tabs example"
        textColor="primary"
      >
        <Tab label="All queries" value="all" />
        <Tab label="Failed" value="failed" />
        <Tab label="Pending" value="pending" />
        <Tab label="Successful" value="success" />
      </Tabs>
      <Box
        ref={scrollContainerRef}
        sx={{ overflow: "auto", height: "calc(85vh - 234px)" }}
      >
        {jobQueries?.length === 0 && <NoJobQuery />}

        {isLoading ? (
          <LoadingJobQueries />
        ) : (
          jobQueries?.map((jobQuery) => (
            <JobQueryItem key={jobQuery.id} jobQuery={jobQuery} />
          ))
        )}
      </Box>
    </Box>
  );
};

JobDetailContent.propTypes = {
  show: PropTypes.bool,
  selectedRow: PropTypes.object,
  onClose: PropTypes.func,
};

const DetailInfo = ({ label, value }) => {
  return (
    <Box sx={{ display: "flex" }}>
      <Typography
        component="span"
        fontSize="13px"
        fontWeight={600}
        color="text.disabled"
        sx={{ minWidth: "130px" }}
      >
        {label}
      </Typography>
      <Typography color="text.primary" fontSize={13}>
        {value}
      </Typography>
    </Box>
  );
};

DetailInfo.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
};

export default JobDetailDrawer;
