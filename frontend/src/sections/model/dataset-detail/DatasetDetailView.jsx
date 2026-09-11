import React from "react";
import { useParams, useSearchParams } from "src/routes/hooks";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import { Box, LinearProgress } from "@mui/material";
import PropTypes from "prop-types";

import DatasetOverview from "./DatasetDetail/DatasetOverview";
import DatasetDetail from "./DatasetDetail/DatasetDetail";

function CustomTabPanel(props) {
  const { children, value, panelValue, loading, ...other } = props;

  return (
    <div hidden={panelValue !== value} role="tabpanel" {...other}>
      {loading ? <LinearProgress /> : null}
      {value === panelValue && !loading && <Box>{children}</Box>}
    </div>
  );
}

CustomTabPanel.propTypes = {
  children: PropTypes.node,
  panelValue: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  loading: PropTypes.bool,
};

const DatasetDetailView = () => {
  const { id } = useParams();

  const [_v] = useSearchParams({ openTab: "datapoints" });
  const value = _v.openTab;

  const { isPending: isLoadingModelDetails } = useQuery({
    queryKey: ["model", id],
    queryFn: () => axios.get(endpoints.model.details(id)),
    select: (d) => d.data,
    staleTime: 1 * 60 * 1000, // 1 min stale time
  });

  return (
    <>
      <Box sx={{ width: "100%" }}>
        <CustomTabPanel
          value={value}
          panelValue="datapoints"
          loading={isLoadingModelDetails}
        >
          <DatasetDetail />
        </CustomTabPanel>
        <CustomTabPanel
          value={value}
          panelValue="overview"
          loading={isLoadingModelDetails}
        >
          <DatasetOverview />
        </CustomTabPanel>
      </Box>
    </>
  );
};

export default DatasetDetailView;
