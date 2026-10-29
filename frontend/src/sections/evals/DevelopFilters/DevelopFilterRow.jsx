import { Box, Button, FormControl, IconButton } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { MapColumnTypeToFilterType } from "./common";
import DevelopFilterValue from "./DevelopFilterValue";
import SvgColor from "src/components/svg-color";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";

const DevelopFilterRow = ({
  index,
  removeFilter,
  addFilter,
  filter,
  allColumns,
  updateFilter,
}) => {
  const onPropertyChange = (e) => {
    const columnId = e.target.value;
    const column = allColumns.find((column) => column.field === columnId);
    const filterType = MapColumnTypeToFilterType[column?.data_type] || "text";

    updateFilter(filter.id, (internalFilter) => ({
      ...internalFilter,
      columnId: column?.colId || column?.col?.id,
      filterConfig: {
        ...internalFilter.filterConfig,
        filterType: filterType,
        filterOp: filterType === "array" ? "contains" : "equals",
        filterValue: "",
      },
    }));
  };

  const allowedColumns = allColumns;

  return (
    <Box sx={{ display: "flex", justifyContent: "space-between" }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
        <FormControl size="small" sx={{ minWidth: "260px" }}>
          <FormSearchSelectFieldState
            value={filter.columnId}
            label="Property"
            showClear={false}
            size="small"
            options={allowedColumns.map((column) => ({
              label: column.headerName,
              value: column.field,
            }))}
            onChange={onPropertyChange}
          />
        </FormControl>
        <DevelopFilterValue filter={filter} updateFilter={updateFilter} />
        <IconButton
          size="small"
          onClick={() => removeFilter(filter.id)}
          sx={{ color: "primary.light", ml: (theme) => theme.spacing(2) }}
        >
          <SvgColor
            src={`/assets/icons/components/ic_delete.svg`}
            sx={{
              width: 20,
              height: 20,
              color: (theme) => theme.palette.text.disabled,
            }}
          />
        </IconButton>
      </Box>
      <Box>
        {index === 0 && (
          <Button
            onClick={addFilter}
            variant="outlined"
            color="primary"
            size="small"
            startIcon={
              <SvgColor
                src="/assets/icons/ic_add.svg"
                sx={{
                  width: "20px !important",
                  height: "20px !important",
                  color: "primary.main",
                }}
              />
            }
            sx={{
              // p: theme.spacing(2),
              color: "primary.main",
              borderColor: "primary.main",
              // fontSize: "14px",
              // fontWeight: 600,
              "& .MuiButton-startIcon": {
                margin: 0,
                paddingRight: (theme) => theme.spacing(1),
              },
              whiteSpace: "nowrap",
            }}
          >
            Add Filter
          </Button>
        )}
      </Box>
    </Box>
  );
};

DevelopFilterRow.propTypes = {
  index: PropTypes.number,
  removeFilter: PropTypes.func,
  addFilter: PropTypes.func,
  filter: PropTypes.object,
  allColumns: PropTypes.array,
  updateFilter: PropTypes.func,
};

export default DevelopFilterRow;
