import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Table, TableBody, TableCell, TableHead, TableRow, TextField } from "@mui/material";
import { ALK_MONO } from "../alkTokens";

const LONG = 120;

/**
 * Arrays are shown by their count first — one nested order would otherwise turn a row into
 * a wall — and anything long is clipped with the whole value left on hover.
 */
const cellText = (value) => {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return `[${value.length}] ${JSON.stringify(value)}`;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
};

const DataTable = ({ columns, rows, count }) => {
  const [filter, setFilter] = useState("");
  const shown = filter
    ? rows.filter((row) =>
        columns.some((c) => cellText(row[c]).toLowerCase().includes(filter.toLowerCase()))
      )
    : rows;
  const tall = rows.length > 12;

  return (
    <Box>
      {rows.length > 5 && (
        <TextField
          size="small"
          value={filter}
          placeholder="filter rows"
          onChange={(event) => setFilter(event.target.value)}
          sx={{ mb: 1, "& .MuiInputBase-input": { fontFamily: ALK_MONO, fontSize: 12 } }}
        />
      )}
      <Box
        sx={{
          overflowX: "auto",
          ...(tall ? { maxHeight: "21rem", overflowY: "auto" } : {}),
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        <Table size="small" stickyHeader={tall}>
          <TableHead>
            <TableRow>
              {columns.map((column) => (
                <TableCell
                  key={column}
                  sx={{ fontFamily: ALK_MONO, fontSize: 11.5, bgcolor: "background.default" }}
                >
                  {column}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {shown.map((row, index) => (
              // Agent state has no guaranteed id, so position is the only stable key.
              // eslint-disable-next-line react/no-array-index-key
              <TableRow key={index}>
                {columns.map((column) => {
                  const full = cellText(row[column]);
                  const clipped = full.length > LONG;
                  return (
                    <TableCell
                      key={column}
                      title={clipped ? full : undefined}
                      sx={{
                        fontFamily: ALK_MONO,
                        fontSize: 11.5,
                        maxWidth: "30rem",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {clipped ? `${full.slice(0, LONG)}…` : full}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      {count > rows.length && (
        <Box sx={{ mt: 0.5, fontFamily: ALK_MONO, fontSize: 11, color: "text.secondary" }}>
          showing {rows.length} of {count}
        </Box>
      )}
    </Box>
  );
};

DataTable.propTypes = {
  columns: PropTypes.array.isRequired,
  rows: PropTypes.array.isRequired,
  count: PropTypes.number,
};

export default DataTable;
