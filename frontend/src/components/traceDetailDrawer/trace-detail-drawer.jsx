import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Box,
  Grid,
  Typography,
  Button,
  Drawer,
  LinearProgress,
  IconButton,
  Stack,
  Link,
} from "@mui/material";
import Iconify from "../iconify";
import TraceTree from "./trace-tree";
import DrawerRight from "./drawer-right";
import DrawerBottom from "./drawer-bottom";
import PropTypes from "prop-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { ShowComponent } from "../show";
import { useSelectedNode } from "./useSelectedNode";
import { enqueueSnackbar } from "notistack";
import AddDataset from "./addToDataset/add-dataset";
import AnnotateDrawer from "./AnnotateDrawer";
import { TraceDetailContext } from "./TraceDetailContext";
import AddAnnotationsDrawer from "./add-annotations-drawer";
import AnnotationSidebarContent from "./AnnotationSidebarContent";
import AddLabelDrawer from "./AddLabelDrawer";
import { buildTraceAnnotationSources } from "../voiceAnnotationSources";
import _ from "lodash";
import SvgColor from "../svg-color";
import { useTraceErrorAnalysis } from "./common";
import ErrorAnalysis from "./ErrorAnalysis";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useUrlState } from "src/routes/hooks/use-url-state";

const columnOptions = [
  { key: "latency", label: "Latency", visible: true },
  { key: "tokens", label: "Tokens", visible: true },
  { key: "cost", label: "Cost", visible: true },
  { key: "evals", label: "Evals", visible: true },
  { key: "annotations", label: "Annotations", visible: true },
  { key: "events", label: "Events", visible: true },
];

const TraceDetailDrawerChild = ({
  traceData,
  setTraceDetailDrawerOpen,
  viewOptions,
  setSelectedTraceId,
  setAnalysisExists,
  onAnnotationChanges,
}) => {
  const queryClient = useQueryClient();
  const [collapsed, setCollapsed] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [dragPosition, setDragPosition] = useState(63);
  const [showBottomSection] = useState(true);
  const [showRightSection] = useState(false);
  const [annotateRunDrawerOpen, setAnnotateRunDrawerOpen] = useState(null);
  const [annotationSidebarOpen, setAnnotationSidebarOpen] = useState(false);
  const [actionToDataset, setActionToDataset] = useState(false);
  const [configureAnnotationsDrawerOpen, setConfigureAnnotationsDrawerOpen] =
    useState(false);
  const [addLabelDrawerOpen, setAddLabelDrawerOpen] = useState(false);

  const { projectId, observeId, runId } = useParams();
  const isSpanNavigation = Boolean(
    traceData?.fromSpansView && traceData?.span_id,
  );

  const projectIdToUse = projectId || observeId;

  const { data: projectLabels } = useQuery({
    queryKey: ["project-annotations-labels", projectIdToUse],
    queryFn: () =>
      axios.get(endpoints.project.getAnnotationLabels(), {
        params: { project_id: projectIdToUse },
      }),
    select: (data) => data?.data?.results,
    staleTime: 1 * 60 * 1000,
  });

  const { mutate: addAnnotationValues } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.project.addAnnotationValuesForSpan(), data),
    onSuccess: () => {
      enqueueSnackbar(`Annotations has been added.`, {
        variant: "success",
      });
      setAnnotateRunDrawerOpen(false);
      queryClient.invalidateQueries({
        queryKey: ["span-annotation", selectedNode?.id],
      });
      onAnnotationChanges?.();
    },
  });
  const { selectedNode } = useSelectedNode();

  const { data: traceErrorAnalysis, isPending: isPendingTraceErrorAnalysis } =
    useTraceErrorAnalysis(traceData?.trace_id);

  useEffect(() => {
    if (!isPendingTraceErrorAnalysis) {
      setAnalysisExists(traceErrorAnalysis?.analysisExists);
      trackEvent(Events.observeTraceidClicked, {
        [PropertyName.id]: traceData?.trace_id,
        [PropertyName.toggle]: traceErrorAnalysis?.analysisExists,
      });
    }
  }, [
    isPendingTraceErrorAnalysis,
    traceData?.trace_id,
    traceErrorAnalysis?.analysisExists,
    setAnalysisExists,
  ]);

  const showInsights =
    viewOptions?.showInsights !== undefined ? viewOptions?.showInsights : true;

  const showNavigation =
    viewOptions?.showNavigation !== undefined
      ? viewOptions?.showNavigation
      : true;

  const showAnnotation =
    viewOptions?.showAnnotation !== undefined
      ? viewOptions?.showAnnotation
      : true;

  const _openAnnotateDrawer = (annotateSpan) => {
    if (!projectLabels.length) {
      setConfigureAnnotationsDrawerOpen(true);
      return;
    }
    setAnnotateRunDrawerOpen(annotateSpan);
  };
  const showEvalLoadingStates = viewOptions?.showEvalLoadingStates || false;

  const onAnnotateSubmit = (data) => {
    const { notes, ...rest } = data;

    //@ts-ignore
    addAnnotationValues({
      observation_span_id: selectedNode?.id,
      annotation_values: Object.fromEntries(
        Object.entries(rest).filter(([_, value]) => value !== ""),
      ),
      notes,
    });
  };

  const { data: traceDetail, isLoading } = useQuery({
    queryKey: ["trace-detail", traceData.trace_id],
    queryFn: () => {
      return axios.get(endpoints.project.getTrace(traceData.trace_id));
    },
    select: (data) => data.data?.result,
  });

  const rootSpanId = useMemo(
    () =>
      traceDetail?.observation_spans?.find(
        (entry) => !entry?.observation_span?.parent_span_id,
      )?.observation_span?.id ?? null,
    [traceDetail?.observation_spans],
  );

  const { data: previousNextTraceDataPrototype } = useQuery({
    queryKey: ["trace-id-by-index", traceData.trace_id, traceData?.filters],
    queryFn: () => {
      return axios.get(endpoints.project.getTraceIdByIndex(), {
        params: {
          project_version_id: runId,
          trace_id: traceData?.trace_id,
          // only trace filters can be applied to this
          filters: JSON.stringify(traceData?.filters || []),
        },
      });
    },
    select: (data) => data.data?.result,
    enabled: !!runId && !isSpanNavigation,
  });

  const { data: previousNextTraceDataObserve } = useQuery({
    queryKey: [
      "trace-id-by-index-observe",
      traceData.trace_id,
      traceData?.filters,
    ],
    queryFn: () => {
      return axios.get(endpoints.project.getTraceIdByIndexObserve(observeId), {
        params: {
          trace_id: traceData.trace_id,
          // only trace filters can be applied to this
          filters: JSON.stringify(traceData?.filters || []),
        },
      });
    },
    select: (data) => data.data?.result,
    enabled: !!observeId && !isSpanNavigation,
    meta: { errorHandled: true },
  });

  const { data: previousNextSpanDataPrototype } = useQuery({
    queryKey: ["span-id-by-index", traceData?.span_id, traceData?.filters],
    queryFn: () => {
      return axios.get(endpoints.project.getTraceIdByIndexSpansAsBase(), {
        params: {
          span_id: traceData?.span_id,
          project_version_id: runId,
          // only span filters can be applied to this
          filters: JSON.stringify(traceData?.filters || []),
        },
      });
    },
    select: (data) => data.data?.result,
    enabled: !!runId && isSpanNavigation,
  });

  const { data: previousNextSpanDataObserve } = useQuery({
    queryKey: [
      "span-id-by-index-observe",
      traceData?.span_id,
      traceData?.filters,
    ],
    queryFn: () => {
      return axios.get(
        endpoints.project.getTraceIdByIndexSpansAsObserve(observeId),
        {
          params: {
            span_id: traceData?.span_id,
            // only span filters can be applied to this
            filters: JSON.stringify(traceData?.filters || []),
          },
        },
      );
    },
    select: (data) => data.data?.result,
    enabled: !!observeId && isSpanNavigation,
  });

  const oldDummy = useRef(null);

  const fetch = useMemo(() => {
    const isMatched = traceDetail?.observation_spans.find(
      (item) => selectedNode?.id === item.observation_span.id,
    );
    const isDummy = isMatched?.observation_span?.metadata?.isDummy;
    const fetch = isMatched ? !isDummy : oldDummy.current ? false : true;
    if (oldDummy?.current) {
      oldDummy.current = false;
    } else if (isDummy) {
      oldDummy.current = isDummy;
    }
    return fetch;
  }, [selectedNode?.id, traceDetail?.observation_spans]);

  const {
    data: observationSpanWithoutLoadingState,
    isLoading: isLoadingObservationSpan,
  } = useQuery({
    queryKey: ["observationSpan", selectedNode?.id, fetch],
    enabled: Boolean(selectedNode?.id) && !showEvalLoadingStates && fetch,
    queryFn: () =>
      axios.get(endpoints.project.getObservationSpan(selectedNode?.id)),
    select: (data) => data?.data?.result,
  });

  const {
    data: observationSpanWithLoadingState,
    isLoading: isLoadingDetailedObservationSpan,
  } = useQuery({
    queryKey: ["observationSpan-loading", selectedNode?.id, fetch],
    enabled: Boolean(selectedNode?.id) && showEvalLoadingStates && fetch,
    queryFn: () =>
      axios.get(endpoints.project.getObservationSpan(selectedNode?.id)),
    select: (data) => data?.data?.result,
    refetchInterval: (data) => {
      const evalsMetrics = data?.state?.data?.data?.result?.evals_metrics;
      if (!evalsMetrics) return false;

      const isLoadingAny = Object.values(evalsMetrics).some((e) => e.loading);
      if (isLoadingAny) {
        return 10000;
      }
      return false;
    },
  });

  const observationSpan = showEvalLoadingStates
    ? observationSpanWithLoadingState
    : observationSpanWithoutLoadingState;

  const observationSpanLoading = showEvalLoadingStates
    ? isLoadingDetailedObservationSpan
    : isLoadingObservationSpan;

  const previousNextTraceData = isSpanNavigation
    ? previousNextSpanDataPrototype || previousNextSpanDataObserve
    : previousNextTraceDataPrototype || previousNextTraceDataObserve;

  const navigateToNextRecord = (nextId) => {
    if (!nextId) {
      return;
    }
    if (typeof setTraceDetailDrawerOpen === "function") {
      setTraceDetailDrawerOpen((prev) =>
        prev && typeof prev === "object" ? { ...prev, traceId: nextId } : prev,
      );
    }
    if (typeof setSelectedTraceId === "function") {
      setSelectedTraceId(nextId);
    }
  };

  const isOverallLoading = isLoading;

  const MIN_HEIGHT = 10;
  const MAX_HEIGHT = 94;

  const onMouseDown = () => {
    setDragging(true);
    document.body.style.cursor = "grabbing";
  };

  const onMouseMove = useCallback(
    (e) => {
      if (dragging) {
        const newPosition = (e.clientY / window.innerHeight) * 100;
        const clampedPosition = Math.max(
          MIN_HEIGHT,
          Math.min(newPosition, MAX_HEIGHT),
        );
        setDragPosition(clampedPosition);
      }
    },
    [dragging],
  );

  const onMouseUp = useCallback(() => {
    setDragging(false);
    document.body.style.cursor = "default";
  }, []);

  useEffect(() => {
    if (dragging) {
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    } else {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    }

    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [dragging, onMouseMove, onMouseUp]);

  const handleCollapse = () => {
    setCollapsed((prevState) => !prevState);
  };

  const topSectionHeight = showRightSection
    ? 100
    : collapsed
      ? 94
      : dragPosition;
  const bottomSectionHeight =
    showRightSection || collapsed ? 6 : 100 - dragPosition;

  if (isOverallLoading) {
    return (
      <Box>
        <LinearProgress />
      </Box>
    );
  }

  const handleClose = () => setActionToDataset(false);

  return (
    <TraceDetailContext.Provider
      value={{
        configureAnnotationsDrawerOpen,
        setConfigureAnnotationsDrawerOpen,
        addLabelDrawerOpen,
        setAddLabelDrawerOpen,
      }}
    >
      <Box
        sx={{
          width: "100%",
          height: "100vh",
          display: "flex",
          flexDirection: "row",
          backgroundColor: "background.default",
          overflowY: "auto",
        }}
      >
        <Grid
          container
          sx={{ flex: 1, height: "100%", bgcolor: "background.paper" }}
        >
          {/* Adjust column width based on right section visibility */}
          <ShowComponent
            condition={traceErrorAnalysis?.analysisExists && showInsights}
          >
            <Grid
              item
              xs={12}
              sx={{
                px: 3,
                py: 2,
              }}
            >
              <ErrorAnalysis
                traceId={traceData?.trace_id}
                traceDetail={traceDetail}
              />
            </Grid>
          </ShowComponent>
          <Grid
            item
            xs={showRightSection ? 4 : 5}
            sx={{
              height: `${topSectionHeight}%`,
              ...(dragging === false && {
                transition: "ease-in-out 0.3s",
              }),
            }}
          >
            <Box
              // elevation={3}
              // variant="outlined"
              sx={{
                height: "100%",
                overflowY: "auto",
                borderRadius: 0,
                "&::-webkit-scrollbar": {
                  width: "6px",
                },
                "&::-webkit-scrollbar-thumb": {
                  backgroundColor: "rgba(0, 0, 0, 0.3)",
                  borderRadius: "3px",
                },
                "&::-webkit-scrollbar-track": {
                  backgroundColor: "transparent",
                },
                backgroundColor: "background.paper",
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: 3,
                  paddingBottom: 0,
                  paddingTop: 1.2,
                }}
              >
                <Box>
                  <Typography typography="m3" sx={{ fontWeight: 600 }}>
                    Trace Details
                  </Typography>
                  <Box display="flex" alignItems="center">
                    <Typography typography="s2_1">
                      Span Kinds denote the possible types of spans you might
                      capture.{" "}
                      <Link
                        href="https://docs.futureagi.com/docs/tracing/manual/instrument-with-traceai-helpers#fi-span-kinds"
                        underline="always"
                        color="blue.500"
                        target="_blank"
                        rel="noopener noreferrer"
                        fontWeight="fontWeightMedium"
                      >
                        Learn more
                      </Link>
                    </Typography>
                  </Box>
                </Box>

                <ShowComponent condition={showNavigation}>
                  <Box sx={{ display: "flex", gap: 2 }}>
                    <Button
                      variant="outlined"
                      sx={{
                        color: "text.primary",
                        borderColor: "divider",
                        display: "flex",
                        alignItems: "center",
                        gap: 2,
                        height: "28px",
                        width: "fit-content",
                        fontWeight: 400,
                        fontSize: "14px",
                      }}
                      disabled={!previousNextTraceData?.previousTraceId}
                      onClick={() =>
                        navigateToNextRecord(
                          previousNextTraceData?.previousTraceId,
                        )
                      }
                    >
                      Prev
                      <Iconify
                        icon="solar:alt-arrow-up-line-duotone"
                        color={
                          previousNextTraceData?.previousTraceId
                            ? "text.primary"
                            : "divider"
                        }
                        width={24}
                      />
                    </Button>
                    <Button
                      variant="outlined"
                      sx={{
                        color: "text.primary",
                        borderColor: "divider",
                        display: "flex",
                        alignItems: "center",
                        gap: 2,
                        height: "28px",
                        width: "fit-content",
                        fontWeight: 400,
                        fontSize: "14px",
                      }}
                      disabled={!previousNextTraceData?.nextTraceId}
                      onClick={() =>
                        navigateToNextRecord(previousNextTraceData?.nextTraceId)
                      }
                    >
                      Next
                      <Iconify
                        icon="solar:alt-arrow-down-line-duotone"
                        color={
                          previousNextTraceData?.nextTraceId
                            ? "text.primary"
                            : "divider"
                        }
                        width={24}
                      />
                    </Button>
                  </Box>
                </ShowComponent>
              </Box>
              <Box
                sx={{
                  flex: 1,
                  paddingTop: 2,
                }}
              >
                <TraceTree
                  treeData={traceDetail?.observation_spans || []}
                  defaultSelectedSpanId={traceData?.span_id}
                  columnOptionItems={columnOptions}
                />
              </Box>
            </Box>
          </Grid>
          <Grid
            item
            xs={showRightSection ? 4 : 7}
            sx={{
              height: `${topSectionHeight}%`,
              ...(dragging === false && {
                transition: "ease-in-out 0.3s",
              }),
            }}
          >
            <Box
              // elevation={3}
              // variant="outlined"
              sx={{
                height: "100%",
                borderRadius: 0,
                overflow: "auto",
                backgroundColor: "background.paper",
                borderLeft: "1px solid ",
                borderColor: "divider",
              }}
            >
              <DrawerRight
                observationSpanLoading={observationSpanLoading}
                observationSpan={observationSpan}
                setActionToDataset={setActionToDataset}
                onAnnotate={() => setAnnotationSidebarOpen(true)}
              />
            </Box>
          </Grid>

          <AddDataset
            handleClose={handleClose}
            actionToDataset={actionToDataset}
            spanId={selectedNode?.id}
          />

          {/* Conditionally render right section */}
          {showRightSection && (
            <Grid item xs={4} sx={{ height: "100%" }}>
              <Box
                // elevation={3}
                // variant="outlined"
                sx={{
                  height: "100%",
                  borderRadius: 0,
                  backgroundColor: "background.paper",
                  borderLeft: "1px solid ",
                  borderColor: "divider",
                }}
              >
                <Box sx={{ padding: 3 }}>
                  <DrawerBottom
                    traceData={traceDetail}
                    showAnnotation={showAnnotation}
                    observationSpan={observationSpan}
                    observationSpanLoading={observationSpanLoading}
                  />
                </Box>
              </Box>
            </Grid>
          )}

          {/* Bottom Section */}
          {showBottomSection && (
            <Grid
              item
              xs={12}
              sx={{
                height: `${bottomSectionHeight}%`,
                zIndex: 3,
                ...(dragging === false && {
                  transition: "ease-in-out 0.3s",
                }),
              }}
            >
              <Stack
                direction="row"
                sx={{
                  position: "relative",
                  top: -11,
                }}
              >
                {!showRightSection && (
                  <Box
                    sx={{
                      position: "absolute",
                      left: "91%",
                      boxShadow: "3",
                      borderRadius: "50%",
                      bgcolor: "background.paper",
                      height: "23px",
                      width: "23px",
                      ...(dragging === false && {
                        transition: "ease-in-out 0.3s",
                      }),
                      cursor: "pointer",
                      zIndex: 4,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                    onClick={handleCollapse} // Toggle collapse on click
                  >
                    <Iconify
                      icon="bi:arrows-collapse"
                      width={15}
                      height={15}
                      color="text.secondary"
                    />
                  </Box>
                )}
                <Box
                  sx={{
                    position: "absolute",
                    boxShadow: "3",
                    borderRadius: "50%", // This makes it a circle
                    backgroundColor: dragging
                      ? "background.neutral"
                      : "background.paper",
                    height: "23px",
                    width: "23px",
                    left: "2.5%",
                    ...(dragging === false && {
                      transition: "ease-in-out 0.3s",
                    }),
                    cursor: dragging ? "grabbing" : "grab",
                    zIndex: 4,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    userSelect: "none",
                  }}
                  onMouseDown={onMouseDown} // Start dragging
                >
                  <SvgColor
                    src="/icons/tracedetails/drawer-slide_icon.svg"
                    sx={{
                      width: "12px",
                      height: "12px",
                      color: "text.secondary",
                    }}
                  />
                </Box>
              </Stack>
              <Box
                // elevation={3}
                // variant="outlined"
                sx={{
                  height: "100%",
                  borderRadius: 0,
                  backgroundColor: "background.paper",
                  borderTop: "1px solid ",
                  borderColor: "divider",
                  overflowY: "hidden",
                }}
              >
                <DrawerBottom
                  traceData={traceDetail}
                  showAnnotation={showAnnotation}
                  observationSpan={observationSpan}
                  observationSpanLoading={observationSpanLoading}
                />
              </Box>
            </Grid>
          )}
        </Grid>
        <AnnotateDrawer
          open={Boolean(annotateRunDrawerOpen)}
          onClose={() => setAnnotateRunDrawerOpen(null)}
          observationName={annotateRunDrawerOpen?.spanName}
          observationType={annotateRunDrawerOpen?.observation_type}
          runName=""
          onSubmit={onAnnotateSubmit}
          projectId={projectId || observeId}
        />

        {/* Annotation sidebar drawer */}
        <Drawer
          anchor="right"
          open={annotationSidebarOpen}
          onClose={() => setAnnotationSidebarOpen(false)}
          PaperProps={{
            sx: {
              width: 420,
              backgroundColor: "background.paper",
            },
          }}
          ModalProps={{
            BackdropProps: {
              style: { backgroundColor: "transparent" },
            },
          }}
        >
          <AnnotationSidebarContent
            sources={buildTraceAnnotationSources({
              traceId: traceData?.trace_id,
              spanId: selectedNode?.id || rootSpanId,
              sessionId: traceDetail?.trace?.session,
            })}
            onClose={() => setAnnotationSidebarOpen(false)}
            onAddLabel={() => setAddLabelDrawerOpen(true)}
            onScoresChanged={() => {
              onAnnotationChanges?.();
              queryClient.invalidateQueries({
                queryKey: ["span-annotation", selectedNode?.id],
              });
              queryClient.invalidateQueries({
                queryKey: ["annotation-queues", "for-source"],
              });
            }}
          />
        </Drawer>

        {/* collapse button */}

        <AddAnnotationsDrawer
          open={configureAnnotationsDrawerOpen}
          onClose={() => setConfigureAnnotationsDrawerOpen(false)}
          projectId={projectIdToUse}
          onAnnotationChanges={onAnnotationChanges}
          onAnnotateClick={() => {
            if (!projectLabels.length) {
              enqueueSnackbar("Please create an annotation label first", {
                variant: "warning",
              });
              return;
            }
            setConfigureAnnotationsDrawerOpen(false);
            setAnnotateRunDrawerOpen({
              observationType: _.capitalize(selectedNode?.observation_type),
              spanName: selectedNode?.name,
            });
          }}
        />

        <AddLabelDrawer
          open={addLabelDrawerOpen}
          onClose={() => setAddLabelDrawerOpen(false)}
          projectId={projectIdToUse}
          onLabelsChanged={() => {
            onAnnotationChanges?.();
            queryClient.invalidateQueries({
              queryKey: ["annotation-queues", "for-source"],
            });
          }}
        />
      </Box>
    </TraceDetailContext.Provider>
  );
};

TraceDetailDrawerChild.propTypes = {
  traceData: PropTypes.object,
  setTraceDetailDrawerOpen: PropTypes.func,
  viewOptions: PropTypes.object,
  setSelectedTraceId: PropTypes.func,
  setAnalysisExists: PropTypes.func,
  onAnnotationChanges: PropTypes.func,
};

const TraceDetailDrawer = ({
  open,
  onClose,
  traceData,
  setTraceDetailDrawerOpen,
  setSelectedTraceId,
  viewOptions,
  onAnnotationChanges,
}) => {
  const [_, setAnalysisExists] = useUrlState("analysisExists", false);
  const handleClose = () => {
    setAnalysisExists(null);
    onClose();
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          overflowY: "hidden",
          zIndex: 9999,
          borderRadius: "10px",
          backgroundColor: "background.paper",
          width: "95vw",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <Box sx={{ position: "absolute", top: 10, right: 10, zIndex: 1 }}>
        <IconButton onClick={handleClose}>
          <Iconify icon="mingcute:close-line" />
        </IconButton>
      </Box>
      {traceData && (
        <TraceDetailDrawerChild
          traceData={traceData}
          setTraceDetailDrawerOpen={setTraceDetailDrawerOpen}
          viewOptions={viewOptions}
          setSelectedTraceId={setSelectedTraceId}
          onAnnotationChanges={onAnnotationChanges}
          setAnalysisExists={setAnalysisExists}
        />
      )}
    </Drawer>
  );
};

TraceDetailDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  traceData: PropTypes.object,
  setTraceDetailDrawerOpen: PropTypes.func,
  viewOptions: PropTypes.object,
  setSelectedTraceId: PropTypes.func,
  onAnnotationChanges: PropTypes.func,
};

export default TraceDetailDrawer;
