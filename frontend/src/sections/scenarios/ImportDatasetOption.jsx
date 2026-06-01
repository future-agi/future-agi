import { Box, FormHelperText } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useWatch } from "react-hook-form";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { MIN_DATASET_ROWS } from "./common";

const ImportDatasetOption = ({ control }) => {
  const { data } = useDevelopDatasetList();

  const datasetList = useMemo(() => {
    return data?.map((dataset) => ({
      label: dataset.name,
      value: dataset.datasetId,
      rowCount: dataset.rowCount ?? dataset.row_count, // tolerate either casing
    }));
  }, [data]);

  const selectedDatasetId = useWatch({ control, name: "config.datasetId" });
  const selected = useMemo(
    () => datasetList?.find((d) => d.value === selectedDatasetId),
    [datasetList, selectedDatasetId],
  );
  const hasRowCount = selected && typeof selected.rowCount === "number";
  const isTooSmall = hasRowCount && selected.rowCount < MIN_DATASET_ROWS;

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        borderRadius: "4px",
        padding: 2,
        border: "1px solid",
        borderColor: "background.neutral",
      }}
    >
      <FormSearchSelectFieldControl
        control={control}
        fieldName="config.datasetId"
        label="Choose dataset"
        fullWidth
        placeholder="Select dataset"
        size="small"
        options={datasetList}
        required
      />
      {isTooSmall && (
        <FormHelperText error sx={{ mt: 1 }}>
          {`Selected dataset has only ${selected.rowCount} row${
            selected.rowCount === 1 ? "" : "s"
          }. A minimum of ${MIN_DATASET_ROWS} rows is required to create a scenario.`}
        </FormHelperText>
      )}
    </Box>
  );
};

ImportDatasetOption.propTypes = {
  control: PropTypes.object,
};

export default ImportDatasetOption;
