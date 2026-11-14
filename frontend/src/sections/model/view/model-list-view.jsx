import React, { useState, useEffect, useCallback } from "react";
import Card from "@mui/material/Card";
import Table from "@mui/material/Table";
import Container from "@mui/material/Container";
import TableBody from "@mui/material/TableBody";
import TableContainer from "@mui/material/TableContainer";
import { paths } from "src/routes/paths";
import { useRouter } from "src/routes/hooks";
import { useGetModels } from "src/api/model/info";
import Scrollbar from "src/components/scrollbar";
import { useSettingsContext } from "src/components/settings";
import {
  useTable,
  emptyRows,
  TableNoData,
  TableSkeleton,
  TableEmptyRows,
  TableHeadCustom,
  TablePaginationCustom,
} from "src/components/table";
import { Typography } from "@mui/material";

import ModelTableRow from "../model-table-row";

// ----------------------------------------------------------------------

const TABLE_HEAD = [
  { id: "name", label: "Model" },
  { id: "modelType", label: "Model Type", width: 170 },
  { id: "alerts", label: "Alerts", width: 170 },
  { id: "performance", label: "Performance", width: 170 },
  { id: "dataDrift", label: "Data Drift", width: 170 },
  { id: "analytics", label: "Analytics", width: 170 },
  { id: "volume", label: "30 Days Volume", width: 170 },
];

// ----------------------------------------------------------------------

export default function ModelListView() {
  const router = useRouter();

  const table = useTable();

  const settings = useSettingsContext();

  const [tableData, setTableData] = useState([]);

  const { models, modelsLoading, modelsEmpty } = useGetModels();

  useEffect(() => {
    if (models.length) {
      setTableData(models);
    }
  }, [models]);

  const denseHeight = table.dense ? 60 : 80;

  const notFound = !tableData.length || modelsEmpty;

  const handleViewRow = useCallback(
    (id) => {
      router.push(paths.dashboard.models.details(id));
    },
    [router],
  );

  return (
    <>
      <Container maxWidth={settings.themeStretch ? false : "lg"}>
        <Typography variant="h2">Models</Typography>

        <Card>
          <TableContainer sx={{ position: "relative", overflow: "unset" }}>
            <Scrollbar>
              <Table
                size={table.dense ? "small" : "medium"}
                sx={{ minWidth: 960 }}
              >
                <TableHeadCustom
                  headLabel={TABLE_HEAD}
                  rowCount={tableData.length}
                />

                <TableBody>
                  {modelsLoading ? (
                    [...Array(table.rowsPerPage)].map((i, index) => (
                      <TableSkeleton key={index} sx={{ height: denseHeight }} />
                    ))
                  ) : (
                    <>
                      {tableData
                        .slice(
                          table.page * table.rowsPerPage,
                          table.page * table.rowsPerPage + table.rowsPerPage,
                        )
                        .map((row, index) => (
                          <ModelTableRow
                            key={index}
                            row={row}
                            // selected={table.selected.includes(row.id)}
                            onSelectRow={() => table.onSelectRow(row.id)}
                            // onDeleteRow={() => handleDeleteRow(row.id)}
                            // onEditRow={() => handleEditRow(row.id)}
                            onViewRow={() => handleViewRow(row.id)}
                          />
                        ))}
                    </>
                  )}

                  <TableEmptyRows
                    height={denseHeight}
                    emptyRows={emptyRows(
                      table.page,
                      table.rowsPerPage,
                      tableData.length,
                    )}
                  />

                  <TableNoData notFound={notFound} />
                </TableBody>
              </Table>
            </Scrollbar>
          </TableContainer>

          <TablePaginationCustom
            count={tableData.length}
            page={table.page}
            rowsPerPage={table.rowsPerPage}
            onPageChange={table.onChangePage}
            onRowsPerPageChange={table.onChangeRowsPerPage}
            //
            dense={table.dense}
            // onChangeDense={table.onChangeDense}
          />
        </Card>
      </Container>
    </>
  );
}
