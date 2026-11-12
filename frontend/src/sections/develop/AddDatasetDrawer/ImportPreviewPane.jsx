import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { fmtCount } from "src/sections/common/format/fmt";
import { PREVIEW_ROWS, ROLE_OPTIONS } from "./importFile";

/**
 * DL#1: the first 20 parsed rows (cells clipped, full value on hover) and a
 * role per column (Input / Expected / Metadata / Ignore), pre-guessed from
 * the header names.
 */
export default function ImportPreviewPane({ parsed, roles, onRole }) {
  const { columns, rows, errors, total } = parsed;
  return (
    <Stack gap={1.5} sx={{ minWidth: 0 }}>
      {errors.length > 0 && (
        <Box
          role="alert"
          sx={{
            p: 1,
            borderRadius: 1,
            bgcolor: (t) => `${t.palette.error.main}1f`,
          }}
        >
          <Typography typography="s2" color="error.main" fontWeight={600}>
            {fmtCount(errors.length, "line")} can&apos;t be read and will be
            skipped
          </Typography>
          {errors.map((e) => (
            <Typography
              key={e.line}
              typography="s3"
              color="error.main"
              component="div"
            >
              Line {e.line}: {e.message}
            </Typography>
          ))}
        </Box>
      )}
      <Box
        sx={{
          maxHeight: 320,
          overflow: "auto",
          border: 1,
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        <Table size="small" stickyHeader aria-label="File preview">
          <TableHead>
            <TableRow>
              <TableCell
                sx={{ width: 40, typography: "s3", color: "text.secondary" }}
              >
                #
              </TableCell>
              {columns.map((c) => (
                <TableCell key={c} sx={{ minWidth: 160, verticalAlign: "top" }}>
                  <Typography
                    typography="s2"
                    fontWeight={600}
                    noWrap
                    sx={{ fontFamily: "monospace" }}
                  >
                    {c}
                  </Typography>
                  {/* L2: no picker where the upload keeps no roles. */}
                  {onRole && (
                    <Select
                      size="small"
                      value={roles[c] ?? "metadata"}
                      onChange={(e) => onRole(c, e.target.value)}
                      inputProps={{ "aria-label": `Role of ${c}` }}
                      sx={{
                        mt: 0.5,
                        height: 26,
                        typography: "s3",
                        minWidth: 110,
                      }}
                    >
                      {ROLE_OPTIONS.map((r) => (
                        <MenuItem
                          key={r.value}
                          value={r.value}
                          sx={{ typography: "s2" }}
                        >
                          {r.label}
                        </MenuItem>
                      ))}
                    </Select>
                  )}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.slice(0, PREVIEW_ROWS).map((r, i) => (
              <TableRow key={i}>
                <TableCell
                  sx={{
                    typography: "s3",
                    color: "text.secondary",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {i + 1}
                </TableCell>
                {r.map((cell, j) => (
                  <TableCell
                    key={j}
                    sx={{
                      maxWidth: 220,
                      opacity: roles[columns[j]] === "ignore" ? 0.4 : 1,
                    }}
                  >
                    <CustomTooltip
                      show={cell.length > 40}
                      title={cell.slice(0, 600)}
                      size="small"
                    >
                      <Typography
                        typography="s2"
                        noWrap
                        color={cell ? "text.primary" : "text.disabled"}
                      >
                        {cell || "–"}
                      </Typography>
                    </CustomTooltip>
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      {total > PREVIEW_ROWS && (
        <Typography typography="s3" color="text.secondary">
          Showing the first {PREVIEW_ROWS} of {fmtCount(total, "row")}.
        </Typography>
      )}
    </Stack>
  );
}

ImportPreviewPane.propTypes = {
  parsed: PropTypes.shape({
    columns: PropTypes.array,
    rows: PropTypes.array,
    errors: PropTypes.array,
    total: PropTypes.number,
  }).isRequired,
  roles: PropTypes.object.isRequired,
  onRole: PropTypes.func,
};
