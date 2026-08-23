import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import FormSearchSelectFieldState from "src/components/FromSearchSelectField/FormSearchSelectFieldState";
import { useDatasetsList } from "src/sections/develop/hooks/useDatasetsList";
import HelperText from "src/sections/develop-detail/Common/HelperText";

// Alternative source to file uploads: build the KB from an existing
// dataset's rows, indexing only the chosen columns as content.
const SelectDatasetSource = ({
  datasetId,
  setDatasetId,
  columnIds,
  setColumnIds,
  disabled,
}) => {
  const { data: datasetsData, isLoading: datasetsLoading } = useDatasetsList({
    pageSize: 100,
  });

  const { data: columns, isLoading: columnsLoading } = useQuery({
    queryKey: ["kb-dataset-columns", datasetId],
    enabled: Boolean(datasetId),
    queryFn: () =>
      axios
        .get(endpoints.develop.getDatasetColumns(datasetId))
        .then((res) => res.data),
    select: (data) => data?.result?.columns ?? [],
  });

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box>
        <FormSearchSelectFieldState
          fullWidth
          label="Dataset"
          placeholder={datasetsLoading ? "Loading datasets..." : "Select dataset"}
          disabled={disabled || datasetsLoading}
          value={datasetId}
          onChange={(e) => {
            setDatasetId(e.target.value);
            setColumnIds([]);
          }}
          options={(datasetsData?.items ?? []).map((dataset) => ({
            label: dataset.name,
            value: dataset.id,
          }))}
          size="small"
        />
        <HelperText text="Rows from this dataset will be indexed into the knowledge base." />
      </Box>
      <Box>
        <FormSearchSelectFieldState
          fullWidth
          multiple
          label="Columns"
          placeholder={
            !datasetId
              ? "Select a dataset first"
              : columnsLoading
                ? "Loading columns..."
                : "Select columns"
          }
          disabled={disabled || !datasetId || columnsLoading}
          value={columnIds}
          onChange={(e) => setColumnIds(e.target.value)}
          options={(columns ?? []).map((column) => ({
            label: column.name,
            value: column.id,
          }))}
          size="small"
        />
        <HelperText text="Values from the selected columns are concatenated into each document." />
      </Box>
      {datasetId && !columnsLoading && columns?.length === 0 && (
        <Typography typography="s2" color="text.disabled">
          This dataset has no columns to index.
        </Typography>
      )}
    </Box>
  );
};

export default SelectDatasetSource;

SelectDatasetSource.propTypes = {
  datasetId: PropTypes.string,
  setDatasetId: PropTypes.func,
  columnIds: PropTypes.array,
  setColumnIds: PropTypes.func,
  disabled: PropTypes.bool,
};
