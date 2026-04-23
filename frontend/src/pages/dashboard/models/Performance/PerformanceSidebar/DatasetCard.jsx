/* eslint-disable react-hooks/exhaustive-deps */
import {
  Box,
  Collapse,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import Label from "src/components/label";
import FilterSection from "./FilterCard/FilterSection";
import {
  useGetAllCustomMetrics,
  useGetMetricOptions,
} from "src/api/model/metric";
import { useParams } from "react-router";
import { useEffect } from "react";
import { getRandomId } from "src/utils/utils";

const DatasetCard = ({
  index,
  dataset,
  setDataset,
  cloneDataset,
  removeDataset,
}) => {
  const [open, setOpen] = useState(true);

  const theme = useTheme();

  const { id } = useParams();

  const { data: allCustomMetrics } = useGetAllCustomMetrics(id);

  const selectedMetricId = dataset?.metricId;
  const selectedEnv = dataset?.environment;
  const selectedVersion = dataset?.version;

  const { data: datasetOptions } = useGetMetricOptions(id, selectedMetricId, {
    enabled: Boolean(selectedMetricId),
  });

  const environmentOptions = useMemo(() => {
    if (!datasetOptions) return [];

    const set = new Set(datasetOptions.map((o) => o.environment));

    return Array.from(set);
  }, [datasetOptions, selectedMetricId]);

  const versionOptions = useMemo(() => {
    if (!datasetOptions) return [];
    if (selectedEnv) {
      return datasetOptions.reduce((arr, { environment, version }) => {
        if (environment === selectedEnv && !arr.includes(version)) {
          arr.push(version);
        }
        return arr;
      }, []);
    } else {
      const set = new Set(datasetOptions.map((o) => o.version));
      return Array.from(set);
    }
  }, [datasetOptions, selectedMetricId, selectedEnv]);

  useEffect(() => {
    if (!datasetOptions || !selectedMetricId) return;
    if (!selectedEnv?.length || !selectedVersion?.length) {
      const dataset = datasetOptions?.[0];
      setDataset(index, (d) => ({
        ...d,
        environment: dataset.environment,
        version: dataset.version,
      }));
    }
  }, [datasetOptions, selectedMetricId]);

  const handleMetricChange = (e) => {
    setDataset(index, (d) => ({
      ...d,
      metricId: e.target.value,
      environment: "",
      version: "",
    }));
  };

  const handleEnvironmentChange = (e) => {
    const newEnv = e.target.value;
    setDataset(index, (d) => ({ ...d, environment: newEnv }));
  };

  const handleVersionChange = (e) => {
    setDataset(index, (d) => ({ ...d, version: e.target.value }));
  };

  const addFilter = () => {
    setDataset(index, (d) => ({
      ...d,
      filters: [
        ...d.filters,
        {
          id: getRandomId(),
          property: "",
          operator: "equal",
          values: [],
          type: "",
          datatype: "string",
          key: "",
          keyId: "",
        },
      ],
    }));
  };

  const removeFilter = (idx) => {
    setDataset(index, (d) => ({
      ...d,
      filters: d.filters.filter((_, i) => i !== idx),
    }));
  };

  const cloneFilter = (idx) => {
    setDataset(index, (d) => ({
      ...d,
      filters: [...d.filters, d.filters[idx]],
    }));
  };

  const setFilter = (idx, update) => {
    setDataset(index, (d) => {
      const newFilters = [...d.filters];
      if (typeof update === "function") {
        newFilters[idx] = update(newFilters[idx]);
      } else {
        newFilters[idx] = update;
      }
      return { ...d, filters: newFilters };
    });
  };

  return (
    <Paper elevation={2} sx={{ display: "flex", flexDirection: "column" }}>
      <Box
        sx={{
          padding: "14px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Label variant="soft">Dataset {index + 1}</Label>
        <Box sx={{ display: "flex", alignItems: "center" }}>
          <IconButton size="small" onClick={() => cloneDataset(index)}>
            <Iconify
              icon="bxs:duplicate"
              width={16}
              height={16}
              sx={{ color: "text.secondary" }}
            />
          </IconButton>
          <IconButton size="small" onClick={addFilter}>
            <Iconify
              width={16}
              height={16}
              icon="mingcute:filter-2-fill"
              sx={{ color: "text.secondary" }}
            />
          </IconButton>

          {index > 0 && (
            <IconButton size="small" onClick={() => removeDataset(index)}>
              <Iconify
                icon="solar:trash-bin-trash-bold"
                width={16}
                height={16}
                sx={{ color: "text.secondary" }}
              />
            </IconButton>
          )}
          <IconButton size="small" onClick={() => setOpen(!open)}>
            <Iconify
              width={16}
              height={16}
              icon="ci:chevron-down"
              sx={{
                color: "text.secondary",
                transform: open ? "rotate(180deg)" : null,
              }}
            />
          </IconButton>
        </Box>
      </Box>
      <Collapse in={open} orientation="vertical">
        <Box
          sx={{
            paddingTop: 1,
            gap: "12px",
            display: "flex",
            flexDirection: "column",
            paddingX: "14px",
          }}
        >
          <FormControl size="small">
            <InputLabel>Metric</InputLabel>
            <Select
              label="Metric"
              value={dataset.metricId}
              onChange={handleMetricChange}
            >
              {allCustomMetrics?.map(({ id, name }) => (
                <MenuItem key={id} value={id}>
                  {name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl size="small">
            <InputLabel>Environment</InputLabel>
            <Select
              label="Environment"
              value={selectedEnv}
              onChange={handleEnvironmentChange}
            >
              {environmentOptions.map((o) => (
                <MenuItem key={o} value={o}>
                  {o}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl size="small">
            <InputLabel>Version</InputLabel>
            <Select
              label="Version"
              value={selectedVersion}
              onChange={handleVersionChange}
            >
              {versionOptions.map((o) => (
                <MenuItem key={o} value={o}>
                  {o}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Box>
        <Box
          sx={{
            paddingY: "14px",
            gap: "14px",
            display: "flex",
            flexDirection: "column",
          }}
        >
          {dataset?.filters?.map((filter, i) => (
            <Box
              sx={{
                borderTop: `2px solid ${theme.palette.background.neutral}`,
                paddingX: "14px",
                paddingTop: 1,
              }}
              key={filter.id}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "flex-end",
                }}
              >
                <IconButton size="small" onClick={() => cloneFilter(i)}>
                  <Iconify
                    icon="bxs:duplicate"
                    width={16}
                    height={16}
                    sx={{ color: "text.secondary" }}
                  />
                </IconButton>

                <IconButton size="small" onClick={() => removeFilter(i)}>
                  <Iconify
                    icon="solar:trash-bin-trash-bold"
                    width={16}
                    height={16}
                    sx={{ color: "text.secondary" }}
                  />
                </IconButton>
              </Box>
              <FilterSection
                filter={filter}
                setFilter={(update) => setFilter(i, update)}
              />
            </Box>
          ))}
        </Box>
      </Collapse>
    </Paper>
  );
};

DatasetCard.propTypes = {
  index: PropTypes.number,
  setDataset: PropTypes.func,
  dataset: PropTypes.object,
  cloneDataset: PropTypes.func,
  removeDataset: PropTypes.func,
};

export default DatasetCard;
