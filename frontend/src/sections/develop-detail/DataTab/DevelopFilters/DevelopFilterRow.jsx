import { Box, Button, IconButton, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo } from "react";
import { MapColumnTypeToFilterType } from "./common";
import SvgColor from "src/components/svg-color";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import DevelopFilterValue from "./DevelopFilterValue";

const allowedColumnTypes = [
  "text",
  "integer",
  "float",
  "boolean",
  "datetime",
  "array",
  "audio",
];

const DevelopFilterRow = ({
  index,
  removeFilter,
  addFilter,
  filter,
  allColumns,
  updateFilter,
}) => {
  const theme = useTheme();
  const onPropertyChange = useCallback(
    (e) => {
      const columnId = e.target.value;
      const column = allColumns.find((col) => col.field === columnId);
      const colDataType = column?.col?.data_type;
      const filterType = MapColumnTypeToFilterType[colDataType];

      updateFilter(filter.id, (internalFilter) => ({
        ...internalFilter,
        columnId,
        filterConfig: {
          ...internalFilter.filterConfig,
          filterType,
          filterOp: filterType === "array" ? "contains" : "equals",
          filterValue: "",
        },
      }));
    },
    [allColumns, filter.id, updateFilter],
  );
  const allowedColumns = useMemo(
    () =>
      allColumns.filter((column) => {
        const colDataType = column?.col?.data_type;
        return allowedColumnTypes?.includes(colDataType);
      }),
    [allColumns],
  );

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
      }}
    >
      <Box
        sx={{ display: "flex", alignItems: "center", gap: theme.spacing(1.5) }}
      >
        <FormSearchSelectFieldState
          onChange={onPropertyChange}
          label="Property"
          size="small"
          value={filter.columnId}
          sx={{ minWidth: "263px" }}
          options={allowedColumns.map((column) => {
            const colDataType = column?.col?.data_type;
            const displayName =
              colDataType === "audio"
                ? `${column.headerName} duration`
                : column.headerName;

            return {
              label: displayName,
              value: column.field,
            };
          })}
        />

        <DevelopFilterValue filter={filter} updateFilter={updateFilter} />
        <IconButton
          size="small"
          onClick={() => removeFilter(filter.id)}
          sx={{ color: "primary.light", ml: theme.spacing(2) }}
        >
          <SvgColor
            src={`/assets/icons/components/ic_delete.svg`}
            sx={{ width: 20, height: 20, color: theme.palette.text.secondary }}
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
                  width: "20ox !important",
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
                paddingRight: theme.spacing(1),
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

const MemoizedDevelopFilterRow = React.memo(DevelopFilterRow);
MemoizedDevelopFilterRow.displayName = "DevelopFilterRow";

export default MemoizedDevelopFilterRow;
