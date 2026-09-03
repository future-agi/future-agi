import { Box, Drawer, Grid, Skeleton, Stack, Typography } from "@mui/material";
import React, {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTestDetailSideDrawerStoreShallow } from "../states";
import { ShowComponent } from "../../../components/show";
import PropTypes from "prop-types";
import TestDetailDrawerScenarioTable from "./TestDetailDrawerScenarioTable";
import TestDetailDrawerRightSection from "./TestDetailDrawerRightSection";
import CustomCallLogHeader from "src/components/CallLogsDrawer/CustomCallLogHeader";
import RightSection from "src/components/CallLogsDetailDrawer/RightSection";
import LeftSection from "src/components/CallLogsDetailDrawer/LeftSection";
import AudioPlayerCustom from "./AudioPlayerCustom";
import { useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { transformMetricDetails } from "src/sections/agents/CallLogs/utils";
import { enqueueSnackbar } from "notistack";
import { deepEqual } from "src/utils/utils";
import { useUrlState } from "src/routes/hooks/use-url-state";
import { stripUiFilterKeys } from "src/components/ComplexFilter/common";
import SkeltonForTestDeatilDrawer from "../Skeletons/SkeltonForTestDeatilDrawer";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { HeaderSkeleton } from "./BasLineCompare/Skeletons";
import BaseLineVsReplay from "./BasLineCompare/BaseLineVsReplay";
import AnnotationSidebarContent from "src/components/traceDetailDrawer/AnnotationSidebarContent";
import AddLabelDrawer from "src/components/traceDetailDrawer/AddLabelDrawer";
import useTestRunDetails from "src/hooks/useTestRunDetails";
import { useParams } from "react-router";
import {
  useCallExecutionDetail,
  useVoiceCallDetail,
} from "src/sections/agents/helper";
import VoiceDetailDrawerV2 from "src/components/VoiceDetailDrawerV2";
import ChatDetailDrawerV2 from "src/components/ChatDetailDrawerV2";
import { buildVoiceCallAnnotationSources } from "src/components/voiceAnnotationSources";
import {
  canNavigateToNextVoiceCallDetail,
  createVoiceCallDetailCursorNavigator,
  createVoiceCallDetailRequestGuard,
  getVoiceCallRowIdentity,
  getLegacyVoiceCallNavigationTotal,
} from "./voiceCallDetailCursorNavigation";
import { LIST_CURSOR_CONTINUATION_NOTICE } from "src/sections/projects/LLMTracing/listCursorPagination";

const BaselineVsReplayHeader = lazy(() => import("./BasLineCompare/Header"));

const TestDetailSideDrawerChild = ({
  data,
  drawerQueryKey,
  onClose,
  urlOrigin,
  urlModule,
}) => {
  const { setTestDetailDrawerOpen, setCompareReplay, compareReplay } =
    useTestDetailSideDrawerStoreShallow((state) => ({
      setTestDetailDrawerOpen: state.setTestDetailDrawerOpen,
      setCompareReplay: state.setCompareReplay,
      compareReplay: state.compareReplay,
    }));
  const [urlRowIndex, setUrlRowIndex] = useUrlState("rowIndex");
  const { rowIndex: updatedRowIndex } = urlRowIndex || {};

  const [isFetching, setIsFetching] = useState(null);
  const [annotationSidebarOpen, setAnnotationSidebarOpen] = useState(false);
  const [addLabelDrawerOpen, setAddLabelDrawerOpen] = useState(false);
  const projectVoiceNavigatorRef = useRef(null);
  const projectVoiceNavigatorSignatureRef = useRef(null);
  const navigationRequestGuardRef = useRef(null);
  const navigationContextSignatureRef = useRef(null);
  if (!navigationRequestGuardRef.current) {
    navigationRequestGuardRef.current = createVoiceCallDetailRequestGuard();
  }
  useEffect(() => {
    const requestGuard = navigationRequestGuardRef.current;
    requestGuard.activate();
    return () => requestGuard.dispose();
  }, []);
  const queryClient = useQueryClient();
  const {
    testId: urlTestId,
    agentDefinitionId: urlAgentDefinitionId,
    observeId,
  } = useParams();
  const { data: testRunData } = useTestRunDetails(
    urlModule === "simulate" ? urlTestId : null,
  );
  const resolvedAgentDefinitionId =
    urlAgentDefinitionId ||
    testRunData?.agent_definition ||
    data?.agent_definition_used_id;
  const resolvedProjectId = observeId || data?.project_id;

  const traceId = data?.trace_id || data?.id;
  // For simulate runs, `simulation_call_type` is authoritative — a chat
  // simulation (`"text"`) must never render the voice drawer. The backend's
  // `get_call_type` serializer defaults to `"Inbound"` for every row, so
  // leaning on `call_type != null` alone sends chat sims into the voice UI
  // (empty transcript, "no recording", voice-only metrics).
  const isVoiceCall =
    data?.simulation_call_type !== AGENT_TYPES.CHAT &&
    (data?.call_type != null ||
      data?.observation_span?.[0]?.observation_type === "conversation" ||
      data?.simulation_call_type === "voice");
  const { data: voiceDetail, isLoading: isVoiceDetailLoading } =
    useVoiceCallDetail(
      traceId,
      urlModule === "project" && isVoiceCall && !!traceId,
    );

  // Fetch full call execution detail for simulate calls. The list response
  // strips `transcript` when `detail_mode=false` (see
  // simulate/serializers/test_execution.py::get_transcript), so both voice
  // and chat simulations need the per-row detail fetch — otherwise chat
  // rows render with an empty transcript and the drawer shows
  // "No transcript available".
  const isSimulate =
    urlModule === "simulate" ||
    urlOrigin === "simulate" ||
    urlOrigin === "agent-definition";
  const isChatSim = data?.simulation_call_type === AGENT_TYPES.CHAT;
  const needsDetailFetch = isSimulate && (isVoiceCall || isChatSim);
  const { data: callExecDetail, isLoading: _isCallExecDetailLoading } =
    useCallExecutionDetail(data?.id, needsDetailFetch && !!data?.id);

  // When `voiceDetail` or `callExecDetail` arrives async, the transcript
  // shape can differ from the list-response shape (e.g. the list row has
  // `duration` per turn but the detail response drops it). A naive
  // override then wipes out fields the transcript view relies on for
  // silence/interrupt math, producing the "appears for a second then
  // disappears" bug. To avoid it, we deep-merge transcript arrays by
  // index so fields from each source add up instead of replacing.
  const mergedData = useMemo(() => {
    const base = {
      ...data,
      module: urlModule,
      origin: urlOrigin,
      project_id: resolvedProjectId,
    };
    const mergeTranscripts = (a, b) => {
      if (!Array.isArray(b)) return a;
      if (!Array.isArray(a)) return b;
      const len = Math.max(a.length, b.length);
      const out = new Array(len);
      for (let i = 0; i < len; i++) {
        out[i] = { ...(a[i] || {}), ...(b[i] || {}) };
      }
      return out;
    };
    if (voiceDetail) {
      return {
        ...base,
        ...voiceDetail,
        transcript: mergeTranscripts(base.transcript, voiceDetail.transcript),
      };
    }
    if (callExecDetail) {
      return {
        ...base,
        ...callExecDetail,
        transcript: mergeTranscripts(
          base.transcript,
          callExecDetail.transcript,
        ),
      };
    }
    return base;
  }, [
    data,
    voiceDetail,
    callExecDetail,
    urlModule,
    urlOrigin,
    resolvedProjectId,
  ]);

  const {
    totalCount,
    totalCountIsLowerBound,
    pageMap,
    currentPage: _currentPage,
    standardPageLimit,
  } = useMemo(() => {
    const queryCache = queryClient.getQueryCache();
    const allQueries = queryCache.getAll();

    // Project call-list query keys end in `[visiblePage, transportRevision]`.
    // The drawer receives the revision-less key, so compare only the stable
    // query-shaping prefix and recover all already-cached visible pages.
    const queryPrefix =
      urlModule === "project" ? drawerQueryKey?.slice(0, 5) : drawerQueryKey;
    const compareLength = queryPrefix?.length;

    const matchingQueries = allQueries.filter((query) => {
      if (
        !Array.isArray(query.queryKey) ||
        query.queryKey.length < compareLength
      )
        return false;

      for (let i = 0; i < compareLength; i++) {
        if (!deepEqual(query.queryKey[i], queryPrefix[i])) {
          return false;
        }
      }

      return true;
    });
    const latestQuery = matchingQueries.reduce(
      (latest, query) =>
        !latest ||
        (query.state?.dataUpdatedAt || 0) > (latest.state?.dataUpdatedAt || 0)
          ? query
          : latest,
      null,
    );
    const latestPayload =
      latestQuery?.state?.data?.data?.result ||
      latestQuery?.state?.data?.data ||
      latestQuery?.state?.data?.result ||
      latestQuery?.state?.data;
    const totalCount = latestPayload?.count;
    const totalCountIsLowerBound = latestPayload?.count_is_lower_bound === true;

    const pageMap = new Map();
    const pageUpdatedAt = new Map();
    matchingQueries.forEach((query) => {
      const pageNum =
        urlModule === "project"
          ? query.queryKey[5]
          : query.queryKey[query.queryKey.length - 1];
      const payload =
        query.state.data?.data?.result ||
        query.state.data?.data ||
        query.state.data?.result ||
        query.state.data;
      const results = payload?.results || [];
      if (results.length > 0) {
        const updatedAt = query.state.dataUpdatedAt || 0;
        if ((pageUpdatedAt.get(pageNum) || -1) <= updatedAt) {
          pageMap.set(pageNum, results);
          pageUpdatedAt.set(pageNum, updatedAt);
        }
      }
    });

    const standardPageLimit =
      urlModule === "project"
        ? drawerQueryKey[3]
        : urlOrigin === "agent-definition"
          ? drawerQueryKey[4]
          : urlModule === "simulate"
            ? 30
            : 10;
    const currentPage =
      updatedRowIndex !== undefined
        ? Math.floor(updatedRowIndex / standardPageLimit) + 1
        : drawerQueryKey[drawerQueryKey.length - 1] || 1;
    return {
      totalCount,
      totalCountIsLowerBound,
      pageMap,
      currentPage,
      standardPageLimit,
    };
  }, [queryClient, drawerQueryKey, updatedRowIndex, urlModule, urlOrigin]);

  const navigationContextSignature = JSON.stringify({
    drawerQueryKey,
    rowIdentity: getVoiceCallRowIdentity(data),
    rowIndex: updatedRowIndex,
    urlModule,
    urlOrigin,
  });
  if (navigationContextSignatureRef.current !== navigationContextSignature) {
    navigationContextSignatureRef.current = navigationContextSignature;
    navigationRequestGuardRef.current.invalidate();
  }
  useEffect(() => {
    setIsFetching(null);
  }, [navigationContextSignature]);

  const projectVoiceNavigatorParams = drawerQueryKey[4] || {};
  const projectVoiceNavigatorSignature = JSON.stringify({
    pageSize: standardPageLimit,
    params: projectVoiceNavigatorParams,
  });

  const closeDetailDrawer = () => {
    navigationRequestGuardRef.current.invalidate();
    onClose();
  };

  const getPageNumberForIndex = (index) => {
    const pageSize = standardPageLimit;
    return Math.floor(index / pageSize) + 1;
  };

  const getRowFromPageMap = (index, pageMap) => {
    const pageSize = standardPageLimit;
    const pageNum = getPageNumberForIndex(index);
    const indexInPage = index % pageSize;
    const pageData = pageMap.get(pageNum);
    return pageData?.[indexInPage];
  };

  const fetchPage = async (pageNum) => {
    const id = drawerQueryKey[1];

    // Project tracing reads are cursor-only and go through
    // `fetchRowAtIndex`. Keep this numbered transport isolated to the legacy
    // simulation/agent-definition APIs so a future call site cannot
    // accidentally reintroduce a deep-page list_voice_calls request.
    if (urlModule === "project") {
      throw new Error("Project call navigation requires an exact cursor");
    }

    const simulateFilters =
      urlModule === "simulate"
        ? JSON.stringify(
            stripUiFilterKeys(
              Array.isArray(drawerQueryKey[3]) ? drawerQueryKey[3] : [],
            ),
          )
        : JSON.stringify([]);

    let endpoint = endpoints.testExecutions.list(id);

    const agentVersion = drawerQueryKey[3] ?? "";
    const agentId = drawerQueryKey[2] ?? "";

    if (urlOrigin === "agent-definition") {
      endpoint = endpoints.agentDefinitions.getCallLogs(agentId, agentVersion);
    }

    const newQueryKey = [...drawerQueryKey, pageNum];
    const simulateSearch = drawerQueryKey[2] ?? "";
    const query = {
      queryKey: newQueryKey,
      queryFn: () =>
        axios.get(endpoint, {
          params: {
            page: pageNum,
            // simulate endpoints use "limit", observe/agent use "page_size"
            ...(urlModule === "simulate" && urlOrigin !== "agent-definition"
              ? { limit: standardPageLimit || 30 }
              : { page_size: standardPageLimit || 30 }),
            ...(urlModule === "simulate" &&
              urlOrigin !== "agent-definition" && {
                filters: simulateFilters,
                search: simulateSearch,
              }),
          },
        }),
      staleTime: Infinity,
      gcTime: Infinity,
      meta: { errorHandled: true },
    };

    return await queryClient.fetchQuery(query);
  };

  const getProjectVoiceNavigator = () => {
    if (
      projectVoiceNavigatorSignatureRef.current !==
      projectVoiceNavigatorSignature
    ) {
      projectVoiceNavigatorSignatureRef.current =
        projectVoiceNavigatorSignature;
      projectVoiceNavigatorRef.current = createVoiceCallDetailCursorNavigator({
        baseParams: projectVoiceNavigatorParams,
        pageSize: standardPageLimit,
        request: (requestParams, requestOptions) =>
          axios
            .get(endpoints.project.getCallLogs, {
              params: requestParams,
              ...(requestOptions || {}),
            })
            .then((response) => response.data),
      });
    }
    return projectVoiceNavigatorRef.current;
  };

  const fetchRowAtIndex = async (index) => {
    if (urlModule === "project") {
      return getProjectVoiceNavigator().loadRow(index);
    }

    const pageNum = getPageNumberForIndex(index);
    const response = await fetchPage(pageNum);
    const results = response?.data?.results ?? [];
    return {
      row: results[index % standardPageLimit] ?? null,
      pending: false,
      terminal: results.length < standardPageLimit,
    };
  };

  useEffect(() => {
    if (updatedRowIndex === null || updatedRowIndex === undefined) return;

    if (data?.ignoreCache) {
      return;
    }
    const parsedRowIndex = parseInt(updatedRowIndex);

    const pageNum = getPageNumberForIndex(parsedRowIndex);

    if (pageMap.get(pageNum)) {
      const rowData = getRowFromPageMap(parsedRowIndex, pageMap);

      if (rowData) {
        let transformedData = rowData;
        if (urlOrigin === "agent-definition") {
          const { metricDetails, evalMetrics } =
            transformMetricDetails(rowData);
          transformedData = { ...metricDetails, evalMetrics };
        }
        setTestDetailDrawerOpen({
          ...transformedData,
        });
      }
    } else {
      const fetchInitialData = async () => {
        const requestGeneration = navigationRequestGuardRef.current.begin();
        try {
          setIsFetching("initial");
          const exactResult = await fetchRowAtIndex(parsedRowIndex);
          if (!navigationRequestGuardRef.current.isCurrent(requestGeneration)) {
            return;
          }
          if (exactResult.pending) {
            enqueueSnackbar(LIST_CURSOR_CONTINUATION_NOTICE, {
              variant: "info",
            });
            return;
          }
          const rowData = exactResult.row;
          if (rowData) {
            let transformedData = rowData;
            if (urlOrigin === "agent-definition") {
              const { metricDetails, evalMetrics } =
                transformMetricDetails(rowData);
              transformedData = { ...metricDetails, evalMetrics };
            }

            setIsFetching(null);
            setTestDetailDrawerOpen({
              ...transformedData,
            });
            return;
          }
        } catch (error) {
          if (navigationRequestGuardRef.current.isCurrent(requestGeneration)) {
            enqueueSnackbar(
              "Call details are temporarily unavailable. Retry.",
              {
                variant: "info",
              },
            );
          }
        } finally {
          if (navigationRequestGuardRef.current.isCurrent(requestGeneration)) {
            setIsFetching(null);
          }
        }
      };

      fetchInitialData();
    }
  }, []);

  const navigateRecord = async (direction) => {
    let requestGeneration = null;
    try {
      const currentIndex = updatedRowIndex ?? 0;
      const newIndex =
        direction === "next" ? currentIndex + 1 : currentIndex - 1;
      const isNext = direction === "next";

      if (newIndex < 0) {
        enqueueSnackbar("You're already viewing the first record", {
          variant: "info",
        });
        return;
      }

      if (
        isNext &&
        !canNavigateToNextVoiceCallDetail({
          rowIndex: currentIndex,
          totalCount: navigationTotalCount,
          totalCountIsLowerBound: navigationTotalCountIsLowerBound,
        })
      ) {
        enqueueSnackbar("You're already viewing the last record", {
          variant: "info",
        });
        return;
      }

      requestGeneration = navigationRequestGuardRef.current.begin();

      // 1. Try cache first (instant)
      let rowData = getRowFromPageMap(newIndex, pageMap);

      // 2. If cache miss, fetch the page containing the target row
      if (!rowData) {
        setIsFetching(direction);
        const exactResult = await fetchRowAtIndex(newIndex);
        if (!navigationRequestGuardRef.current.isCurrent(requestGeneration)) {
          return;
        }
        if (exactResult.pending) {
          enqueueSnackbar(LIST_CURSOR_CONTINUATION_NOTICE, {
            variant: "info",
          });
          return;
        }
        rowData = exactResult.row;
      }

      if (!navigationRequestGuardRef.current.isCurrent(requestGeneration)) {
        return;
      }

      if (!rowData) {
        enqueueSnackbar(`No ${isNext ? "more" : "previous"} records found`, {
          variant: "info",
        });
        return;
      }

      if (urlOrigin === "agent-definition") {
        const { metricDetails, evalMetrics } = transformMetricDetails(rowData);
        rowData = { ...metricDetails, evalMetrics };
      }

      setUrlRowIndex({
        rowIndex: newIndex,
        module: urlModule,
        origin: urlOrigin,
      });
      setTestDetailDrawerOpen({
        ...rowData,
      });
    } catch (_error) {
      if (
        requestGeneration !== null &&
        navigationRequestGuardRef.current.isCurrent(requestGeneration)
      ) {
        enqueueSnackbar("Call details are temporarily unavailable. Retry.", {
          variant: "info",
        });
      }
    } finally {
      if (
        requestGeneration !== null &&
        navigationRequestGuardRef.current.isCurrent(requestGeneration)
      ) {
        setIsFetching(null);
      }
    }
  };

  const { scenarioId } = useMemo(() => {
    let scenarioPath = null;
    if (!data) return { scenarioId: null, scenarioPath: null };
    const scenarioPathColumn = Object.values(
      data?.scenario_columns || {},
    )?.find((item) => item.column_name === "scenario_flow");
    try {
      scenarioPath = JSON.parse(scenarioPathColumn?.value).map(
        (item) => item.name,
      );
    } catch (error) {
      return { scenarioId: data?.scenarioId, scenarioPath: null };
    }
    return {
      scenarioId: data?.scenarioId,
      scenarioPath: scenarioPath,
    };
  }, [data]);

  // Find the root conversation span from voiceDetail (snake_case flat array).
  // voiceDetail.observation_span includes parent_span_id on each span.
  const rootObsSpanId = useMemo(() => {
    const spans = mergedData?.observation_span;
    if (!Array.isArray(spans) || spans.length === 0) return null;
    return (
      spans.find(
        (s) => !s?.parent_span_id && s?.observation_type === "conversation",
      )?.id ||
      spans.find((s) => !s?.parent_span_id)?.id ||
      spans[0]?.id ||
      null
    );
  }, [mergedData?.observation_span]);

  const annotationSources = useMemo(
    () =>
      buildVoiceCallAnnotationSources({
        traceId,
        rootSpanId: rootObsSpanId,
        module: urlModule,
        callExecutionId: data?.id,
      }),
    [traceId, rootObsSpanId, urlModule, data?.id],
  );
  const hasCurrentTerminalNavigator =
    urlModule === "project" &&
    projectVoiceNavigatorSignatureRef.current ===
      projectVoiceNavigatorSignature &&
    projectVoiceNavigatorRef.current?.isTerminal() === true;
  const cursorTerminalTotal = hasCurrentTerminalNavigator
    ? projectVoiceNavigatorRef.current.loadedRowCount()
    : null;
  const navigationTotalCount = cursorTerminalTotal ?? totalCount;
  const navigationTotalCountIsLowerBound =
    cursorTerminalTotal === null && totalCountIsLowerBound === true;
  const hasNextRecord = canNavigateToNextVoiceCallDetail({
    rowIndex: updatedRowIndex,
    totalCount: navigationTotalCount,
    totalCountIsLowerBound: navigationTotalCountIsLowerBound,
  });
  const legacyNavigationTotalCount = getLegacyVoiceCallNavigationTotal({
    rowIndex: updatedRowIndex,
    totalCount: navigationTotalCount,
    totalCountIsLowerBound: navigationTotalCountIsLowerBound,
  });

  if (!data || isFetching === "initial") {
    return null;
  }

  return (
    <Box sx={{ display: "flex", minHeight: "100vh" }}>
      <ShowComponent condition={isFetching === "initial"}>
        <SkeltonForTestDeatilDrawer />
      </ShowComponent>

      <ShowComponent
        condition={isFetching !== "initial" && isVoiceCall && !compareReplay}
      >
        <VoiceDetailDrawerV2
          data={mergedData}
          onClose={closeDetailDrawer}
          onPrev={() => navigateRecord("prev")}
          onNext={() => navigateRecord("next")}
          hasPrev={(updatedRowIndex ?? 0) > 0}
          hasNext={hasNextRecord}
          isFetching={isFetching}
          onAnnotate={() => setAnnotationSidebarOpen(true)}
          onCompareBaseline={
            urlModule === "simulate" ? setCompareReplay : undefined
          }
          scenarioId={scenarioId}
          isLoading={isVoiceDetailLoading}
        />
      </ShowComponent>

      {/* Chat simulate rows render in the revamped ChatDetailDrawerV2 —
          including the Compare with baseline flow, which lives inside the
          drawer (body swaps; chrome stays). The drawer reads `compareReplay`
          and toggles its content between the two-panel chat layout and the
          ChatCompareView, with `onExitCompare` for the back-arrow affordance.
          The legacy fallback branch's condition still excludes chat sims so
          we don't render two drawers. */}
      <ShowComponent
        condition={
          isFetching !== "initial" && urlModule === "simulate" && isChatSim
        }
      >
        <ChatDetailDrawerV2
          data={mergedData}
          onClose={closeDetailDrawer}
          onPrev={() => navigateRecord("prev")}
          onNext={() => navigateRecord("next")}
          hasPrev={(updatedRowIndex ?? 0) > 0}
          hasNext={hasNextRecord}
          isFetching={isFetching}
          onAnnotate={() => setAnnotationSidebarOpen(true)}
          onCompareBaseline={setCompareReplay}
          compareReplay={compareReplay}
          onExitCompare={() => setCompareReplay(false)}
          scenarioId={scenarioId}
          isLoading={isVoiceDetailLoading}
        />
      </ShowComponent>

      <ShowComponent
        condition={
          isFetching !== "initial" &&
          !(isVoiceCall && !compareReplay) &&
          !(urlModule === "simulate" && isChatSim)
        }
      >
        <Box
          sx={{
            height: "100%",
            display: "flex",
            flexDirection: "column",
            gap: 2,
            width: "90vw",
            overflowY: "auto",
            scrollbarWidth: "none",
            msOverflowStyle: "none",
            "&::-webkit-scrollbar": {
              width: 0,
              height: 0,
              display: "none",
            },
          }}
        >
          <ShowComponent condition={urlModule === "project"}>
            <CustomCallLogHeader
              type={data?.call_type}
              module={urlModule}
              onClose={closeDetailDrawer}
              timestamp={data?.timestamp}
              duration={data?.duration_seconds}
              status={data?.status}
              phoneNumber={data?.phone_number}
              endedReason={data?.ended_reason}
              overAllScore={data?.overall_score}
              totalCount={legacyNavigationTotalCount}
              rowIndex={updatedRowIndex}
              isFetching={isFetching}
              onPrevClick={() => navigateRecord("prev")}
              onNextClick={() => navigateRecord("next")}
              onAnnotate={() => setAnnotationSidebarOpen(true)}
            />
          </ShowComponent>
          <ShowComponent condition={urlModule === "simulate" && !compareReplay}>
            <CustomCallLogHeader
              totalCount={totalCount}
              rowIndex={updatedRowIndex}
              isFetching={isFetching}
              type={data?.call_type}
              onClose={closeDetailDrawer}
              timestamp={data?.timestamp}
              duration={data?.duration}
              status={data?.status}
              scenario={data?.scenario}
              simulationCallType={data?.simulation_call_type}
              onPrevClick={() => navigateRecord("prev")}
              onNextClick={() => navigateRecord("next")}
              phoneNumber={data?.phone_number}
              endedReason={data?.ended_reason}
              overAllScore={data?.overall_score}
              origin={urlOrigin}
              serviceProviderCallId={data?.service_provider_call_id}
              customerCallId={data?.customerCallId}
              onAnnotate={() => setAnnotationSidebarOpen(true)}
            />
          </ShowComponent>
          <ShowComponent condition={urlModule === "simulate" && compareReplay}>
            <Suspense fallback={<HeaderSkeleton />}>
              <BaselineVsReplayHeader
                onClose={closeDetailDrawer}
                setCompareReplay={setCompareReplay}
                simulationCallType={data?.simulation_call_type}
              />
            </Suspense>
          </ShowComponent>
          <ShowComponent condition={compareReplay}>
            <BaseLineVsReplay rowData={mergedData} />
          </ShowComponent>
          <ShowComponent condition={!compareReplay}>
            <ShowComponent condition={urlModule === "simulate"}>
              <TestDetailDrawerScenarioTable data={mergedData} />
            </ShowComponent>
            <ShowComponent
              condition={data?.simulation_call_type !== AGENT_TYPES.CHAT}
            >
              <Stack
                marginX={2}
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                  backgroundColor: "background.neutral",

                  padding: "14px",
                  borderRadius: 0.5,
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Typography variant="m3" fontWeight={"fontWeightMedium"}>
                  Recording
                </Typography>
                {isVoiceDetailLoading ? (
                  <Skeleton
                    variant="rectangular"
                    width="100%"
                    height={140}
                    sx={{ borderRadius: 0.5 }}
                  />
                ) : (
                  <AudioPlayerCustom data={mergedData} />
                )}
              </Stack>
            </ShowComponent>

            <Grid container spacing={2} px={2} mb={2}>
              <Grid
                item
                xs={6}
                md={6}
                display="flex"
                flexDirection="column"
                gap={2}
                height="100%"
              >
                {isVoiceDetailLoading ? (
                  <Skeleton
                    variant="rectangular"
                    width="100%"
                    height={350}
                    sx={{ borderRadius: 1 }}
                  />
                ) : (
                  <LeftSection data={mergedData} />
                )}
              </Grid>
              <Grid item xs={6} md={6} height="100%">
                <ShowComponent condition={urlModule === "simulate"}>
                  <Box
                    sx={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 0.5,
                      alignItems: "center",
                    }}
                  >
                    <TestDetailDrawerRightSection
                      scenarioId={scenarioId}
                      status={data?.status}
                      openedExecutionId={data?.id}
                      latencies={data?.customerLatencyMetrics?.systemMetrics}
                      analysisSummary={data?.callSummary}
                      costBreakdown={data?.customerCostBreakdown}
                      evalOutputs={data?.eval_metrics}
                      callStatus={data?.overall_status}
                      simulationCallType={data?.simulation_call_type}
                      setCompareReplay={setCompareReplay}
                      sessionId={data?.session_id ?? data?.sessionId}
                      provider={data?.provider}
                    />
                  </Box>
                </ShowComponent>
                <ShowComponent condition={urlModule === "project"}>
                  {isVoiceDetailLoading ? (
                    <Skeleton
                      variant="rectangular"
                      width="100%"
                      height={350}
                      sx={{ borderRadius: 1 }}
                    />
                  ) : (
                    <RightSection data={mergedData} />
                  )}
                </ShowComponent>
              </Grid>
            </Grid>
          </ShowComponent>
        </Box>
      </ShowComponent>

      <Drawer
        anchor="right"
        open={annotationSidebarOpen}
        onClose={() => setAnnotationSidebarOpen(false)}
        PaperProps={{
          sx: { width: 360, zIndex: 20 },
        }}
        ModalProps={{
          BackdropProps: {
            style: { backgroundColor: "transparent" },
          },
        }}
      >
        <AnnotationSidebarContent
          sources={annotationSources}
          onClose={() => setAnnotationSidebarOpen(false)}
          onScoresChanged={() => {
            queryClient.invalidateQueries({
              queryKey: ["annotation-queues", "for-source"],
            });
            queryClient.invalidateQueries({
              queryKey: ["callLogs"],
            });
          }}
          onAddLabel={
            resolvedAgentDefinitionId || resolvedProjectId
              ? () => setAddLabelDrawerOpen(true)
              : undefined
          }
        />
      </Drawer>

      <AddLabelDrawer
        open={addLabelDrawerOpen}
        onClose={() => setAddLabelDrawerOpen(false)}
        agentDefinitionId={resolvedAgentDefinitionId}
        projectId={resolvedProjectId}
        onLabelsChanged={() => {
          queryClient.invalidateQueries({
            queryKey: ["annotation-queues", "for-source"],
          });
        }}
      />
    </Box>
  );
};

TestDetailSideDrawerChild.propTypes = {
  data: PropTypes.object,
  isRefreshing: PropTypes.bool,
  drawerQueryKey: PropTypes.array,
  urlOrigin: PropTypes.string,
  urlModule: PropTypes.string,
  onClose: PropTypes.func,
};

const TestDetailSideDrawer = ({
  isRefreshing,
  drawerQueryKey,
  origin = "simulate",
}) => {
  const [rowIndexData, _, removeRowIndex] = useUrlState("rowIndex");
  const {
    rowIndex: updatedRowIndex,
    module,
    origin: urlOrigin,
  } = rowIndexData || {};
  const { testDetailDrawerOpen, setTestDetailDrawerOpen, setCompareReplay } =
    useTestDetailSideDrawerStoreShallow((state) => ({
      testDetailDrawerOpen: state.testDetailDrawerOpen,
      setTestDetailDrawerOpen: state.setTestDetailDrawerOpen,
      setCompareReplay: state.setCompareReplay,
    }));
  const handleClose = () => {
    removeRowIndex();
  };

  const hasUrlRowIndex =
    updatedRowIndex !== undefined && updatedRowIndex !== null;
  const isDrawerOpen = hasUrlRowIndex && !!testDetailDrawerOpen;

  const effectiveOrigin = urlOrigin || origin;
  const effectiveModule = module || "simulate";

  return (
    <Drawer
      open={isDrawerOpen}
      keepMounted={hasUrlRowIndex}
      onClose={handleClose}
      anchor="right"
      SlideProps={{
        onExited: () => {
          setTestDetailDrawerOpen(null);
          setCompareReplay(false);
        },
      }}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 10,
          boxShadow: "-10px 0px 100px #00000035",
          borderRadius: "0px !important",
          backgroundColor: "background.default",
          display: "flex",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: {
            backgroundColor: "transparent",
            borderRadius: "0px !important",
          },
        },
      }}
    >
      <TestDetailSideDrawerChild
        drawerQueryKey={drawerQueryKey}
        isRefreshing={isRefreshing}
        data={testDetailDrawerOpen}
        urlOrigin={effectiveOrigin}
        urlModule={effectiveModule}
        onClose={handleClose}
      />
    </Drawer>
  );
};

export default TestDetailSideDrawer;

TestDetailSideDrawer.propTypes = {
  isRefreshing: PropTypes.bool,
  drawerQueryKey: PropTypes.array,
  origin: PropTypes.string,
};
