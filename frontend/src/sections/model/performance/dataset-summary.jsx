import {
  Box,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  alpha,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useState } from "react";
import { useGetDatasetDetails } from "src/api/model/dataset";
import Label from "src/components/label";

const FIELDS_OPTIONS = [
  { value: "all", label: "All", showCount: true },
  {
    value: "categoricalFeatures",
    label: "Categorical Features",
    showCount: true,
  },
  { value: "numericFeatures", label: "Nemeric Features", showCount: true },
  { value: "embeddings", label: "Embeddings", showCount: true },
  { value: "tags", label: "Tags", showCount: true },
  { value: "predictions", label: "Predictions", showCount: false },
  { value: "actuals", label: "Actuals", showCount: false },
];

const CAT_HEADERS = [{ id: "cardinality", label: "Cardinality" }];

const NUM_HEADERS = [
  { id: "min", label: "Min" },
  { id: "Max", label: "Max" },
  { id: "avg", label: "Avg" },
  { id: "std", label: "Std Dev" },
];

const ALL_HEADERS = [
  { id: "name", label: "Name" },
  { id: "dataType", label: "Data Type" },
  { id: "empty", label: "% Empty" },
  ...CAT_HEADERS,
  ...NUM_HEADERS,
];

const CAT_ALL_HEADERS = [
  { id: "name", label: "Name" },
  { id: "empty", label: "% Empty" },
  ...CAT_HEADERS,
];

const NUM_ALL_HEADERS = [
  { id: "name", label: "Name" },
  { id: "empty", label: "% Empty" },
  ...NUM_HEADERS,
];

const EMBED_HEADERS = [
  { id: "name", label: "Name" },
  { id: "empty", label: "% Empty" },
];

const defaultFilters = {
  status: "all",
};

export default function DatasetSummary({ model, dataset }) {
  const [filters, setFilters] = useState(defaultFilters);

  const { datasetDetails } = useGetDatasetDetails(model.id, {
    version: dataset?.modelVersion,
    environment: dataset?.environment,
    batchId: dataset?.batchId,
  });

  const handleFilters = useCallback((name, value) => {
    setFilters((prevState) => ({
      ...prevState,
      [name]: value,
    }));
  }, []);

  const handleFilterStatus = useCallback(
    (event, newValue) => {
      handleFilters("status", newValue);
    },
    [handleFilters],
  );

  return (
    <>
      <Box
        sx={{
          px: 2.5,
          boxShadow: (theme) =>
            `inset 0 -2px 0 0 ${alpha(theme.palette.text.disabled, 0.08)}`,
        }}
      >
        <Tabs value={filters.status} onChange={handleFilterStatus}>
          {FIELDS_OPTIONS.map((tab) => (
            <Tab
              key={tab.value}
              iconPosition="end"
              value={tab.value}
              label={tab.label}
              icon={
                tab.showCount ? (
                  <Label
                    variant={
                      ((tab.value === "all" || tab.value === filters.status) &&
                        "filled") ||
                      "soft"
                    }
                    color={"default"}
                  >
                    {tab.value === "all" &&
                      datasetDetails?.predictionLabel.length +
                        datasetDetails?.categorical.length +
                        datasetDetails?.numerical.length +
                        datasetDetails?.vector.length +
                        datasetDetails?.tags.length}
                    {tab.value === "categoricalFeatures" &&
                      datasetDetails?.categorical.length}
                    {tab.value === "numericFeatures" &&
                      datasetDetails?.numerical.length}
                    {tab.value === "embeddings" &&
                      datasetDetails?.vector.length}
                    {tab.value === "tags" && datasetDetails?.tags.length}
                    {tab.value === "predictions"}
                    {tab.value === "actuals"}
                  </Label>
                ) : null
              }
            />
          ))}
        </Tabs>
      </Box>
      {(filters.status === "all" || filters.status === "predictions") && (
        <TableContainer sx={{ position: "relative", overflow: "unset" }}>
          <Table>
            <TableHead>
              <TableRow>
                {ALL_HEADERS.map((row) => (
                  <TableCell key={row.id}>{row.label}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {datasetDetails?.predictionLabel?.map((predLabel, index) => (
                <TableRow key={index}>
                  <TableCell>{predLabel.name}</TableCell>
                  <TableCell>{predLabel.dataType}</TableCell>
                  <TableCell>{predLabel.empty}</TableCell>
                  <TableCell>{predLabel.cardinality}</TableCell>
                  <TableCell>{predLabel.min}</TableCell>
                  <TableCell>{predLabel.max}</TableCell>
                  <TableCell>{predLabel.avg}</TableCell>
                  <TableCell>{predLabel.std}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {(filters.status === "all" || filters.status === "actuals") && (
        <TableContainer sx={{ position: "relative", overflow: "unset" }}>
          <Table>
            <TableHead>
              <TableRow>
                {ALL_HEADERS.map((row) => (
                  <TableCell key={row.id}>{row.label}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {datasetDetails?.actualLabel?.map((aLabel, index) => (
                <TableRow key={index}>
                  <TableCell>{aLabel.name}</TableCell>
                  <TableCell>{aLabel.dataType}</TableCell>
                  <TableCell>{aLabel.empty}</TableCell>
                  <TableCell>{aLabel.cardinality}</TableCell>
                  <TableCell>{aLabel.min}</TableCell>
                  <TableCell>{aLabel.max}</TableCell>
                  <TableCell>{aLabel.avg}</TableCell>
                  <TableCell>{aLabel.std}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {(filters.status === "all" ||
        filters.status === "categoricalFeatures") && (
        <TableContainer sx={{ position: "relative", overflow: "unset" }}>
          <Table>
            <TableHead>
              <TableRow>
                {CAT_ALL_HEADERS.map((row) => (
                  <TableCell key={row.id}>{row.label}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {datasetDetails?.categorical?.map((categoricalLabel, index) => (
                <TableRow key={index}>
                  <TableCell>{categoricalLabel.name}</TableCell>
                  <TableCell>{categoricalLabel.empty}</TableCell>
                  <TableCell>{categoricalLabel.cardinality}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {(filters.status === "all" || filters.status === "numericFeatures") && (
        <TableContainer sx={{ position: "relative", overflow: "unset" }}>
          <Table>
            <TableHead>
              <TableRow>
                {NUM_ALL_HEADERS.map((row) => (
                  <TableCell key={row.id}>{row.label}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {datasetDetails?.numerical?.map((predLabel, index) => (
                <TableRow key={index}>
                  <TableCell>{predLabel.name}</TableCell>
                  <TableCell>{predLabel.empty}</TableCell>
                  <TableCell>{predLabel.min}</TableCell>
                  <TableCell>{predLabel.max}</TableCell>
                  <TableCell>{predLabel.avg}</TableCell>
                  <TableCell>{predLabel.std}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {(filters.status === "all" || filters.status === "embeddings") && (
        <TableContainer sx={{ position: "relative", overflow: "unset" }}>
          <Table>
            <TableHead>
              <TableRow>
                {EMBED_HEADERS.map((row) => (
                  <TableCell key={row.id}>{row.label}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {datasetDetails?.vector?.map((predLabel, index) => (
                <TableRow key={index}>
                  <TableCell>{predLabel.name}</TableCell>
                  <TableCell>{predLabel.empty}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {(filters.status === "all" || filters.status === "tags") && (
        <TableContainer sx={{ position: "relative", overflow: "unset" }}>
          <Table>
            <TableHead>
              <TableRow>
                {ALL_HEADERS.map((row) => (
                  <TableCell key={row.id}>{row.label}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {datasetDetails?.tags?.map((predLabel, index) => (
                <TableRow key={index}>
                  <TableCell>{predLabel.name}</TableCell>
                  <TableCell>{predLabel.dataType}</TableCell>
                  <TableCell>{predLabel.empty}</TableCell>
                  <TableCell>{predLabel.cardinality}</TableCell>
                  <TableCell>{predLabel.min}</TableCell>
                  <TableCell>{predLabel.max}</TableCell>
                  <TableCell>{predLabel.avg}</TableCell>
                  <TableCell>{predLabel.std}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </>
  );
}

DatasetSummary.propTypes = {
  model: PropTypes.object,
  dataset: PropTypes.object,
};
