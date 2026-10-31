/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import SelectionBar from "src/components/selection-bar";
import { Box, Typography, Button, Link, useTheme } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import { useNavigate } from "react-router";
import { preventHeaderSelection } from "src/utils/utils";
import { trackEvent, Events } from "src/utils/Mixpanel";
import AddDatasetDrawer from "./AddDatasetDrawer/AddDatasetDrawer";
import TotalRowsStatusBar from "../develop-detail/Common/TotalRowsStatusBar";
import { format } from "date-fns";
import ApexCharts from "apexcharts";
import PropTypes from "prop-types";
import SingleImageViewerProvider from "../develop-detail/Common/SingleImageViewer/SingleImageViewerProvider";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { APP_CONSTANTS } from "src/utils/constants";

const ApexChartRenderer = ({ data, type, max }) => {
  const muiTheme = useTheme();
  const chartRef = useRef(null);
  const color = type == "chart1" ? "#E2A6F1" : "#AE9AFD";

  useEffect(() => {
    if (chartRef.current && data) {
      const dates = data?.map((item) =>
        format(new Date(item?.timestamp), "dd/MM/yyyy"),
      );
      const values = data?.map((item) => item.value);

      const chartOptions = {
        chart: {
          type: "area",
          height: "100%",
          width: "100%",
          sparkline: {
            enabled: true,
          },
          dropShadow: {
            enabled: true,
            enabledSeries: [0],
            top: -2,
            left: 2,
            blur: 5,
            opacity: 0.06,
          },
        },
        colors: [color],
        stroke: {
          curve: "smooth",
          width: 1,
        },
        fill: {
          type: "gradient", // Set the fill type to gradient
          gradient: {
            shade: "light", // Light shade
            type: "vertical", // Vertical gradient
            shadeIntensity: 0.6,
            gradientToColors: [color, muiTheme.palette.background.default],
            opacityFrom: 0.7,
            opacityTo: 0.1,
            stops: [0, 100], // Starting from your color to white
          },
        },
        markers: {
          size: 0,
          strokeColor: muiTheme.palette.background.paper,
          strokeWidth: 3,
          strokeOpacity: 1,
          fillOpacity: 1,
        },
        series: [
          {
            data: values,
          },
        ],
        xaxis: {
          categories: dates,
          labels: {
            show: false,
          },
        },
        yaxis: {
          show: false,
          min: 0,
          max: max,
          tickAmount: max / 10,
        },
        tooltip: {
          enabled: false,
        },
      };

      // Create and render the chart
      const chart = new ApexCharts(chartRef.current, chartOptions);
      chart.render();

      return () => {
        // Destroy chart when the component is unmounted
        chart.destroy();
      };
    }
  }, [data]);

  return <div ref={chartRef} style={{ height: "100px" }} />;
};

ApexChartRenderer.propTypes = {
  data: PropTypes.array,
  type: PropTypes.string,
  max: PropTypes.number,
};

const EvalsView = () => {
  const muiTheme = useTheme();
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.dataGridPadded);
  const [addDatasetDrawerOpen, setAddDatasetDrawerOpen] = useState(false);
  const [selected, setSelected] = useState([]);
  const [, setOpenDuplicate] = useState(false);
  const [, setOpenDelete] = useState(false);
  const gridRef = useRef(null);
  const [, setTotalRows] = useState(0);
  const [selectedAll, setSelectedAll] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [maxAxis, setMaxAxis] = useState(100);
  const [isData, setIsData] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);
  preventHeaderSelection();

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        const { request } = params;
        // onSelectionChanged(null);
        setSelectedAll(false);
        const pageSize = request.endRow - request.startRow;
        const pageNumber = Math.floor(request.startRow / pageSize);

        try {
          const payload = {
            search_text: debouncedSearchQuery?.length
              ? debouncedSearchQuery
              : null,
            current_page_index: pageNumber,
            page_size: pageSize,
            sort: request?.sortModel?.map(({ colId, sort }) => ({
              column_id: colId,
              type: sort === "asc" ? "ascending" : "descending",
            })),
          };

          const { data } = await axios.post(
            endpoints.develop.eval.getEvalTemplates,
            payload,
          );

          const rows = data?.status ? data?.result?.row_data : [];
          setTotalRows(data?.result?.total_rows);
          setMaxAxis(data?.result?.max_axis);

          setIsData(data?.result?.data_available);
          params.success({
            rowData: rows,
          });
        } catch (error) {
          params.fail();
        }
      },
      getRowId: (data) => data.id,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [debouncedSearchQuery, refresh],
  );

  // Column Definitions
  const columnDefs = useMemo(
    () => [
      {
        headerName: "Title",
        field: "eval_template_name",
        flex: 1,
      },
      {
        headerName: "30 days chart",
        field: "average.avg_graph_data",
        flex: 1,
        sortable: false,
        cellRenderer: (params) => {
          const averageData = params.value;
          return (
            <ApexChartRenderer
              max={maxAxis}
              data={averageData}
              type={"chart1"}
            />
          );
        },
      },
      {
        headerName: "30 days error rate",
        field: "error_rate",
        flex: 1,
        sortable: false,
        cellRenderer: (params) => {
          const averageData = params.value;
          return (
            <ApexChartRenderer
              max={maxAxis}
              data={averageData}
              type={"chart2"}
            />
          );
        },
      },
      {
        headerName: "Avg score",
        field: "average.average",
        flex: 1,
        valueFormatter: (p) => {
          if (!p.value) return "0 %";
          return p.value + " %";
        },
      },
      {
        headerName: "30 Days run",
        field: "last_30_run",
        flex: 1,
      },
      {
        headerName: "Updated",
        field: "updated_at",
        flex: 1,
        valueFormatter: (p) => {
          if (!p.value) return ""; // Ensures no errors if value is undefined or null
          const date = new Date(p.value); // Create a Date object from p.value
          return isNaN(date.getTime())
            ? ""
            : format(date, "dd/MM/yyyy, h:mm a"); // Format as required
        },
      },
    ],
    [],
  );

  const allColumns = useMemo(() => {
    return columnDefs.filter((col) => col.field !== "checkbox");
  }, [columnDefs]);

  // Grid Options
  const defaultColDef = {
    sortable: true,
    filter: false,
    resizable: true,
    suppressHeaderMenuButton: true,
    suppressHeaderContextMenu: true,
  };

  const navigate = useNavigate();

  const [statusBar] = useState({
    statusPanels: [
      {
        statusPanel: TotalRowsStatusBar,
        align: "left",
      },
    ],
  });

  const refreshGrid = () => {
    gridRef.current?.api?.refreshServerSide({});
  };

  const onSelectionChanged = (event) => {
    if (!event) {
      setTimeout(() => {
        setSelected([]);
      }, 300);
      gridRef.current.api.deselectAll();
      return;
    }
    const rowId = event.data.id;

    setSelected((prevSelectedItems) => {
      const updatedSelectedRowsData = [...prevSelectedItems];

      const rowIndex = updatedSelectedRowsData.findIndex(
        (row) => row.id === rowId,
      );

      if (rowIndex === -1) {
        updatedSelectedRowsData.push(event.data);
      } else {
        updatedSelectedRowsData.splice(rowIndex, 1);
      }

      return updatedSelectedRowsData;
    });
  };

  const closeModal = () => {
    setOpenDelete(false);
    setOpenDuplicate(false);
    onSelectionChanged(null);
    gridRef.current.api.deselectAll();
    setSelectedAll(false);
  };

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        height: "100%",
        padding: 2,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <SingleImageViewerProvider>
        {isData ? (
          <>
            <Typography
              color="text.primary"
              variant="m1"
              fontWeight={"fontWeightSemiBold"}
            >
              Evaluations
            </Typography>
            <Box sx={{ display: "flex", alignItems: "center" }}>
              <Typography
                variant="s1"
                color="text.secondary"
                fontWeight={"fontWeightRegular"}
              >
                Create evaluation and monitor
              </Typography>
            </Box>
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginTop: 2,
              }}
            >
              <FormSearchField
                size="small"
                placeholder="Search"
                sx={{ minWidth: "415px" }}
                searchQuery={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              <Box>
                {selected?.length ? (
                  <SelectionBar count={selected.length} onCancel={closeModal} />
                ) : (
                  <Button
                    variant="contained"
                    color="primary"
                    onClick={() => {
                      trackEvent(Events.addDatasetCLicked);
                      setAddDatasetDrawerOpen(true);
                    }}
                  >
                    Create Evaluation
                  </Button>
                )}
              </Box>
            </Box>
            <Box
              className="ag-theme-quartz"
              style={{ height: "100%", paddingTop: "12px" }}
            >
              <AgGridReact
                ref={gridRef}
                getRowHeight={(params) => {
                  return params.node.rowPinned === "bottom" ? 40 : 120;
                }}
                onColumnHeaderClicked={(event) => {
                  if (
                    event.column.colId !==
                    APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
                  )
                    return;

                  if (selectedAll) {
                    event.api.deselectAll();
                    setSelectedAll(false);
                  } else {
                    event.api.selectAll();
                    setSelectedAll(true);
                  }
                }}
                rowSelection="none"
                theme={agTheme}
                columnDefs={columnDefs}
                defaultColDef={defaultColDef}
                pagination={false}
                suppressServerSideFullWidthLoadingRow={true}
                serverSideInitialRowCount={5}
                cacheBlockSize={10}
                maxBlocksInCache={10}
                suppressRowClickSelection={true}
                statusBar={statusBar}
                rowModelType="serverSide"
                serverSideDatasource={dataSource}
                isApplyServerSideTransaction={() => true}
                // onCellDoubleClicked={doubleClickCellEdit}
                onRowSelected={(event) => onSelectionChanged(event)}
                getRowId={({ data }) => data.id}
                onCellClicked={(event) => {
                  if (
                    event.column.getColId() !==
                    APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
                  ) {
                    const { data } = event;
                    navigate(`/dashboard/evaluations/${data.id}`, {
                      state: { dataset: data },
                    });
                  } else if (
                    event.column.getColId() ===
                    APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
                  ) {
                    const selected = event.node.isSelected();
                    event.node.setSelected(!selected);
                  }
                }}
              />
            </Box>
          </>
        ) : (
          <>
            <Typography fontWeight={600}>
              How to create evals and use
            </Typography>

            <Box
              sx={{
                height: "100%",
                width: "100%",
                display: "flex",
                gap: "2rem",
                flexDirection: "row",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Box
                sx={{
                  height: "300px",
                  width: "450px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "15px",
                }}
              >
                <img
                  src={
                    muiTheme.palette.mode === "dark"
                      ? "/assets/images/evals/eval_frame_1_dark.jpg"
                      : "/assets/images/evals/eval_frame_1.jpg"
                  }
                  alt="Overlayed"
                  style={{
                    display: "block",
                    objectFit: "cover",
                    width: "100%",
                    height: "100%",
                    borderRadius: "8px",
                  }}
                />
                <Typography
                  sx={{
                    fontWeight: "600",
                    fontFamily: "inherit",
                  }}
                >
                  Select from present evals
                  <Typography
                    sx={{
                      fontWeight: "400",
                      color: "text.secondary",
                      fontFamily: "inherit",
                    }}
                  >
                    Choose from preset evaluations and simplify your decisions
                    instantly!
                  </Typography>
                </Typography>
              </Box>
              <Box
                sx={{
                  height: "300px",
                  width: "450px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "15px",
                }}
              >
                <img
                  src={
                    muiTheme.palette.mode === "dark"
                      ? "/assets/images/evals/eval_frame_2_dark.jpg"
                      : "/assets/images/evals/eval_frame_2.jpg"
                  }
                  alt="Overlayed"
                  style={{
                    display: "block",
                    objectFit: "cover",
                    width: "100%",
                    height: "100%",
                    borderRadius: "8px",
                  }}
                />
                <Typography
                  sx={{
                    fontWeight: "600",
                    fontFamily: "inherit",
                  }}
                >
                  Save as template
                  <Typography
                    sx={{
                      fontWeight: "400",
                      color: "text.secondary",
                      fontFamily: "inherit",
                    }}
                  >
                    Configure them once, save as templates, and reuse
                    effortlessly whenever you need.
                  </Typography>
                </Typography>
              </Box>
              <Box
                sx={{
                  height: "300px",
                  width: "450px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "15px",
                }}
              >
                <img
                  src={
                    muiTheme.palette.mode === "dark"
                      ? "/assets/images/evals/eval_frame_3_dark.jpg"
                      : "/assets/images/evals/eval_frame_3.jpg"
                  }
                  alt="Overlayed"
                  style={{
                    display: "block",
                    objectFit: "cover",
                    width: "100%",
                    height: "100%",
                    borderRadius: "8px",
                  }}
                />
                <Typography
                  sx={{
                    fontWeight: "600",
                    fontFamily: "inherit",
                  }}
                >
                  Run evaluation test
                  <Typography
                    sx={{
                      fontWeight: "400",
                      color: "text.secondary",
                      fontFamily: "inherit",
                    }}
                  >
                    Ensure accuracy and reliabilty by running a test before
                    applying changes.
                  </Typography>
                </Typography>
              </Box>
            </Box>

            <Box
              sx={{
                height: "100%",
                width: "100%",
                display: "flex",
                paddingX: "15px",
                gap: 1,
                flexDirection: "row",
                justifyContent: "center",
                alignItems: "center",
                textAlign: "center",
              }}
            >
              <Typography
                sx={{
                  color: "grey",
                  fontWeight: "600",
                  marginTop: "10px",
                }}
              >
                For more instructions, check out{" "}
                <Link
                  href="https://docs.futureagi.com/docs"
                  sx={{ color: "primary", cursor: "pointer" }}
                >
                  docs
                </Link>
                <Typography
                  sx={{
                    marginTop: "10px",
                  }}
                >
                  <Button
                    variant="contained"
                    color="primary"
                    onClick={() => setAddDatasetDrawerOpen(true)}
                  >
                    Start creating evaluations
                  </Button>
                </Typography>
              </Typography>
            </Box>
          </>
        )}
      </SingleImageViewerProvider>
      <AddDatasetDrawer
        open={addDatasetDrawerOpen}
        onClose={() => setAddDatasetDrawerOpen(false)}
        setDrawer={setAddDatasetDrawerOpen}
        refreshGrid={refreshGrid}
        allColumns={allColumns}
        setRefresh={setRefresh}
        refresh={refresh}
        onBack={() => setAddDatasetDrawerOpen(true)}
      />
    </Box>
  );
};

export default EvalsView;
