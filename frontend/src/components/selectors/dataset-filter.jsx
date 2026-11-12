import {
  Box,
  Button,
  FormControl,
  Grid,
  InputLabel,
  MenuItem,
  Select,
  Typography,
} from "@mui/material";
import Section from "src/components/section";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import DatasetSelector from "./dataset-selector";
import { FilterButton } from "src/components/filter";

// Filterable event dimensions of a dataset: its features, predictions, actuals
// and tags, grouped the way the picker lists them.
const EVENT_FILTER_GROUPS = [
  { key: "feature", label: "Feature", sources: ["categorical", "numerical"] },
  { key: "prediction", label: "Prediction", sources: ["predictionLabel"] },
  { key: "actual", label: "Actual", sources: ["actualLabel"] },
  { key: "tag", label: "Tag", sources: ["tags"] },
];

const buildEventFilterFields = (datasets) =>
  EVENT_FILTER_GROUPS.flatMap(({ key, sources }) =>
    sources.flatMap((source) =>
      (datasets?.[source] || []).map(({ name, dataType }) => ({
        id: `${key}::${name}`,
        label: name,
        type: dataType === "number" ? "number" : "text",
        category: key,
      })),
    ),
  );

const EVENT_FILTER_CATEGORY_LABELS = Object.fromEntries(
  EVENT_FILTER_GROUPS.map(({ key, label }) => [key, label]),
);

export default function DatasetFilter({
  showExport,
  datasets,
  showComparison = false,
  isBaseline = false,
}) {
  const [showComparisonDataset, setShowComparisonDataset] =
    useState(showComparison);
  const [primaryDatasetSelection, setPrimaryDatasetSelection] = useState(
    datasets?.recentProduction,
  );
  const [comparisonDatasetSelection, setComparisonDatasetSelection] = useState(
    !isBaseline ? datasets?.recentProduction : datasets?.baselineDataset,
  );
  const [metricConfig, setMetricConfig] = useState(
    datasets?.modelPerformanceConfig,
  );
  const [filters, setFilters] = useState([]);
  const filterFields = useMemo(
    () => buildEventFilterFields(datasets),
    [datasets],
  );

  function toggleShowComparison() {
    setShowComparisonDataset((prevValue) => !prevValue);
  }

  useEffect(() => {
    if (datasets?.recentProduction) {
      setPrimaryDatasetSelection(datasets.recentProduction);
      setComparisonDatasetSelection(datasets.recentProduction);
    }

    if (datasets?.modelPerformanceConfig) {
      setMetricConfig(datasets.modelPerformanceConfig);
    }
  }, [datasets]);

  function onPrimaryDatasetChange(datasetSelection) {
    setPrimaryDatasetSelection(datasetSelection);
  }

  function onComparisonDatasetChange(datasetSelection) {
    setComparisonDatasetSelection(datasetSelection);
  }

  function onFilterChange(rows) {
    setFilters(rows || []);
  }

  const valueFilters = !isBaseline && (
    <>
      <Grid container sx={{ mt: 1 }}>
        <Grid item>
          <FormControl size="small" sx={{ minWidth: 120 }}>
            <InputLabel id="metric-selector">Metric</InputLabel>
            <Select
              labelId="metric-selector-label"
              id="metric-selector"
              value={metricConfig?.value ? metricConfig?.value : "-"}
              label="Metric"
              // onChange={handleChange}
            >
              {datasets?.performanceMetrics?.map((row, index) => (
                <MenuItem key={index} value={row?.value}>
                  {row?.name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          {metricConfig?.requiresPositiveClass && (
            <FormControl size="small" sx={{ minWidth: 120 }}>
              <InputLabel id="positive-small-selector">
                Positive Class
              </InputLabel>
              <Select
                labelId="positive-small-label"
                id="positive-small"
                value={
                  metricConfig?.positiveClass
                    ? metricConfig?.positiveClass
                    : "-"
                }
                label="Positive Class"
                // onChange={handleChange}
              >
                {datasets?.classes?.map((row, index) => (
                  <MenuItem key={index} value={row}>
                    {row}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
          <FilterButton
            fields={filterFields}
            value={filters}
            onChange={onFilterChange}
            categoryLabels={EVENT_FILTER_CATEGORY_LABELS}
          />
        </Grid>
      </Grid>
    </>
  );

  return (
    <>
      <Section variant="panel">
        <Box sx={{ my: 2, mx: 2 }}>
          <Grid container>
            <Grid
              item
              xs
              sx={{
                alignItems: "center",
              }}
            >
              <div style={{ display: "flex" }}>
                <DatasetSelector
                  key={"primary"}
                  datasets={datasets}
                  datasetType={"primary"}
                  onDatasetSelectionChange={onPrimaryDatasetChange}
                  datasetSelection={primaryDatasetSelection}
                ></DatasetSelector>
                {!showComparisonDataset && (
                  <Button variant="outlined" onClick={toggleShowComparison}>
                    Add Comparison
                  </Button>
                )}
                {showComparisonDataset && (
                  <DatasetSelector
                    datasetType={"comparison"}
                    datasets={datasets}
                    onDatasetSelectionChange={onComparisonDatasetChange}
                    datasetSelection={comparisonDatasetSelection}
                    disabled={isBaseline}
                  ></DatasetSelector>
                )}
                {showComparisonDataset && (
                  <Button variant="outlined" onClick={toggleShowComparison}>
                    X
                  </Button>
                )}
              </div>
            </Grid>
            <Grid item>
              {showExport && (
                <Typography gutterBottom variant="h6">
                  <Button variant="outlined">Export</Button>
                </Typography>
              )}
            </Grid>
          </Grid>

          {valueFilters}
        </Box>
      </Section>
    </>
  );
}

DatasetFilter.propTypes = {
  showExport: PropTypes.bool,
  datasets: PropTypes.object,
  showComparison: PropTypes.bool,
  isBaseline: PropTypes.bool,
};
