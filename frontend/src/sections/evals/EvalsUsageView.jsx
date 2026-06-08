import React, { useEffect, useMemo, useRef, useState } from "react";
import EvalsWrapper from "./EvalsWrapper";
import ApexCharts from "apexcharts";
import { format } from "date-fns";
import PropTypes from "prop-types";
import { useLocation, useNavigate } from "react-router";
import { useDebounce } from "src/hooks/use-debounce";
import { preventHeaderSelection } from "src/utils/utils";
import axios, { endpoints } from "src/utils/axios";
import { Box, Button, Typography, useTheme } from "@mui/material";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { ShowComponent } from "src/components/show";
import { evalsDoc, EvalsFeatures } from "./constant";
import { Link } from "react-router-dom";
import LandingPageCard from "src/components/LandingPageCard/LandingPageCard";
// import LoadingScreen from "src/components/loading-screen/loading-screen";
import CustomStatusBar from "./CustomStatusBar";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { APP_CONSTANTS } from "src/utils/constants";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import EvalOnboardingFocusPanel from "./components/EvalOnboardingFocusPanel";
import {
  buildEvalReviewDetailHref,
  buildEvalReviewRouteFocusPayload,
  evalSetupQuickStartAttributionFromSearch,
  getEvalReviewOnboardingCopy,
  getEvalReviewOnboardingParams,
} from "./components/evalCreateOnboarding";

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
          height: "90%",
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
          max: (max) => max,
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

  return <div ref={chartRef} style={{ height: "32px" }} />;
};

ApexChartRenderer.propTypes = {
  data: PropTypes.array,
  type: PropTypes.string,
  max: PropTypes.number,
};

const EvalsUsageView = () => {
  const _muiTheme = useTheme();
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.withColumnBorder);
  const gridRef = useRef(null);
  const [maxAxis, setMaxAxis] = useState(100);
  const [searchQuery, setSearchQuery] = useState("");
  const [hasData, setHasData] = useState(true);
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);
  const [firstLoading, setFirstLoading] = useState(true);
  const navigate = useNavigate();
  const location = useLocation();
  const { mutate: recordActivationEvent } = useRecordActivationEvent();
  const recordedOnboardingFocusRef = useRef(false);
  const reviewOnboardingParams = useMemo(
    () => getEvalReviewOnboardingParams(location.search),
    [location.search],
  );
  const onboardingQuickStartAttribution = useMemo(
    () => evalSetupQuickStartAttributionFromSearch(location.search),
    [location.search],
  );
  const reviewOnboardingCopy = useMemo(
    () => getEvalReviewOnboardingCopy(reviewOnboardingParams),
    [reviewOnboardingParams],
  );
  preventHeaderSelection();

  useEffect(() => {
    if (
      !reviewOnboardingParams.isOnboarding ||
      recordedOnboardingFocusRef.current
    ) {
      return;
    }

    recordedOnboardingFocusRef.current = true;
    recordActivationEvent?.(
      buildEvalReviewRouteFocusPayload({
        previousRunId: reviewOnboardingParams.previousRunId,
        quickStartAttribution: onboardingQuickStartAttribution,
        rerunFrom: reviewOnboardingParams.rerunFrom,
        route: "eval_usage_list",
        runId: reviewOnboardingParams.runId,
        setupLanguage: reviewOnboardingParams.setupLanguage,
        setupProvider: reviewOnboardingParams.setupProvider,
        sourceId: reviewOnboardingParams.sourceId,
        sourceType: reviewOnboardingParams.sourceType,
        traceId: reviewOnboardingParams.traceId,
      }),
    );
  }, [
    onboardingQuickStartAttribution,
    recordActivationEvent,
    reviewOnboardingParams,
  ]);

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        const { request } = params;
        const pageSize = request.endRow - request.startRow;
        const pageNumber = Math.floor(request.startRow / pageSize);

        try {
          if (pageNumber === 0 && !debouncedSearchQuery) {
            setFirstLoading(true);
          }
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
          setFirstLoading(false);

          const rows = data?.status ? data?.result?.row_data : [];
          if (pageNumber === 0 && rows.length === 0 && !debouncedSearchQuery) {
            setHasData(false);
          }
          setMaxAxis(data?.result?.max_axis);
          params.success({
            rowData: rows,
            rowCount: data?.result?.total_rows ?? 0,
          });
        } catch (error) {
          setFirstLoading(false);
          params.fail();
        }
      },
      getRowId: (data) => data.id,
    }),
    [debouncedSearchQuery],
  );

  // Column Definitions
  const columnDefs = useMemo(
    () => [
      {
        headerName: "Evaluation name",
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
              max={params.data.max_axis || maxAxis || 100}
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
              max={params.data.max_axis || maxAxis || 100}
              data={averageData}
              type={"chart2"}
            />
          );
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
          if (!p.value) return "";
          const date = new Date(p.value);
          return isNaN(date.getTime())
            ? ""
            : format(date, "dd/MM/yyyy, h:mm a");
        },
      },
    ],
    [],
  );

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      sortable: true,
      filter: false,
      resizable: true,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
    }),
    [],
  );

  const statusBar = useMemo(() => {
    if (firstLoading) return;
    return {
      statusPanels: [
        {
          statusPanel: CustomStatusBar,
          align: "left",
        },
      ],
    };
  }, [firstLoading]);

  return (
    <EvalsWrapper currentTab="usage">
      <ShowComponent condition={hasData}>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            height: "100%",
            minHeight: 0,
          }}
        >
          <EvalOnboardingFocusPanel
            hidden={!reviewOnboardingParams.isOnboarding}
            tourAnchor={reviewOnboardingParams.tourAnchor}
            {...reviewOnboardingCopy}
          />
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexShrink: 0,
            }}
          >
            <FormSearchField
              size="small"
              placeholder="Search"
              sx={{ minWidth: "415px" }}
              autoFocus
              disabled={firstLoading}
              searchQuery={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            <Box>
              <Button
                variant="contained"
                color="primary"
                disabled={firstLoading}
                onClick={() => {
                  navigate("/dashboard/evaluations");
                }}
                sx={{
                  px: "24px",
                  typography: "s1",
                }}
              >
                Create Evaluation
              </Button>
            </Box>
          </Box>
          <Box
            className="ag-theme-quartz"
            sx={{ flex: 1, minHeight: 0, pt: "12px" }}
          >
            <AgGridReact
              ref={gridRef}
              getRowHeight={40}
              rowSelection="none"
              theme={agTheme}
              columnDefs={columnDefs}
              defaultColDef={defaultColDef}
              pagination={false}
              suppressServerSideFullWidthLoadingRow={true}
              serverSideInitialRowCount={5}
              cacheBlockSize={10}
              maxBlocksInCache={10}
              suppressContextMenu={true}
              suppressRowClickSelection={true}
              statusBar={firstLoading ? null : statusBar}
              rowModelType="serverSide"
              serverSideDatasource={dataSource}
              isApplyServerSideTransaction={() => true}
              getRowId={({ data }) => data.id}
              onCellClicked={(event) => {
                if (
                  event.column.getColId() !==
                  APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
                ) {
                  const { data } = event;
                  if (!data?.id) return;
                  trackEvent(Events.usageEvalClicked, {
                    [PropertyName.evalId]: data.id,
                    [PropertyName.evalName]: data?.eval_template_name,
                  });
                  navigate(
                    buildEvalReviewDetailHref(data.id, location.search),
                    {
                      state: { dataset: data },
                    },
                  );
                }
              }}
              // loading={firstLoading}
              // loadingOverlayComponent={() => (
              //   <Box
              //     sx={{
              //       width: "100vw",
              //       height: "100vh",
              //       backgroundColor: "background.paper",
              //     }}
              //   >
              //     <LoadingScreen sx={undefined} />
              //   </Box>
              // )}
            />
          </Box>
        </Box>
      </ShowComponent>
      <ShowComponent condition={!hasData}>
        <Box
          height={"100%"}
          display="flex"
          justifyContent="center"
          flexDirection={"column"}
          gap={3}
        >
          <Box
            display="flex"
            flexDirection={"column"}
            gap={"4px"}
            alignItems={"center"}
          >
            <Typography
              typography="m2"
              fontWeight={"fontWeightSemiBold"}
              color="text.primary"
              textAlign={"center"}
            >
              Evaluations
            </Typography>
            <Typography
              typography="s1"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
              textAlign={"center"}
            >
              Create, test and manage your evaluations
            </Typography>
          </Box>
          <Box
            display="flex"
            justifyContent="center"
            gap={(theme) => theme.spacing(2)}
            flexWrap={"wrap"}
          >
            {EvalsFeatures.map((item, index) => (
              <Box flex={1} key={index}>
                <LandingPageCard
                  title={item.title}
                  description={item.description}
                  image={item.image}
                  showAction={item.showAction}
                />
              </Box>
            ))}
          </Box>
          <Box
            display="flex"
            flexDirection={"column"}
            gap={"4px"}
            alignItems={"center"}
          >
            <Typography
              typography="s1"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
              textAlign={"center"}
            >
              For more instructions, check out{" "}
              <Link to={evalsDoc} target="_blank">
                docs
              </Link>
            </Typography>
            <Button
              variant="contained"
              color="primary"
              type="button"
              onClick={() => navigate("/dashboard/evaluations")}
              sx={{ width: "max-content" }}
            >
              Start creating evaluations
            </Button>
          </Box>
        </Box>
      </ShowComponent>
    </EvalsWrapper>
  );
};

export default EvalsUsageView;
