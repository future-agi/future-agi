import React from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import Switch from "@mui/material/Switch";
import FormControlLabel from "@mui/material/FormControlLabel";

import DataTablePagination from "src/components/data-table/DataTablePagination";

// ----------------------------------------------------------------------
// Adapter for screens that still speak MUI's TablePagination props
// (count / page / rowsPerPage / onPageChange(event, page) / ...). It renders
// the one DS footer, DataTablePagination ("Rows per page"), so every table
// pages the same way.
// ----------------------------------------------------------------------

export default function TablePaginationCustom({
  dense,
  onChangeDense,
  rowsPerPageOptions = [10, 25, 50],
  count = 0,
  page = 0,
  rowsPerPage = 10,
  onPageChange,
  onRowsPerPageChange,
  sx,
}) {
  const options = rowsPerPageOptions.map((o) =>
    typeof o === "object" ? o.value : o,
  );
  return (
    <Box sx={{ position: "relative", ...sx }}>
      <DataTablePagination
        page={page}
        pageSize={rowsPerPage}
        total={count}
        pageSizeOptions={options}
        onPageChange={(p) => onPageChange?.(null, p)}
        onPageSizeChange={(n) =>
          onRowsPerPageChange?.({ target: { value: n } })
        }
        leading={
          onChangeDense ? (
            <FormControlLabel
              label="Dense"
              control={<Switch checked={!!dense} onChange={onChangeDense} />}
            />
          ) : undefined
        }
      />
    </Box>
  );
}

TablePaginationCustom.propTypes = {
  dense: PropTypes.bool,
  onChangeDense: PropTypes.func,
  rowsPerPageOptions: PropTypes.array,
  count: PropTypes.number,
  page: PropTypes.number,
  rowsPerPage: PropTypes.number,
  onPageChange: PropTypes.func,
  onRowsPerPageChange: PropTypes.func,
  sx: PropTypes.object,
};
