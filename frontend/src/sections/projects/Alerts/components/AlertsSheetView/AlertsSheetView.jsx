import {
  Box,
  Button,
  Drawer,
  Grid,
  IconButton,
  Skeleton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import React, { lazy, Suspense, useEffect, useMemo, useState } from "react";
import BackButton from "src/sections/develop-detail/Common/BackButton";
import SvgColor from "src/components/svg-color";
import TracingControls from "src/sections/projects/LLMTracing/TracingControls";
import {
  getCompareChartConfig,
  getDefaultDateRange,
  getSimpleLineChartConfig,
} from "../../common";
import AlertsChart from "../AlertsChart";
import DuplicateAlert from "../DuplicateALert";
import { useQuery } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";
import { useMuteAlertsMutation } from "../../useMuteAlerMutation";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAlertStore } from "../../store/useAlertStore";
import { useAlertSheetView } from "../../store/useAlertSheetView";
import { AlertTableSkeleton } from "../AlertSkeletons";
import _ from "lodash";
import { isAlertMuted } from "../../common";
const Issues = lazy(() => import("./Issues"));
const AlertDetails = lazy(() => import("./AlertDetails"));

export default function AlertsSheetView() {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const {
    openCreateAlerts,
    openSheetView,
    handleCloseSheetView,
    handleProjectChange,
    handleStartCreatingAlerts,
    setCurrentTab,
    setOpenCreateAlerts,
    mainPage,
    refreshGrid: refreshMainGrid,
  } = useAlertStore();
  const {
    handleViewTrace,
    alertRuleDetails,
    refreshGrid,
    setAlertRuleDetails,
  } = useAlertSheetView();
  const [dateFilter, setDateFilter] = useState(getDefaultDateRange());
  const [duplicateModal, setDuplicateModal] = useState(false);
  const { mutate: mutateAlerts, isPending: isMutingAlerts } =
    useMuteAlertsMutation({
      onSuccessCallback: () => {
        refreshGrid();
        refreshMainGrid();
      },
    });
  const alertIsMuted = isAlertMuted(alertRuleDetails);
  const hasLoadedAlertDetails = Boolean(alertRuleDetails?.id);

  const handleDuplicateAlert = () => {
    setDuplicateModal(false);
    handleStartCreatingAlerts();
  };

  const { data } = useQuery({
    queryKey: ["alert-graph", openSheetView, dateFilter],
    queryFn: () =>
      axiosInstance.get(endpoints.project.getAlertGraph(openSheetView), {
        params: {
          start_date: dateFilter?.dateFilter[0],
          end_date: dateFilter?.dateFilter[1],
        },
      }),
    refetchInterval: 10 * 1000,
    enabled: !!openSheetView,
    select: (data) => data?.data,
  });

  const [chartKey] = useState(0);

  const selectedThresHoldType = useMemo(() => {
    if (!data?.result) return null;
    if (data?.result?.alert_bar_data) {
      return "percentage_change";
    } else {
      return "static";
    }
  }, [data]);

  const { series, options } = useMemo(() => {
    if (!selectedThresHoldType || !data) {
      return { series: [], options: {} };
    }

    try {
      let chartConfig;

      if (selectedThresHoldType === "static") {
        const values = data?.result?.map((res) => res?.value) || [];
        let max = values.length ? Math.max(...values, 100) : 100;
        let min = values.length ? Math.min(...values) : 0;

        min = Math.max(0, Math.floor(min * 0.9));
        max = Math.ceil(max * 1.1);

        const isLessThan = alertRuleDetails?.thresholdOperator === "less_than";

        const thresholds = isLessThan
          ? [
              {
                value: max,
                y2: alertRuleDetails?.warningThresholdValue,
                fillColor: "#00A25108",
                borderColor: "#00A251",
              },
              {
                value: alertRuleDetails?.warningThresholdValue,
                y2: alertRuleDetails?.criticalThresholdValue,
                fillColor: "#E9690C08",
                borderColor: "#F49A54",
              },
              {
                value: alertRuleDetails?.criticalThresholdValue,
                y2: min,
                fillColor: "#D92D200D",
                borderColor: "#D92D20",
              },
            ]
          : [
              {
                value: max,
                y2: alertRuleDetails?.criticalThresholdValue,
                fillColor: "#D92D200D",
                borderColor: "#D92D20",
              },
              {
                value: alertRuleDetails?.criticalThresholdValue,
                y2: alertRuleDetails?.warningThresholdValue,
                fillColor: "#E9690C08",
                borderColor: "#F49A54",
              },
              {
                value: alertRuleDetails?.warningThresholdValue,
                y2: min,
                fillColor: "#00A25108",
                borderColor: "#00A251",
              },
            ];

        chartConfig = getSimpleLineChartConfig(data, {
          seriesName: _.startCase(_.toLower(alertRuleDetails.metricType)),
          thresholds,
        });
      } else if (selectedThresHoldType === "percentage_change") {
        chartConfig = getCompareChartConfig(data, {
          seriesName: _.startCase(_.toLower(alertRuleDetails.metricType)),
        });
      }

      if (!chartConfig) return { series: [], options: {} };

      const cleanOptions = {
        ...chartConfig.options,
        annotations: {
          yaxis: chartConfig.options.annotations?.yaxis || [],
          xaxis: [],
          points: [],
        },
      };

      return {
        series: chartConfig.series,
        options: cleanOptions,
      };
    } catch (err) {
      return { series: [], options: {} };
    }
  }, [selectedThresHoldType, data, alertRuleDetails]);

  // Render chart with proper loading states
  const renderChart = () => {
    return (
      <AlertsChart
        key={`${selectedThresHoldType}-${chartKey}`}
        series={series}
        options={{
          ...options,
          xaxis: {
            type: "datetime",
            convertedCatToNumeric: false, // include this explicitly
            labels: {
              style: { fontSize: "12px", colors: isDark ? "#a1a1aa" : "#666" },
            },
          },
        }}
      />
    );
  };

  useEffect(() => {
    if (!alertRuleDetails?.id) return;
    trackEvent(Events.alertConfigLoaded, {
      [PropertyName.id]: alertRuleDetails.id,
    });
  }, [alertRuleDetails?.id]);

  const handleEditClick = () => {
    if (!alertRuleDetails?.id) {
      return;
    }
    if (mainPage && alertRuleDetails?.project) {
      handleProjectChange(alertRuleDetails.project);
    }
    trackEvent(Events.alertEditClicked, {
      [PropertyName.id]: alertRuleDetails.id,
    });
    setCurrentTab(1);
    setOpenCreateAlerts(true);
  };

  const handleViewTraceClick = () => {
    if (!alertRuleDetails?.id) return;
    trackEvent(Events.alertViewTracesClicked, {
      [PropertyName.id]: alertRuleDetails.id,
    });
    handleViewTrace();
    handleCloseSheetView();
    setAlertRuleDetails(null);
  };

  const handleToggleMute = () => {
    if (openSheetView) {
      trackEvent(Events.alertMuteClicked, {
        [PropertyName.list]: [openSheetView],
        [PropertyName.toggle]: alertIsMuted ? "Unmute" : "Mute",
        [PropertyName.source]: "alert_edit",
      });
      mutateAlerts({
        ids: [openSheetView],
        is_mute: !alertIsMuted,
      });
    }
  };
  return (
    <>
      <Drawer
        anchor={"bottom"}
        open={!!openSheetView && !openCreateAlerts}
        onClose={() => {
          if (mainPage) {
            handleProjectChange(null);
          }
          handleCloseSheetView();
          setAlertRuleDetails(null);
        }}
        PaperProps={{
          sx: {
            height: "100vh",
            maxHeight: "100vh",
            overflow: "auto",
            borderRadius: "0 !important",
            padding: theme.spacing(2, 2.5),
            paddingTop: 0,
          },
        }}
        ModalProps={{
          hideBackdrop: true,
        }}
      >
        <Stack direction={"column"}>
          <Stack
            sx={{
              position: "sticky",
              top: 0,
              backgroundColor: "background.paper",
              zIndex: 20,
              paddingTop: theme.spacing(2),
            }}
          >
            <Stack
              direction={"row"}
              justifyContent={"space-between"}
              alignItems={"center"}
              sx={{
                mb: 2,
              }}
            >
              <Stack direction={"row"} gap={1} alignItems={"center"}>
                <BackButton
                  onBack={() => {
                    if (mainPage) {
                      handleProjectChange(null);
                    }
                    handleCloseSheetView();
                    setAlertRuleDetails(null);
                  }}
                />
                <Typography
                  variant="m3"
                  color={"text.primary"}
                  fontWeight={"fontWeightSemiBold"}
                >
                  {alertRuleDetails?.name}
                </Typography>
              </Stack>
              <IconButton
                data-alert-sheet-action="close"
                onClick={() => {
                  if (mainPage) {
                    handleProjectChange(null);
                  }
                  handleCloseSheetView();
                  setAlertRuleDetails(null);
                }}
              >
                <SvgColor
                  sx={{
                    bgcolor: "text.primary",
                  }}
                  src="/assets/icons/ic_close.svg"
                />
              </IconButton>
            </Stack>
            <Stack
              direction={"row"}
              alignItems={"center"}
              justifyContent={"space-between"}
              sx={{
                mb: 2,
              }}
            >
              <Box
                sx={{
                  width: {
                    xs: "100%",
                    sm: "80%",
                    md: "50%",
                  },
                }}
              >
                <TracingControls
                  dateFilter={dateFilter}
                  setDateFilter={setDateFilter}
                />
              </Box>
              <Stack direction={"row"} alignItems={"center"} gap={1.5}>
                <Button
                  variant="outlined"
                  size="small"
                  data-alert-sheet-action={alertIsMuted ? "unmute" : "mute"}
                  startIcon={
                    alertIsMuted ? (
                      <SvgColor src="/assets/icons/ic_mute.svg" />
                    ) : (
                      <SvgColor src="/assets/icons/ic_unmute.svg" />
                    )
                  }
                  disabled={isMutingAlerts}
                  onClick={handleToggleMute}
                >
                  {alertIsMuted ? "Unmute" : "Mute"}
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  data-alert-sheet-action="duplicate"
                  startIcon={<SvgColor src="/assets/icons/ic_duplicate.svg" />}
                  onClick={() => setDuplicateModal(true)}
                >
                  Duplicate
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  data-alert-sheet-action="view-trace"
                  startIcon={<SvgColor src="/assets/icons/custom/eye.svg" />}
                  onClick={handleViewTraceClick}
                >
                  View Trace
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  data-alert-sheet-action="edit"
                  startIcon={<SvgColor src="/assets/icons/ic_edit.svg" />}
                  disabled={!hasLoadedAlertDetails}
                  onClick={handleEditClick}
                >
                  Edit Rule
                </Button>
              </Stack>
            </Stack>
          </Stack>
          {renderChart()}
          <Grid
            container
            sx={{
              borderTop: "1px solid",
              borderColor: "divider",
              backgroundColor: "background.paper",
              zIndex: 2,
              mb: 2,
            }}
          >
            <Grid
              item
              md={8.5}
              xs={12}
              sx={{
                borderRight: "1px solid",
                borderColor: "divider",
                minHeight: "400px",
                overflow: "auto",
              }}
            >
              {/* Left side - Table area */}
              <Box p={2}>
                <Suspense fallback={<AlertTableSkeleton />}>
                  <Issues />
                </Suspense>
              </Box>
            </Grid>

            <Grid
              item
              md={3.5}
              sx={{
                minHeight: "400px",
                overflow: "auto",
                backgroundColor: "background.paper",
              }}
            >
              {/* Right side - Details */}
              <Box p={2}>
                <Suspense
                  fallback={
                    <Skeleton
                      animation="wave"
                      variant="rounded"
                      width={"100%"}
                      height={500}
                    />
                  }
                >
                  <AlertDetails />
                </Suspense>
              </Box>
            </Grid>
          </Grid>
        </Stack>
      </Drawer>
      <DuplicateAlert
        open={duplicateModal}
        onClose={() => setDuplicateModal(false)}
        onAction={handleDuplicateAlert}
      />
    </>
  );
}
