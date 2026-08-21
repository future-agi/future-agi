import { Box, Button, Card, Collapse, Typography } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

import DatasetFilterRow from "./DatasetFilterRow";

const DatasetFilter = ({
  filterOpen,
  filters,
  properties,
  setFilters,
  datasetOptions,
  addFilter,
  removeFilter,
}) => {
  const setValuesForIndex = (idx, values) => {
    setFilters((f) =>
      f.map((obj, index) => {
        if (index === idx) {
          return {
            ...obj,
            ...values,
          };
        }
        return { ...obj };
      }),
    );
  };

  return (
    <Collapse in={filterOpen}>
      <Box sx={{ paddingY: "10px", paddingX: "20px" }}>
        <Box
          sx={{
            borderWidth: 2,
            borderStyle: "dashed",
            borderColor: "divider",
            borderRadius: 2,
            padding: "7px",
          }}
        >
          <Typography
            fontSize="16px"
            fontWeight={700}
            color="text.disabled"
            sx={{ paddingX: 2, paddingY: "12px" }}
          >
            Filter
          </Typography>

          <Card sx={{ padding: 2, border: "none", display: "flex" }}>
            <Box
              sx={{
                flex: 1,
                display: "flex",
                gap: "14px",
                flexDirection: "column",
              }}
            >
              {filters.map((filter, idx) => (
                <DatasetFilterRow
                  key={filter.id}
                  properties={properties}
                  setValuesForIndex={setValuesForIndex}
                  idx={idx}
                  filter={filter}
                  options={datasetOptions?.[filter.key] || []}
                  removeFilter={removeFilter}
                  totalFilters={filters.length}
                />
              ))}
            </Box>
            <Box>
              <Button
                variant="contained"
                color="primary"
                onClick={() => addFilter()}
                startIcon={<Iconify icon="ic:round-plus" />}
              >
                Add filter
              </Button>
            </Box>
          </Card>
        </Box>
      </Box>
    </Collapse>
  );
};

DatasetFilter.propTypes = {
  filterOpen: PropTypes.bool,
  setFilterOpen: PropTypes.func,
  properties: PropTypes.array,
  filters: PropTypes.array,
  setFilters: PropTypes.func,
  datasetOptions: PropTypes.object,
  addFilter: PropTypes.func,
  removeFilter: PropTypes.func,
};

export default DatasetFilter;
