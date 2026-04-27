/* eslint-disable react-hooks/exhaustive-deps */
import {
  Box,
  Button,
  ButtonGroup,
  CircularProgress,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
} from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import PerformanceTable from "./PerformanceTable";
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { useLocation, useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { useGetAllCustomMetrics } from "../../../../api/model/metric";
import PerformanceGraph from "./PerformanceGraph";
import {
  endOfToday,
  format,
  startOfToday,
  startOfYesterday,
  startOfTomorrow,
  sub,
} from "date-fns";
import { saveAs } from "file-saver";
import { useSnackbar } from "src/components/snackbar";
import PerformanceNoData from "./PerformanceNoData";
import ConfigureDatasetModal from "../ConfigureDatasetModal";
import ConfigureMetricModal from "../ConfigureMetricModal";
import { AggregationOption, DateRangeButtonOptions } from "src/utils/constants";
import { formatDate } from "src/utils/report-utils";
import "yet-another-react-lightbox/styles.css";
import { useGetModelDetail } from "src/api/model/model";
import PerformanceSidebar from "./PerformanceSidebar/PerformanceSidebar";
import { getRandomId } from "src/utils/utils";
import PerformanceDetailSection from "./PerformanceTableHeader";
import PerformanceTagDistribution from "./PerformanceTagDistribution";
import PerformanceGraphDatapoints from "./PerformanceGraphDatapoints";
import SaveReport from "./SaveReport";
import CustomDateRangePicker from "src/components/custom-datepicker/DatePicker";

// const formatDate = (date) => format(date, "yyyy-MM-dd HH:mm:ss");

const Performance = () => {
  const customDatePickerAnc = useRef(null);
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  const currentSnackbarData = useRef(null);

  const { id } = useParams();

  const { data: modelDetails } = useGetModelDetail(id);

  const { state } = useLocation();

  // @ts-ignore
  const isMetricAdded = modelDetails?.isMetricAdded;
  // @ts-ignore
  const isDatasetAdded = modelDetails?.isDatasetAdded;

  const isNoData = !isMetricAdded || !isDatasetAdded;

  const searchParams = new URLSearchParams(location.search);

  const { data: allCustomMetrics } = useGetAllCustomMetrics(id);

  const [dateFilter, setDateFilter] = useState(() => {
    if (state?.report?.startDate && state?.report?.endDate) {
      return [
        formatDate(new Date(state.report.startDate)),
        formatDate(new Date(state.report.endDate)),
      ];
    }
    return [
      formatDate(
        sub(new Date(), {
          months: 6,
        }),
      ),
      formatDate(endOfToday()),
    ];
  });
  const [dateOption, setDateOption] = useState("6M");

  const [isDatePickerOpen, setIsDatePickerOpen] = useState(false);

  const [selectedAggregation, setSelectedAggregation] = useState(() => {
    if (state?.report?.aggregation) {
      return state.report.aggregation;
    }
    return "daily";
  });

  const [isConfigureDatasetOpen, setIsConfigureDatasetOpen] =
    useState(!isDatasetAdded);

  const [selectedDataset, setSelectedDataset] = useState(0);

  const [orderOption, setOrderOption] = useState("latest");

  const [isDefineMetricOpen, setIsDefineMetricOpen] = useState(
    () => isDatasetAdded && !isMetricAdded,
  );

  const [isSaveReportOpen, setIsSaveReportOpen] = useState(false);

  const [selectedDatasets, setSelectedDatasets] = useState(() => {
    if (state?.report) {
      return state.report.datasets;
    }

    return [
      {
        id: getRandomId(),
        metricId:
          searchParams.get("metricId") || modelDetails?.defaultMetricId || "",
        environment: "",
        version: "",
        filters: [],
      },
    ];
  });

  const [selectedFilters, setSelectedFilters] = useState(() => {
    if (state?.report) {
      return state.report.filters;
    }
    return [];
  });

  const [selectedBreakdown, setSelectedBreakdown] = useState(() => {
    if (state?.report) {
      return state.report.breakdown;
    }
    return [];
  });

  const [selectedDetailTab, setSelectedDetailTab] = useState("data");

  const [selectedTagDistributionType, setSelectedTagDistributionType] =
    useState("all");

  const filterDatasets = (dataset) => {
    return (
      dataset.metricId.length > 0 &&
      dataset.environment.length > 0 &&
      dataset.version.length > 0
    );
  };

  const filterFilters = (filter) => {
    return filter.key.length > 0 && filter.values.length > 0;
  };

  const filterBreakdown = (breakdown) => {
    return breakdown.key.length > 0 && breakdown.keyId.length > 0;
  };

  const validatedDatasets = selectedDatasets
    .filter(filterDatasets)
    .map((dataset) => ({
      ...dataset,
      filters: dataset.filters.filter(filterFilters),
    }));
  const validatedFilters = selectedFilters.filter(filterFilters);
  const validatedBreakdown = selectedBreakdown.filter(filterBreakdown);

  const selectedDatasetObj = validatedDatasets?.[selectedDataset];

  useEffect(() => {
    if (!selectedDatasets?.[0]?.metricId && allCustomMetrics?.length) {
      setSelectedDatasets((datasets) =>
        datasets.map((dataset, idx) => {
          const obj = { ...dataset };
          if (idx === 0) {
            obj.metricId = allCustomMetrics?.[0]?.id;
          }
          return obj;
        }),
      );
    }
  }, [allCustomMetrics]);

  const { data: graphData, isLoading: isLoadingGraphData } = useQuery({
    queryKey: [
      "performance",
      validatedDatasets,
      validatedFilters,
      validatedBreakdown,
      selectedAggregation,
      dateFilter,
    ],
    queryFn: () =>
      axios.post(`${endpoints.performance.graphData}${id}/`, {
        startDate: dateFilter[0],
        endDate: dateFilter[1],
        aggBy: selectedAggregation,
        datasets: validatedDatasets,
        filters: validatedFilters,
        breakdown: validatedBreakdown,
      }),
    select: (d) => d.data,
    enabled: !isNoData && validatedDatasets.length > 0,
  });

  const { data: tagDistribution, isLoading: isLoadingTagDistribution } =
    useQuery({
      queryKey: [
        "performance-tag-distribution",
        validatedDatasets,
        selectedDatasetObj,
        selectedAggregation,
        dateFilter,
        selectedTagDistributionType,
      ],
      queryFn: () => {
        return axios.post(`${endpoints.performance.getTagDistribution(id)}`, {
          dataset: selectedDatasetObj,
          filters: validatedFilters,
          aggBy: selectedAggregation,
          startDate: dateFilter[0],
          endDate: dateFilter[1],
          graphType: selectedTagDistributionType,
        });
      },
      select: (d) => d.data?.result,
      enabled:
        !isNoData &&
        validatedDatasets.length > 0 &&
        selectedDetailTab === "tagDistribution",
    });

  const {
    data: performanceDetails,
    fetchNextPage,
    isLoading,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: [
      "performance-detail",
      selectedDatasetObj,
      validatedFilters,
      validatedBreakdown,
      selectedAggregation,
      dateFilter,
    ],
    queryFn: ({ pageParam }) => {
      return axios.post(`${endpoints.performance.tableData}${id}/`, {
        startDate: dateFilter[0],
        endDate: dateFilter[1],
        aggBy: selectedAggregation,
        dataset: selectedDatasetObj,
        filters: validatedFilters,
        breakdown: validatedBreakdown,
        page: pageParam ? pageParam : null,
        orderOption,
        limit: 15,
      });
    },
    getNextPageParam: (o) => (o.data.isNext === true ? o.data.page + 1 : null),
    initialPageParam: 1,
    enabled:
      !isNoData && Boolean(selectedDatasetObj) && selectedDetailTab === "data",
  });

  const tableData = useMemo(
    () =>
      performanceDetails?.pages.reduce(
        (acc, curr) => [...acc, ...curr.data.result],
        [],
      ) || [],
    [performanceDetails],
  );

  const selectedMetric = allCustomMetrics?.find(
    (cm) => cm.id === selectedDatasetObj?.metricId,
  );

  useEffect(() => {
    const totalProcessingCount =
      performanceDetails?.pages?.[0].data.processingCount || 0;
    if (totalProcessingCount) {
      if (
        totalProcessingCount !== currentSnackbarData?.current?.processingCount
      ) {
        if (currentSnackbarData?.current?.key) {
          closeSnackbar(currentSnackbarData?.current?.key);
        }
        const key = enqueueSnackbar({
          variant: "info",
          autoHideDuration: null,
          message: `Processing ${totalProcessingCount} record${totalProcessingCount > 1 ? "s" : ""}`,
        });
        currentSnackbarData.current = {
          key: key,
          processingCount: totalProcessingCount,
        };
      }
    }
  }, [performanceDetails]);

  useEffect(() => {
    return () => {
      if (currentSnackbarData?.current?.key)
        closeSnackbar(currentSnackbarData?.current?.key);
    };
  }, []);

  const { mutate: generateExport } = useMutation({
    mutationFn: () => {
      return axios.post(`${endpoints.performance.tableExport}${id}/`, {
        dataset: {
          startDate: dateFilter[0],
          endDate: dateFilter[1],
          aggBy: selectedAggregation,
        },
      });
    },
    onSuccess: (data) => {
      enqueueSnackbar("Metric Performance Exported", {
        variant: "success",
      });
      saveAs(new Blob([data.data], { type: "text/csv" }), "text.csv");
    },
  });

  const handleDataOptionChange = (newOption) => {
    let filter = null;
    switch (newOption) {
      case "Custom":
        setIsDatePickerOpen(true);
        return;
      case "Today":
        filter = [formatDate(startOfToday()), formatDate(startOfTomorrow())];
        break;
      case "Yesterday":
        filter = [formatDate(startOfYesterday()), formatDate(startOfToday())];
        break;
      case "7D":
        filter = [
          formatDate(
            sub(new Date(), {
              days: 7,
            }),
          ),
          formatDate(startOfTomorrow()),
        ];
        break;
      case "30D":
        filter = [
          formatDate(
            sub(new Date(), {
              days: 30,
            }),
          ),
          formatDate(startOfTomorrow()),
        ];
        break;
      case "3M":
        filter = [
          formatDate(
            sub(new Date(), {
              months: 3,
            }),
          ),
          formatDate(startOfTomorrow()),
        ];
        break;
      case "6M":
        filter = [
          formatDate(
            sub(new Date(), {
              months: 6,
            }),
          ),
          formatDate(startOfTomorrow()),
        ];
        break;
      case "12M":
        filter = [
          formatDate(
            sub(new Date(), {
              months: 12,
            }),
          ),
          formatDate(startOfTomorrow()),
        ];
        break;
      default:
        break;
    }
    if (filter) {
      setDateFilter(filter);

      // Convert to timestamps before calculation
      const startDate = new Date(filter[0]).getTime();
      const endDate = new Date(filter[1]).getTime();
      const diffInDays = (endDate - startDate) / (1000 * 60 * 60 * 24);

      // If difference is less than 2 days, set aggregation to hourly
      if (diffInDays < 2) {
        setSelectedAggregation("hourly");
      }

      // trackEvent(Events.metricPerformanceDateRangeChange, {
      //   "Old Start Date": dateFilter?.[0],
      //   "Old End Date": dateFilter?.[1],
      //   "New Start Date": filter[0],
      //   "New End Date": filter[1],
      // });
    }

    setDateOption(newOption);
  };

  const getDateOptionTitle = (option, isSelected) => {
    if (option === "Custom" && isSelected)
      return `${format(new Date(dateFilter[0]), "dd/MM/yyyy")} - ${format(new Date(dateFilter[1]), "dd/MM/yyyy")}`;

    return option;
  };

  if (isNoData)
    return (
      <Box sx={{ height: "100%", flex: 1, display: "flex" }}>
        <ConfigureDatasetModal
          open={isConfigureDatasetOpen}
          onClose={() => setIsConfigureDatasetOpen(false)}
        />
        <ConfigureMetricModal
          open={isDefineMetricOpen}
          onClose={() => setIsDefineMetricOpen(false)}
        />
        <PerformanceNoData />
      </Box>
    );

  return (
    <Box
      sx={{
        display: "flex",
        width: "100%",
        height: "calc(100vh - 140px)",
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          flex: 1,
          overflow: "auto",
          paddingY: 2.5,
          paddingLeft: 2.5,
          paddingRight: 1,
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
            <ButtonGroup variant="outlined" color="inherit" size="small">
              {DateRangeButtonOptions.map((option) => {
                const selected = dateOption === option.title;
                return (
                  <Button
                    sx={{
                      // Adjust flex behavior
                      backgroundColor: selected ? "action.hover" : undefined,
                      fontWeight: selected ? 600 : 400,

                      borderColor: "divider",
                      borderWidth: "1px",
                      px: 1.5, // Slightly more padding
                      minWidth: 0,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      bgcolor: selected ? "action.hover" : undefined,
                      "&:hover": {
                        bgcolor: selected ? "action.hover" : "transparent",
                        borderColor: "divider",
                        transition:
                          "background-color 0.2s ease, font-weight 0.2s ease, color 0.2s ease, border-color 0.2s ease",
                      },
                      height: "28px",
                    }}
                    ref={(ref) => {
                      if (option.title === "Custom")
                        customDatePickerAnc.current = ref;
                    }}
                    onClick={() => handleDataOptionChange(option.title)}
                    key={option.title}
                  >
                    {getDateOptionTitle(option.title, selected, dateFilter)}
                  </Button>
                );
              })}
            </ButtonGroup>
            <CustomDateRangePicker
              open={isDatePickerOpen}
              onClose={() => setIsDatePickerOpen(false)}
              anchorEl={customDatePickerAnc?.current}
              setDateFilter={setDateFilter}
              setDateOption={setDateOption}
            />
            <FormControl fullWidth size="small" sx={{ width: 266 }}>
              <InputLabel>Aggregation</InputLabel>
              <Select
                value={selectedAggregation}
                onChange={(e) => {
                  setSelectedAggregation(e.target.value);
                  // trackEvent(Events.metricPerformanceAggregationChange, {
                  //   "Old Aggregation": selectedAggregation,
                  //   "New Aggregation": newAggregation,
                  // });
                }}
                label="Aggregation"
              >
                {AggregationOption.map(({ label, value }) => (
                  <MenuItem key={value} value={value}>
                    {label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Box>
          <Box>
            <Button
              size="small"
              variant="soft"
              onClick={() => setIsSaveReportOpen(true)}
            >
              Save Report
            </Button>
          </Box>
        </Box>
        {isLoadingGraphData || !graphData ? (
          <Box
            sx={{
              height: "200px",
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
            }}
          >
            <CircularProgress size={22} />
          </Box>
        ) : (
          <PerformanceGraph data={graphData} />
        )}
        <PerformanceDetailSection
          orderOption={orderOption}
          setOrderOption={setOrderOption}
          generateExport={generateExport}
          datasets={validatedDatasets}
          selectedDataset={selectedDataset}
          setSelectedDataset={setSelectedDataset}
          selectedDetailTab={selectedDetailTab}
          setSelectedDetailTab={setSelectedDetailTab}
        >
          {selectedDetailTab === "data" && (
            <PerformanceTable
              tableData={tableData}
              fetchNextPage={fetchNextPage}
              isLoading={isLoading}
              isFetchingNextPage={isFetchingNextPage}
              setSelectedTags={() => {}}
              selectedMetric={selectedMetric}
            />
          )}
          {selectedDetailTab === "tagDistribution" && (
            <PerformanceTagDistribution
              tagDistribution={tagDistribution}
              selectedTagDistributionType={selectedTagDistributionType}
              setSelectedTagDistributionType={setSelectedTagDistributionType}
              isLoading={isLoadingTagDistribution}
            />
          )}
          {selectedDetailTab === "graphDatapoints" && (
            <PerformanceGraphDatapoints
              graphData={graphData}
              selectedDataset={selectedDataset}
            />
          )}
        </PerformanceDetailSection>
      </Box>
      <SaveReport
        open={isSaveReportOpen}
        onClose={() => setIsSaveReportOpen(false)}
        datasets={validatedDatasets}
        filters={validatedFilters}
        breakdown={validatedBreakdown}
        aggregation={selectedAggregation}
        startDate={dateFilter[0]}
        endDate={dateFilter[1]}
      />
      <PerformanceSidebar
        selectedDatasets={selectedDatasets}
        setSelectedDatasets={setSelectedDatasets}
        selectedFilters={selectedFilters}
        setSelectedFilters={setSelectedFilters}
        selectedBreakdown={selectedBreakdown}
        setSelectedBreakdown={setSelectedBreakdown}
      />
    </Box>
  );
};

Performance.propTypes = {};

export default Performance;
