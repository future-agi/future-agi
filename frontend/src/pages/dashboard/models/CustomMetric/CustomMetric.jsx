import {
  Box,
  Button,
  CircularProgress,
  InputAdornment,
  LinearProgress,
  TextField,
} from "@mui/material";
import React, { useState } from "react";
import Iconify from "src/components/iconify/iconify";
import CustomMetricTable from "./CustomMetricTable";
import { CreateUpdateCustomMetric } from "./CreateUpdateCustomMetric";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router-dom";
import { CustomMetricNoData } from "./CustomMetricNoData";
import ConfigureDatasetModal from "../ConfigureDatasetModal";
import { useGetModelDetail } from "src/api/model/model";

const CustomMetric = () => {
  const { id } = useParams();

  const { data: modelDetails } = useGetModelDetail(id);

  // @ts-ignore
  const isMetricAdded = modelDetails?.isMetricAdded;
  // @ts-ignore
  const isDatasetAdded = modelDetails?.isDatasetAdded;

  const [showCreateMetric, setShowCreateMetric] = useState(
    isDatasetAdded && !isMetricAdded,
  );
  const [updateMetricData, setUpdateMetricData] = useState(null);

  const [isConfigureDatasetOpen, setIsConfigureDatasetOpen] =
    useState(!isDatasetAdded);

  const [searchQuery, setSearchQuery] = useState("");

  const [paginationModel, setPaginationModel] = useState({
    page: 0,
    pageSize: 10,
  });

  const [sortModel, setSortModel] = useState([
    {
      field: "name",
    },
  ]);

  const { isLoading, data } = useQuery({
    queryKey: [
      "customMetric",
      paginationModel.page,
      sortModel?.[0]?.sort,
      searchQuery,
      id,
    ],
    queryFn: () =>
      axios.get(`${endpoints.customMetric.list}${id}/`, {
        params: {
          page: paginationModel.page + 1,
          sort_order: sortModel?.[0]?.sort,
          search_query: searchQuery,
        },
      }),
    select: (d) => d.data,
  });

  return (
    <Box sx={{ height: "calc(100vh - 135px)" }}>
      <Box
        sx={{
          padding: 2.5,
          display: "flex",
          gap: 2,
          width: "100%",
        }}
      >
        <TextField
          value={searchQuery}
          onChange={(e) => {
            // trackEvent(Events.customMetricSearch, {
            //   "Search String": e.target.value,
            // });
            setSearchQuery(e.target.value);
          }}
          size="small"
          sx={{ flex: 1 }}
          placeholder="Search custom metrics"
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="eva:search-fill"
                  sx={{ color: "primary.main" }}
                />
              </InputAdornment>
            ),
            endAdornment: isLoading ? (
              <InputAdornment position="end">
                <CircularProgress size={20} color="primary" />
              </InputAdornment>
            ) : (
              <></>
            ),
          }}
        />
        <Button
          variant="contained"
          color="primary"
          onClick={() => {
            setShowCreateMetric(true);
            // trackEvent(Events.customMetricCreateStart);
          }}
          id="add-metric-button"
        >
          Create custom metric
        </Button>
      </Box>
      <ConfigureDatasetModal
        open={isConfigureDatasetOpen}
        onClose={() => setIsConfigureDatasetOpen(false)}
      />
      <CreateUpdateCustomMetric
        show={showCreateMetric}
        onClose={() => setShowCreateMetric(false)}
      />
      <CreateUpdateCustomMetric
        show={Boolean(updateMetricData)}
        onClose={() => setUpdateMetricData(null)}
        updateData={updateMetricData}
      />
      {isLoading && <LinearProgress />}
      {!isLoading && !data?.results?.length && <CustomMetricNoData />}
      {!isLoading && Boolean(data?.results?.length) && (
        <CustomMetricTable
          customMetricData={data}
          isLoading={isLoading}
          paginationModel={paginationModel}
          setPaginationModel={(v) => {
            // trackEvent(Events.customMetricPaginationClicked, {
            //   "Current Page Number": paginationModel?.page,
            //   "New Page Number": v?.page,
            // });
            setPaginationModel(v);
          }}
          sortModel={sortModel}
          setSortModel={setSortModel}
          setUpdateMetricData={setUpdateMetricData}
        />
      )}
    </Box>
  );
};

CustomMetric.propTypes = {};

export default CustomMetric;
