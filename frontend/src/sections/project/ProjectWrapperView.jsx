import {
  Badge,
  Box,
  Button,
  Divider,
  LinearProgress,
  Typography,
  useTheme,
} from "@mui/material";
import { styled } from "@mui/material/styles";
import LoadingButton from "@mui/lab/LoadingButton";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import axios, { endpoints } from "src/utils/axios";
import { useLocation, useNavigate } from "react-router";
import { Helmet } from "react-helmet-async";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import axiosInstance from "src/utils/axios";
import { useSnackbar } from "notistack";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";

import ExperimentListView from "./ExperimentListView";
import ObserveListView from "./ObserveListView";
import ProjectObserveContextProvider from "./context/ProjectObserveContextProvider";
import ProjectExperimentContextProvider from "./context/ProjectExperimentContextProvider";
import ProjectRightSection from "./RightSection/ProjectRightSection";
import ProjectFtux from "./ProjectFtux";
import ProjectFilterPanel from "./ProjectFilterPanel";
import NewProjectDrawer from "./NewProject/NewProjectDrawer";
import { canOpenSample } from "src/sections/onboarding-home/activation-state-utils";
import { useActivationState } from "src/sections/onboarding-home/hooks/useActivationState";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import { useSampleProject } from "src/sections/onboarding-home/hooks/useSampleProject";
import ObserveOnboardingFocusPanel from "src/sections/projects/ObserveOnboardingFocusPanel";
import {
  buildObserveProjectOnboardingHref,
  buildObserveRouteFocusPayload,
  buildObserveTraceReviewHref,
  getFirstTraceIdFromTraceListResult,
  getObserveOnboardingCopy,
  getObserveSetupPackageLabel,
  getObserveSetupOnboardingParams,
  OBSERVE_ONBOARDING_MODES,
  OBSERVE_ONBOARDING_SOURCES,
} from "src/sections/projects/observeOnboardingRoute";

export const SearchFieldBox = styled(Box)(({ theme }) => ({
  display: "flex",
  alignItems: "center",
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: theme.spacing(0.5),
  height: "38px",
  width: "360px",
  // margin: '0 auto 17px'
}));

const ProjectWrapperView = () => {
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedAll, setSelectedAll] = useState(false);
  const [selectedRowsData, setSelectedRowsData] = useState([]);
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);
  const [observeFilters, setObserveFilters] = useState(null);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [setupDrawerOpen, setSetupDrawerOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const gridRef = useRef(null);
  const recordedObserveSetupFocusRef = useRef(false);
  const autoOpenedObserveSetupDrawerRef = useRef(false);
  const autoEnteredTraceWaitRef = useRef(null);
  const autoOpenedTraceReviewRef = useRef(null);
  const observeSetupTraceBaselineRef = useRef({
    key: null,
    traceId: undefined,
  });
  const sawEmptyObserveSetupRef = useRef(false);
  const currentTab = location.pathname.split("/").pop();
  const { enqueueSnackbar } = useSnackbar();
  const queryClient = useQueryClient();
  const observeSetupOnboardingParams = useMemo(
    () => getObserveSetupOnboardingParams(location.search),
    [location.search],
  );
  const showObserveSetupFocus =
    currentTab === "observe" &&
    observeSetupOnboardingParams.mode ===
      OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE;
  const isSampleReviewReturn =
    observeSetupOnboardingParams.source ===
    OBSERVE_ONBOARDING_SOURCES.SAMPLE_TRACE_REVIEW;
  const { data: observeSetupFocusState, mutate: recordActivationEvent } =
    useRecordActivationEvent();
  const { state: observeActivationState } = useActivationState({
    enabled: showObserveSetupFocus,
    requireWorkspaceContext: false,
    source: observeSetupOnboardingParams.source,
  });
  const {
    openSampleProject: {
      isPending: isOpeningSampleTrace,
      mutateAsync: openSampleProject,
    },
  } = useSampleProject();
  const { data, isLoading } = useQuery({
    queryKey: [`project-${currentTab}-list`],
    queryFn: () =>
      axios.get(
        currentTab === "observe"
          ? endpoints.project.projectObserveList
          : endpoints.project.projectExperimentList,
        {
          params: {
            project_type: currentTab === "observe" ? "observe" : "experiment",
          },
        },
      ),
    select: (data) => data.data,
    refetchInterval: showObserveSetupFocus ? 5000 : false,
  });

  const theme = useTheme();

  const isProjectCount =
    currentTab === "observe"
      ? data?.result?.metadata?.total_rows > 0
      : data?.result?.projects?.length > 0;
  const observeProjectRows =
    data?.result?.table?.length > 0
      ? data.result.table
      : data?.result?.projects || [];
  const activationFirstObserveProjectId =
    observeSetupFocusState?.signals?.firstObserveId ||
    observeSetupFocusState?.signals?.first_observe_id ||
    observeActivationState?.signals?.firstObserveId ||
    observeActivationState?.signals?.first_observe_id ||
    null;
  const firstObserveProjectId =
    currentTab === "observe"
      ? observeProjectRows.find(
          (project) =>
            activationFirstObserveProjectId &&
            String(project?.id) === String(activationFirstObserveProjectId),
        )?.id ||
        observeProjectRows.find((project) => project?.id)?.id ||
        null
      : null;

  const observeSetupCopy = useMemo(
    () =>
      showObserveSetupFocus
        ? getObserveOnboardingCopy(OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE, {
            credentialsCopied: observeSetupOnboardingParams.credentialsCopied,
            setupLanguage: observeSetupOnboardingParams.setupLanguage,
            setupProvider: observeSetupOnboardingParams.setupProvider,
            source: observeSetupOnboardingParams.source,
          })
        : null,
    [
      observeSetupOnboardingParams.credentialsCopied,
      observeSetupOnboardingParams.setupLanguage,
      observeSetupOnboardingParams.setupProvider,
      observeSetupOnboardingParams.source,
      showObserveSetupFocus,
    ],
  );
  const observeSetupPackageLabel = useMemo(
    () =>
      getObserveSetupPackageLabel({
        setupLanguage: observeSetupOnboardingParams.setupLanguage,
        setupProvider: observeSetupOnboardingParams.setupProvider,
      }),
    [
      observeSetupOnboardingParams.setupLanguage,
      observeSetupOnboardingParams.setupProvider,
    ],
  );
  const canOpenObserveSetupSample =
    !isSampleReviewReturn &&
    canOpenSample(
      observeSetupFocusState?.sampleProject ||
        observeActivationState?.sampleProject,
    );
  const observeKnownTraceCount =
    observeSetupFocusState?.signals?.traces ??
    observeSetupFocusState?.signals?.trace_count ??
    observeActivationState?.signals?.traces ??
    observeActivationState?.signals?.trace_count;
  const traceCountWasKnownEmpty =
    observeKnownTraceCount !== undefined &&
    Number(observeKnownTraceCount) === 0;

  useEffect(() => {
    if (!showObserveSetupFocus) return;
    const recordKey = [
      observeSetupOnboardingParams.credentialStep,
      observeSetupOnboardingParams.source,
      observeSetupOnboardingParams.setupProvider,
      observeSetupOnboardingParams.setupLanguage,
    ].join(":");
    if (recordedObserveSetupFocusRef.current === recordKey) return;
    recordedObserveSetupFocusRef.current = recordKey;
    recordActivationEvent?.(
      buildObserveRouteFocusPayload({
        credentialStep: observeSetupOnboardingParams.credentialStep,
        mode: OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE,
        setupLanguage: observeSetupOnboardingParams.setupLanguage,
        setupProvider: observeSetupOnboardingParams.setupProvider,
        setupSource: observeSetupOnboardingParams.source,
      }),
    );
  }, [
    observeSetupOnboardingParams.credentialStep,
    observeSetupOnboardingParams.setupLanguage,
    observeSetupOnboardingParams.setupProvider,
    observeSetupOnboardingParams.source,
    recordActivationEvent,
    showObserveSetupFocus,
  ]);

  useEffect(() => {
    if (
      !showObserveSetupFocus ||
      isLoading ||
      !isProjectCount ||
      autoOpenedObserveSetupDrawerRef.current
    ) {
      return;
    }
    autoOpenedObserveSetupDrawerRef.current = true;
    setSetupDrawerOpen(true);
  }, [isLoading, isProjectCount, showObserveSetupFocus]);

  useEffect(() => {
    if (!showObserveSetupFocus || isLoading) return;
    if (!isProjectCount) {
      sawEmptyObserveSetupRef.current = true;
      return;
    }
    if (!firstObserveProjectId || !sawEmptyObserveSetupRef.current) return;
    navigate(
      buildObserveProjectOnboardingHref({
        observeId: firstObserveProjectId,
        mode: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
        search: location.search,
        setupLanguage: observeSetupOnboardingParams.setupLanguage,
        setupProvider: observeSetupOnboardingParams.setupProvider,
      }),
      { replace: true },
    );
  }, [
    firstObserveProjectId,
    isLoading,
    isProjectCount,
    navigate,
    location.search,
    observeSetupOnboardingParams.setupLanguage,
    observeSetupOnboardingParams.setupProvider,
    observeSetupOnboardingParams.source,
    showObserveSetupFocus,
  ]);

  useEffect(() => {
    if (
      !showObserveSetupFocus ||
      isLoading ||
      !isProjectCount ||
      !firstObserveProjectId ||
      !observeSetupOnboardingParams.credentialsCopied
    ) {
      return;
    }

    const waitKey = [
      firstObserveProjectId,
      observeSetupOnboardingParams.setupProvider,
      observeSetupOnboardingParams.setupLanguage,
      observeSetupOnboardingParams.credentialStep,
    ].join(":");
    if (autoEnteredTraceWaitRef.current === waitKey) return;
    autoEnteredTraceWaitRef.current = waitKey;

    navigate(
      buildObserveProjectOnboardingHref({
        observeId: firstObserveProjectId,
        mode: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
        search: location.search,
        setupLanguage: observeSetupOnboardingParams.setupLanguage,
        setupProvider: observeSetupOnboardingParams.setupProvider,
        baselineTraceId: observeSetupTraceBaselineRef.current.traceId || null,
      }),
      { replace: true },
    );
  }, [
    firstObserveProjectId,
    isLoading,
    isProjectCount,
    navigate,
    location.search,
    observeSetupOnboardingParams.credentialStep,
    observeSetupOnboardingParams.credentialsCopied,
    observeSetupOnboardingParams.setupLanguage,
    observeSetupOnboardingParams.setupProvider,
    observeSetupOnboardingParams.source,
    showObserveSetupFocus,
  ]);

  const fetchOnboardingFirstTraceId = useCallback(async () => {
    if (!firstObserveProjectId) return null;

    const response = await axios.get(
      endpoints.project.getTracesForObserveProject(),
      {
        params: {
          page_number: 0,
          page_size: 1,
          filters: "[]",
          project_id: firstObserveProjectId,
        },
      },
    );
    return getFirstTraceIdFromTraceListResult(response?.data?.result);
  }, [firstObserveProjectId]);

  useEffect(() => {
    if (
      !showObserveSetupFocus ||
      isLoading ||
      !isProjectCount ||
      !firstObserveProjectId
    ) {
      return undefined;
    }

    let mounted = true;
    const baselineKey = [
      firstObserveProjectId,
      observeSetupOnboardingParams.setupProvider,
      observeSetupOnboardingParams.setupLanguage,
      observeSetupOnboardingParams.source,
    ].join(":");
    if (observeSetupTraceBaselineRef.current.key !== baselineKey) {
      observeSetupTraceBaselineRef.current = {
        key: baselineKey,
        traceId: undefined,
      };
    }

    const verifyFirstTrace = () => {
      void fetchOnboardingFirstTraceId()
        .then((traceId) => {
          if (!mounted) return;
          const baseline = observeSetupTraceBaselineRef.current;
          if (baseline.key !== baselineKey) return;
          if (baseline.traceId === undefined) {
            baseline.traceId = traceId || null;
            if (!traceId || !traceCountWasKnownEmpty) return;
          } else {
            if (!traceId) return;
            if (traceId === baseline.traceId) return;
          }
          const reviewKey = `${firstObserveProjectId}:${traceId}`;
          if (autoOpenedTraceReviewRef.current === reviewKey) return;
          autoOpenedTraceReviewRef.current = reviewKey;
          navigate(
            buildObserveTraceReviewHref({
              observeId: firstObserveProjectId,
              search: location.search,
              setupLanguage: observeSetupOnboardingParams.setupLanguage,
              setupProvider: observeSetupOnboardingParams.setupProvider,
              traceId,
            }),
            { replace: true },
          );
        })
        .catch(() => undefined);
    };

    verifyFirstTrace();
    const intervalId = window.setInterval(verifyFirstTrace, 5000);
    return () => {
      mounted = false;
      window.clearInterval(intervalId);
    };
  }, [
    fetchOnboardingFirstTraceId,
    firstObserveProjectId,
    isLoading,
    isProjectCount,
    location.search,
    navigate,
    observeSetupOnboardingParams.setupLanguage,
    observeSetupOnboardingParams.setupProvider,
    observeSetupOnboardingParams.source,
    traceCountWasKnownEmpty,
    showObserveSetupFocus,
  ]);

  const resolveCurrentTraceBaselineId = useCallback(async () => {
    if (!firstObserveProjectId) return null;
    const currentBaseline = observeSetupTraceBaselineRef.current.traceId;
    if (currentBaseline !== undefined) return currentBaseline || null;
    try {
      const traceId = await fetchOnboardingFirstTraceId();
      observeSetupTraceBaselineRef.current.traceId = traceId || null;
      return traceId || null;
    } catch {
      observeSetupTraceBaselineRef.current.traceId = null;
      return null;
    }
  }, [fetchOnboardingFirstTraceId, firstObserveProjectId]);

  const handleObserveSetupPrimaryAction = useCallback(async () => {
    if (firstObserveProjectId) {
      const baselineTraceId = await resolveCurrentTraceBaselineId();
      navigate(
        buildObserveProjectOnboardingHref({
          baselineTraceId,
          observeId: firstObserveProjectId,
          mode: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
          search: location.search,
          setupLanguage: observeSetupOnboardingParams.setupLanguage,
          setupProvider: observeSetupOnboardingParams.setupProvider,
        }),
      );
      return;
    }

    if (isProjectCount) {
      setSetupDrawerOpen(true);
      return;
    }
    document
      .getElementById("observe-setup-instructions")
      ?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [
    firstObserveProjectId,
    isProjectCount,
    location.search,
    navigate,
    observeSetupOnboardingParams.setupLanguage,
    observeSetupOnboardingParams.setupProvider,
    resolveCurrentTraceBaselineId,
  ]);
  const handleOpenObserveSetupDrawer = useCallback(() => {
    setSetupDrawerOpen(true);
  }, []);

  const handleOpenSampleTrace = useCallback(async () => {
    try {
      const nextState = await openSampleProject({
        path: "observe",
        source: "observe_setup_onboarding",
        reason: "setup_observe",
        openAfterCreate: true,
      });
      const entryRoute =
        nextState?.sampleProject?.entryRoute ||
        nextState?.sampleProject?.entryRoutes?.[0];
      if (entryRoute) {
        navigate(entryRoute);
        return;
      }
      enqueueSnackbar(
        "Sample trace is not available yet. Continue with setup.",
        {
          variant: "info",
        },
      );
    } catch {
      enqueueSnackbar(
        "Sample trace is not available yet. Continue with setup.",
        {
          variant: "error",
        },
      );
    }
  }, [enqueueSnackbar, navigate, openSampleProject]);

  const reviewObserveSetupAction = useMemo(() => {
    if (!observeSetupCopy) return null;
    return {
      label: firstObserveProjectId
        ? observeSetupPackageLabel
          ? `Check for ${observeSetupPackageLabel} trace`
          : "Check for trace"
        : isProjectCount
          ? "Open package setup"
          : observeSetupCopy.primaryLabel,
      onClick: handleObserveSetupPrimaryAction,
    };
  }, [
    firstObserveProjectId,
    handleObserveSetupPrimaryAction,
    isProjectCount,
    observeSetupCopy,
    observeSetupPackageLabel,
  ]);

  const openSampleTraceAction = useMemo(() => {
    if (!observeSetupCopy || !canOpenObserveSetupSample) return null;
    return {
      disabled: isOpeningSampleTrace,
      label: isOpeningSampleTrace ? "Opening sample..." : "Open sample trace",
      onClick: handleOpenSampleTrace,
    };
  }, [
    canOpenObserveSetupSample,
    handleOpenSampleTrace,
    isOpeningSampleTrace,
    observeSetupCopy,
  ]);

  const observeSetupPrimaryAction = reviewObserveSetupAction;

  const observeSetupSecondaryAction = useMemo(() => {
    if (observeSetupCopy && showObserveSetupFocus && firstObserveProjectId) {
      return {
        label: observeSetupCopy.primaryLabel || "Open package setup",
        onClick: handleOpenObserveSetupDrawer,
      };
    }
    if (
      !observeSetupCopy ||
      !showObserveSetupFocus ||
      !canOpenObserveSetupSample ||
      !openSampleTraceAction
    ) {
      return null;
    }
    return openSampleTraceAction;
  }, [
    canOpenObserveSetupSample,
    firstObserveProjectId,
    handleOpenObserveSetupDrawer,
    observeSetupCopy,
    openSampleTraceAction,
    showObserveSetupFocus,
  ]);

  const observeSetupVerification = useMemo(() => {
    if (!observeSetupCopy || !showObserveSetupFocus) {
      return null;
    }
    const hasObserveProject = Boolean(firstObserveProjectId);
    const waitLabel = observeSetupPackageLabel
      ? `Check for ${observeSetupPackageLabel} trace`
      : "Check for trace";
    return {
      description: hasObserveProject
        ? observeSetupPackageLabel
          ? `Run one ${observeSetupPackageLabel} request after pasting the setup. Keep this setup open; Future AGI opens review when the trace arrives, then guides the first quality check.`
          : "Run one request after pasting the setup. Keep this setup open; Future AGI opens review when the trace arrives, then guides the first quality check."
        : "Keep this page open after running your app. Future AGI checks every few seconds, opens trace review when data arrives, then guides the first quality check.",
      primaryAction: hasObserveProject
        ? {
            label: waitLabel,
            onClick: handleObserveSetupPrimaryAction,
          }
        : undefined,
      status: "waiting",
      title: hasObserveProject
        ? observeSetupPackageLabel
          ? `Waiting for ${observeSetupPackageLabel} trace`
          : "Waiting for first trace"
        : "Checking for your first trace",
    };
  }, [
    firstObserveProjectId,
    handleObserveSetupPrimaryAction,
    observeSetupCopy,
    observeSetupPackageLabel,
    showObserveSetupFocus,
  ]);

  const handleSearchChange = (e) => {
    setSearchQuery(e.target.value);
  };

  const handleSelectionChanged = (event) => {
    if (!event) {
      setTimeout(() => {
        setSelectedRowsData([]);
      }, 300);
      return;
    }
    if (event?.data?.id) {
      const rowId = event?.data?.id;
      setSelectedRowsData((prevSelectedItems) => {
        const updatedSelectedRowsData = [...prevSelectedItems];

        const rowIndex = updatedSelectedRowsData?.findIndex(
          (row) => row === rowId,
        );

        if (rowIndex === -1) {
          updatedSelectedRowsData.push(event?.data?.id);
        } else {
          updatedSelectedRowsData.splice(rowIndex, 1);
        }

        return updatedSelectedRowsData;
      });
    }
  };

  const clearSelection = () => {
    gridRef.current?.clearSelection?.();
    setSelectedAll(false);
    setSelectedRowsData([]);
  };

  const handleDelete = () => {
    setDeleteModalOpen(true);
  };

  const { mutate: confirmDelete, isPending: isDeleting } = useMutation({
    mutationFn: () =>
      axiosInstance.delete(endpoints.project.deleteObservePrototype, {
        data: {
          project_ids: selectedRowsData,
          project_type: currentTab === "observe" ? "observe" : "experiment",
        },
      }),
    onSuccess: () => {
      const filesLength = selectedRowsData.length;
      const message =
        filesLength === 1
          ? "Project has been deleted."
          : `${filesLength} Projects have been deleted.`;
      setDeleteModalOpen(false);
      enqueueSnackbar(message, {
        variant: "success",
      });
      queryClient.invalidateQueries({
        queryKey: [`project-${currentTab}-list`],
      });
      queryClient.invalidateQueries({
        queryKey: [
          currentTab === "observe" ? "observe-projects" : "experiment-projects",
        ],
      });
      gridRef?.current?.clearSelection?.();
      setSelectedRowsData([]);
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.message ||
          "An unexpected error occurred while deleting monitors.",
        {
          variant: "error",
        },
      );
    },
  });

  if (isLoading) {
    return <LinearProgress />;
  }

  if (!isProjectCount) {
    return (
      <ProjectFtux
        observeSetupCopy={observeSetupCopy}
        observeSetupPrimaryAction={observeSetupPrimaryAction}
        observeSetupSecondaryAction={observeSetupSecondaryAction}
        observeSetupTourAnchor={observeSetupOnboardingParams.tourAnchor}
        observeSetupVerification={observeSetupVerification}
      />
    );
  }

  return (
    <ProjectExperimentContextProvider>
      <ProjectObserveContextProvider>
        <Helmet>
          <title>{currentTab === "observe" ? "Tracing" : "Prototype"}</title>
        </Helmet>
        <Box
          sx={{
            backgroundColor: "background.paper",
            height: "100%",
            padding: (theme) => theme.spacing(2),
            display: "flex",
            flexDirection: "column",
            gap: (theme) => theme.spacing(2),
          }}
        >
          {showObserveSetupFocus && observeSetupCopy ? (
            <ObserveOnboardingFocusPanel
              currentStep={observeSetupCopy.currentStep}
              description={observeSetupCopy.description}
              primaryAction={observeSetupPrimaryAction}
              secondaryAction={observeSetupSecondaryAction}
              steps={observeSetupCopy.steps}
              sx={{ mb: 0 }}
              title={observeSetupCopy.title}
              tourAnchor={observeSetupOnboardingParams.tourAnchor}
            />
          ) : null}
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: (theme) => theme.spacing(0.25),
            }}
          >
            <Typography
              color="text.primary"
              variant="m2"
              fontWeight={"fontWeightSemiBold"}
            >
              {currentTab === "observe" ? "Tracing" : "Prototype"}
            </Typography>
            <Box
              sx={{
                display: "flex",
                gap: (theme) => theme.spacing(0.5),
                alignItems: "center",
              }}
            >
              <Typography
                variant="s1"
                color="text.primary"
                fontWeight={"fontWeightRegular"}
              >
                Create a project to experiment on your model
              </Typography>
            </Box>
          </Box>

          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <FormSearchField
                size="small"
                placeholder="Search"
                searchQuery={searchQuery}
                onChange={handleSearchChange}
                sx={{
                  minWidth: "250px",
                  "& .MuiOutlinedInput-root": { height: "30px" },
                }}
              />
              {currentTab === "observe" && (
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={
                    observeFilters?.length > 0 ? (
                      <Badge variant="dot" color="error" overlap="circular">
                        <Iconify icon="mage:filter" width={14} />
                      </Badge>
                    ) : (
                      <Iconify icon="mage:filter" width={14} />
                    )
                  }
                  onClick={(e) => setFilterAnchorEl(e.currentTarget)}
                  sx={{
                    textTransform: "none",
                    fontSize: 12,
                    height: 36,
                    borderColor: "divider",
                    color: "text.secondary",
                  }}
                >
                  Filter
                </Button>
              )}
            </Box>
            {selectedRowsData.length > 0 ? (
              <Box
                sx={{
                  display: "flex",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                  paddingX: theme.spacing(2),
                  paddingY: theme.spacing(0.5),
                  alignItems: "center",
                  gap: theme.spacing(2),
                }}
              >
                <Typography
                  fontWeight="fontWeightMedium"
                  variant="s1"
                  color="primary.main"
                >
                  {selectedRowsData.length} Selected
                </Typography>
                <Divider
                  orientation="vertical"
                  flexItem
                  sx={{ borderRightWidth: theme.spacing(0.25) }}
                />
                <Button
                  startIcon={
                    <SvgColor
                      src="/assets/icons/ic_delete.svg"
                      sx={{ width: 20, height: 20, color: "text.disabled" }}
                    />
                  }
                  size="small"
                  sx={{ color: "text.secondary" }}
                  onClick={handleDelete}
                >
                  <Typography
                    variant="s1"
                    fontWeight={"fontWeightRegular"}
                    color="text.primary"
                  >
                    Delete
                  </Typography>
                </Button>
                <Button
                  size="small"
                  sx={{ color: "text.secondary" }}
                  onClick={clearSelection}
                >
                  <Typography
                    variant="s1"
                    fontWeight={"fontWeightRegular"}
                    color="text.primary"
                  >
                    Cancel
                  </Typography>
                </Button>
              </Box>
            ) : (
              <ProjectRightSection isObserve={currentTab === "observe"} />
            )}
          </Box>

          {currentTab === "observe" ? (
            <>
              <ObserveListView
                ref={gridRef}
                searchQuery={searchQuery}
                onSelectionChanged={handleSelectionChanged}
                selectedAll={selectedAll}
                setSelectedAll={setSelectedAll}
                setSelectedRowsData={setSelectedRowsData}
                filters={observeFilters}
              />
              <ProjectFilterPanel
                anchorEl={filterAnchorEl}
                open={Boolean(filterAnchorEl)}
                onClose={() => setFilterAnchorEl(null)}
                currentFilters={observeFilters}
                onApply={setObserveFilters}
              />
            </>
          ) : (
            <ExperimentListView
              ref={gridRef}
              searchQuery={searchQuery}
              setSelectedRowsData={setSelectedRowsData}
            />
          )}
          <ConfirmDialog
            open={deleteModalOpen}
            onClose={() => setDeleteModalOpen(false)}
            title="Delete Project"
            content={
              <Typography color="text.disabled">
                Are you sure you want to delete{" "}
                {selectedRowsData.length === 1
                  ? "this project?"
                  : `these ${selectedRowsData.length} projects?`}
              </Typography>
            }
            action={
              <LoadingButton
                variant="contained"
                color="error"
                size="small"
                onClick={confirmDelete}
                loading={isDeleting}
              >
                <Typography variant="s2" fontWeight="fontWeightSemiBold">
                  Delete
                </Typography>
              </LoadingButton>
            }
          />
          <NewProjectDrawer
            open={setupDrawerOpen}
            onClose={() => setSetupDrawerOpen(false)}
            observeSetupVerification={observeSetupVerification}
          />
        </Box>
      </ProjectObserveContextProvider>
    </ProjectExperimentContextProvider>
  );
};

export default ProjectWrapperView;
