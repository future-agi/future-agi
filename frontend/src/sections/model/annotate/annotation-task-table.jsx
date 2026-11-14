import React from "react";
import {
  Table,
  TableBody,
  TableContainer,
  tableCellClasses,
} from "@mui/material";
import Box from "@mui/material/Box";
import { useTheme } from "@mui/material/styles";
import PropTypes from "prop-types";
import {
  TableEmptyRows,
  TableHeadCustom,
  emptyRows,
} from "src/components/table";
import AnnotationTaskTableRow from "./annotation-task-table-row";

const TABLE_HEAD = [
  { id: "name", label: "Name" },
  { id: "modelName", label: "Model Name", width: 120 },
  { id: "progress", label: "Progress", width: 120 },
  { id: "assigned", label: "Assigned to", align: "right", width: 140 },
  { id: "", width: 88 },
];

export default function AnnotationTaskTable({ table, tableData }) {
  const theme = useTheme();

  const {
    dense,
    page,
    // order,
    // orderBy,
    rowsPerPage,
    //
    // selected,
    // onSelectRow,
    // onSelectAllRows,
    //
    // onSort,
    // onChangeDense,
    // onChangePage,
    // onChangeRowsPerPage,
  } = table;

  const denseHeight = dense ? 58 : 78;

  return (
    <>
      <Box
        sx={{
          position: "relative",
          m: theme.spacing(-2, -3, -3, -3),
        }}
      >
        <TableContainer
          sx={{
            p: theme.spacing(0, 3, 3, 3),
          }}
        >
          <Table
            size={dense ? "small" : "medium"}
            sx={{
              minWidth: 960,
              borderCollapse: "separate",
              borderSpacing: "0 16px",
            }}
          >
            <TableHeadCustom
              // order={order}
              // orderBy={orderBy}
              headLabel={TABLE_HEAD}
              rowCount={tableData.length}
              // numSelected={selected.length}
              // onSort={onSort}
              // onSelectAllRows={(checked) =>
              //   onSelectAllRows(
              //     checked,
              //     tableData.map((row) => row.id)
              //   )
              // }
              sx={{
                [`& .${tableCellClasses.head}`]: {
                  "&:first-of-type": {
                    borderTopLeftRadius: 12,
                    borderBottomLeftRadius: 12,
                  },
                  "&:last-of-type": {
                    borderTopRightRadius: 12,
                    borderBottomRightRadius: 12,
                  },
                },
              }}
            />
            <TableBody>
              {tableData
                // .slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage)
                .map((row) => (
                  <AnnotationTaskTableRow
                    key={row.id}
                    row={row}
                    // selected={selected.includes(row.id)}
                    // onSelectRow={() => onSelectRow(row.id)}
                    // onDeleteRow={() => onDeleteRow(row.id)}
                  />
                ))}

              <TableEmptyRows
                height={denseHeight}
                emptyRows={emptyRows(page, rowsPerPage, tableData.length)}
              />

              {/* <TableNoData
                notFound={notFound}
                sx={{
                  m: -2,
                  borderRadius: "8px",
                  border: `dashed 1px ${theme.palette.divider}`,
                }}
              /> */}
            </TableBody>
          </Table>
        </TableContainer>
      </Box>
    </>
  );
}

AnnotationTaskTable.propTypes = {
  table: PropTypes.object,
  tableData: PropTypes.array,
};
