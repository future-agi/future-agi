import React, {
  useState,
  useEffect,
  useMemo,
  useRef,
  useCallback,
} from "react";
import { flushSync } from "react-dom";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Typography,
  useTheme,
  styled,
  CircularProgress,
  Popover,
  MenuItem,
} from "@mui/material";
import { useNavigate, useParams, useLocation } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
// palette import removed — no longer used
import { useUrlState } from "src/routes/hooks/use-url-state";
import { getStorage, setStorage } from "src/hooks/use-local-storage";
import { ShareDialog } from "src/components/share-dialog";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useDebounce } from "src/hooks/use-debounce";

import { useProjectList, DOC_LINKS } from "./LLMTracing/common";
import { resetTraceGridStore, resetSpanGridStore } from "./LLMTracing/states";
import TagEditor from "src/sections/project/TagEditor";
import ConfigureProject from "../project-detail/ConfigureProject";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { ObserveIconButton } from "./SharedComponents";
import { useGetProjectDetails } from "src/api/project/project-detail";
import {
  OBSERVE_LIST_REFRESH_EVENT,
  OBSERVE_PAGE_CHANGED_EVENT,
} from "./observeEvents";

// CustomBackButton removed — replaced with inline Box button

const ProjectDropdownButton = styled(Button)(({ theme }) => ({
  minWidth: 200,
  height: 26,
  justifyContent: "space-between",
  textTransform: "none",
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: "4px",
  backgroundColor: "transparent",
  color: theme.palette.text.primary,
  padding: theme.spacing(0.25, 1.5),
  fontSize: 14,
  fontFamily: "'IBM Plex Sans', sans-serif",
  "&:hover": {
    backgroundColor: theme.palette.action.hover,
    borderColor: theme.palette.text.disabled,
  },
}));

const ObserveHeader = ({ text, refreshData, resetFilters }) => {
  const [openConfigDialog, setOpenConfigDialog] = useState(false);
  const queryClient = useQueryClient();
  const [openShareUrl, setOpenShareUrl] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [isAggregationRefreshing, setIsAggregationRefreshing] = useState(false);
  const aggregationRefreshSourcesRef = useRef(new Set());
  const [autoRefresh, _setAutoRefresh] = useState(
    () => getStorage("autoRefresh") ?? false,
  );
  const setAutoRefresh = useCallback((value) => {
    _setAutoRefresh(value);
    setStorage("autoRefresh", value);
  }, []);
  const [excludeSimulationCalls, setExcludeSimulationCalls] = useUrlState(
    "remove_simulation_calls",
    false,
  );
  const { observeId } = useParams();

  const { data: projectDetail } = useGetProjectDetails(observeId, false);
  const [projectDropdownOpen, setProjectDropdownOpen] = useState(false);
  const [searchText, setSearchText] = useState("");

  const navigate = useNavigate();
  const location = useLocation();
  const theme = useTheme();
  const projectDropdownRef = useRef(null);

  const debouncedSearchText = useDebounce(searchText.trim(), 300);

  const currentPath = location.pathname;
  const isLLMTracingTab = currentPath.includes("/llm-tracing");
  const isSessionsTab = currentPath.includes("/sessions");
  const isUsersTab = currentPath.includes("/users");

  const handleBack = () => {
    if (window.history.length > 2) {
      navigate(-1);
    } else {
      const currentPath = window.location.pathname;

      if (currentPath.includes("/users/")) {
        const parentPath = currentPath.split("/users/")[0] + "/users";
        navigate(parentPath);
      } else {
        navigate("/dashboard/observe");
      }
    }
  };

  useEffect(() => {
    let intervalId;
    if (autoRefresh) {
      intervalId = setInterval(() => {
        // Keep the current rows painted while their same-query replacement is
        // fetched. Calling the parent refresh callback directly bypasses each
        // grid's preserve-rows path and makes the whole table flash black.
        // Exact aggregations remain an explicit reload-only operation.
        window.dispatchEvent(
          new CustomEvent(OBSERVE_LIST_REFRESH_EVENT, {
            detail: { observeId },
          }),
        );
      }, 10000);
    }
    return () => {
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  }, [autoRefresh, observeId]);

  useEffect(() => {
    const handlePageChanged = (event) => {
      if (Number(event?.detail?.page) > 1) setAutoRefresh(false);
    };
    window.addEventListener(OBSERVE_PAGE_CHANGED_EVENT, handlePageChanged);
    return () =>
      window.removeEventListener(OBSERVE_PAGE_CHANGED_EVENT, handlePageChanged);
  }, [setAutoRefresh]);

  useEffect(() => {
    setLastUpdated(null);
    aggregationRefreshSourcesRef.current.clear();
    setIsAggregationRefreshing(false);
  }, [currentPath, observeId]);

  useEffect(() => {
    const handleAggregationCompleted = (event) => {
      if (String(event?.detail?.observeId || "") !== String(observeId || "")) {
        return;
      }
      const raw = event?.detail?.queryCompletedAt;
      const completedAt = raw ? new Date(raw) : null;
      if (!completedAt || Number.isNaN(completedAt.getTime())) return;
      setLastUpdated((current) =>
        !current || completedAt > current ? completedAt : current,
      );
    };
    window.addEventListener(
      "observe-aggregation-completed",
      handleAggregationCompleted,
    );
    return () =>
      window.removeEventListener(
        "observe-aggregation-completed",
        handleAggregationCompleted,
      );
  }, [observeId]);

  useEffect(() => {
    const handleAggregationRefreshState = (event) => {
      if (String(event?.detail?.observeId || "") !== String(observeId || "")) {
        return;
      }
      const sourceId = event?.detail?.sourceId;
      if (!sourceId) return;
      const sources = aggregationRefreshSourcesRef.current;
      if (event.detail.refreshing) sources.add(sourceId);
      else sources.delete(sourceId);
      setIsAggregationRefreshing(sources.size > 0);
    };
    window.addEventListener(
      "observe-aggregation-refresh-state",
      handleAggregationRefreshState,
    );
    return () =>
      window.removeEventListener(
        "observe-aggregation-refresh-state",
        handleAggregationRefreshState,
      );
  }, [observeId]);

  const { data: projectList, isLoading: isLoadingProjects } = useProjectList();

  const projectOptions = useMemo(
    () =>
      projectList?.map(({ id, name }) => ({
        label: name,
        value: id,
      })) || [],
    [projectList],
  );

  // Filter projects based on search text
  const filteredProjectOptions = useMemo(() => {
    if (!debouncedSearchText) {
      return projectOptions;
    }
    return projectOptions.filter((option) =>
      option.label.toLowerCase().includes(debouncedSearchText.toLowerCase()),
    );
  }, [projectOptions, debouncedSearchText]);

  const currentProject = useMemo(() => {
    return projectOptions.find((option) => option.value === observeId);
  }, [projectOptions, observeId]);

  const handleProjectSelect = () => {
    // Always open config dialog if we have a project ID
    if (observeId) {
      setOpenConfigDialog(true);
    }
  };

  const handleProjectChange = (project) => {
    // Clear any cross-project selection state so the new project starts
    // with a clean bulk-actions bar (stale toggledNodes/selectAll carry
    // over IDs from the previous project otherwise). Also ask the grids
    // to clear their own AG Grid selection — the zustand reset alone
    // doesn't touch AG Grid's internal server-side selection model.
    resetTraceGridStore();
    resetSpanGridStore();
    window.dispatchEvent(new CustomEvent("observe-reset-selection"));

    // Get current tab from the URL to preserve it when switching projects
    const pathSegments = location.pathname.split("/");
    let targetPath = "";

    if (pathSegments.includes("users")) {
      // Check if userId exists after "users"
      const usersIndex = pathSegments.indexOf("users");
      const hasUserId =
        pathSegments[usersIndex + 1] &&
        !pathSegments[usersIndex + 1].includes("?");

      if (hasUserId) {
        // If on userdetails → redirect to users list
        targetPath = `/dashboard/observe/${project.value}/users`;
      } else {
        // If on users list → stay on users
        targetPath = `/dashboard/observe/${project.value}/users`;
      }
    } else {
      const currentTab = pathSegments[pathSegments.length - 1];
      targetPath = `/dashboard/observe/${project.value}/${currentTab}`;
    }

    // Reset filters if resetFilters callback is provided (e.g., for LLM Tracing tab)
    if (resetFilters) {
      flushSync(() => resetFilters());
      navigate(targetPath);
      setProjectDropdownOpen(false);
      setSearchText("");
    } else {
      // No filters to reset, navigate immediately
      navigate(targetPath);
      setProjectDropdownOpen(false);
      setSearchText("");
    }
  };

  const handleDropdownClose = () => {
    setProjectDropdownOpen(false);
    setSearchText("");
  };

  const handleDocLink = () => {
    if (isLLMTracingTab) return DOC_LINKS.llmTracing;
    if (isSessionsTab) return DOC_LINKS.sessions;
    if (isUsersTab) return DOC_LINKS.users;

    return DOC_LINKS.llmTracing;
  };

  return (
    <Box display="flex" flexDirection="column" width="100%">
      <Box
        display="flex"
        alignItems="center"
        justifyContent="space-between"
        sx={{ minHeight: 38 }}
      >
        {/* ── Left: Back + Project dropdown + Tag icon ── */}
        <Box display="flex" alignItems="center" gap={1.5}>
          {/* Back button — 26px bordered pill */}
          <Box
            component="button"
            onClick={handleBack}
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              height: 26,
              px: 1.5,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
              bgcolor: "transparent",
              cursor: "pointer",
              fontSize: 14,
              fontWeight: 500,
              fontFamily: "'IBM Plex Sans', sans-serif",
              color: "text.primary",
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <Iconify icon="mdi:chevron-left" width={16} />
            Back
          </Box>

          {/* Project dropdown — 26px bordered */}
          <ProjectDropdownButton
            ref={projectDropdownRef}
            onClick={() => setProjectDropdownOpen(true)}
            endIcon={
              isLoadingProjects ? (
                <CircularProgress size={16} />
              ) : (
                <Iconify icon="eva:chevron-down-fill" />
              )
            }
          >
            <Typography variant="body2" noWrap>
              {currentProject?.label || "Select a project"}
            </Typography>
          </ProjectDropdownButton>

          {/* Project Dropdown Popover */}
          <Popover
            open={projectDropdownOpen}
            anchorEl={projectDropdownRef.current}
            onClose={handleDropdownClose}
            anchorOrigin={{
              vertical: "bottom",
              horizontal: "left",
            }}
            transformOrigin={{
              vertical: "top",
              horizontal: "left",
            }}
            PaperProps={{
              sx: {
                minWidth: projectDropdownRef.current?.clientWidth || 227,
                maxWidth: 400,
              },
            }}
          >
            <Box>
              <FormSearchField
                placeholder="Search projects..."
                size="small"
                searchQuery={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                fullWidth
                autoFocus
                sx={{
                  margin: theme.spacing(1),
                  width: `calc(100% - ${theme.spacing(2)})`,
                }}
                InputProps={{}}
              />
              <Typography
                sx={{
                  paddingX: theme.spacing(1),
                  paddingBottom: theme.spacing(0.5),
                  fontSize: 12,
                  fontWeight: 600,
                  color: "text.disabled",
                }}
              >
                All Projects
              </Typography>
              <Box sx={{ maxHeight: "220px", overflowY: "auto" }}>
                {isLoadingProjects ? (
                  <Box
                    sx={{
                      padding: 2,
                      textAlign: "center",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      gap: 1,
                    }}
                  >
                    <CircularProgress size={20} />
                    <Typography variant="body2" color="text.secondary">
                      Loading projects...
                    </Typography>
                  </Box>
                ) : filteredProjectOptions.length === 0 ? (
                  <Box sx={{ padding: 2, textAlign: "center" }}>
                    <Typography variant="body2" color="text.secondary">
                      {searchText
                        ? "No projects found"
                        : "No projects available"}
                    </Typography>
                  </Box>
                ) : (
                  filteredProjectOptions.map((option) => (
                    <MenuItem
                      key={option.value}
                      onClick={() => handleProjectChange(option)}
                      selected={option.value === observeId}
                      sx={{
                        backgroundColor:
                          option.value === observeId
                            ? "action.selected"
                            : "transparent",
                        "&:hover": {
                          backgroundColor: "action.hover",
                        },
                      }}
                    >
                      <Typography variant="body2" noWrap>
                        {option.label}
                      </Typography>
                    </MenuItem>
                  ))
                )}
              </Box>
            </Box>
          </Popover>
          {/* Tag editor */}
          {observeId && <TagEditor projectId={observeId} variant="header" />}

          {/* Show simulation calls toggle — moved to Display panel */}
        </Box>

        {/* ── Right: Last updated + Auto refresh + Action buttons ── */}
        <Box display="flex" alignItems="center" gap={1}>
          {/* Last updated timestamp */}
          {lastUpdated && (
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                opacity: 0.8,
              }}
            >
              <Iconify
                icon="mdi:clock-outline"
                width={14}
                sx={{ color: "text.secondary" }}
              />
              <Typography
                sx={{
                  fontSize: 12,
                  color: "text.secondary",
                  fontFamily: "'IBM Plex Sans', sans-serif",
                  whiteSpace: "nowrap",
                }}
              >
                Last updated on{" "}
                {lastUpdated.toLocaleDateString("en-GB", {
                  day: "2-digit",
                  month: "2-digit",
                  year: "numeric",
                })}
                ,{" "}
                {lastUpdated
                  .toLocaleTimeString("en-US", {
                    hour: "2-digit",
                    minute: "2-digit",
                    hour12: true,
                  })
                  .toLowerCase()}
              </Typography>
            </Box>
          )}

          {/* Auto refresh toggle — bordered pill */}
          <CustomTooltip
            show
            title={
              autoRefresh
                ? "Disabling Auto-refresh will need manual refresh"
                : "Enabling Auto-refresh updates the data every 10 seconds"
            }
            arrow
            size="small"
            type="black"
          >
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                height: 26,
                px: 1.5,
                bgcolor: "background.neutral",
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "4px",
                cursor: "pointer",
              }}
              onClick={() => setAutoRefresh(!autoRefresh)}
            >
              <Typography
                sx={{
                  fontSize: 13,
                  fontWeight: 500,
                  fontFamily: "'IBM Plex Sans', sans-serif",
                  color: "text.primary",
                  whiteSpace: "nowrap",
                }}
              >
                Auto refresh (10s)
              </Typography>
              <Box
                sx={{
                  width: 27,
                  height: 15,
                  borderRadius: "75px",
                  bgcolor: (theme) =>
                    autoRefresh ? "#7857fc" : theme.palette.divider,
                  position: "relative",
                  transition: "background-color 150ms",
                }}
              >
                <Box
                  sx={{
                    width: 12,
                    height: 12,
                    borderRadius: "50%",
                    bgcolor: "background.paper",
                    position: "absolute",
                    top: 1.5,
                    left: autoRefresh ? 13.5 : 1.5,
                    boxShadow: "0 1.5px 3px rgba(39,39,39,0.1)",
                    transition: "left 150ms",
                  }}
                />
              </Box>
            </Box>
          </CustomTooltip>

          {/* Action buttons — bordered icon squares */}
          <Box display="flex" alignItems="center" gap={1}>
            {/* Reload */}
            <CustomTooltip
              show
              title={
                isAggregationRefreshing ? "Refreshing data" : "Reload data"
              }
              arrow
              size="small"
              type="black"
            >
              <ObserveIconButton
                size="small"
                aria-label={
                  isAggregationRefreshing ? "Refreshing data" : "Reload data"
                }
                disabled={isAggregationRefreshing}
                onClick={() => {
                  // Use refreshData from LLMTracingView if available
                  refreshData?.({ includeAggregations: false });
                  // Keep row/project data fresh. Aggregations listen for the
                  // explicit event below and send `refresh=true` themselves.
                  queryClient.invalidateQueries({
                    queryKey: ["observe-projects"],
                  });
                  // Dispatch a custom event that the grid can listen to
                  window.dispatchEvent(
                    new CustomEvent("observe-refresh", {
                      detail: { observeId },
                    }),
                  );
                }}
              >
                {isAggregationRefreshing ? (
                  <CircularProgress size={14} />
                ) : (
                  <Iconify icon="mdi:refresh" width={16} />
                )}
              </ObserveIconButton>
            </CustomTooltip>

            {/* Exact Observe exports remain fail-closed until a bounded,
                resumable export contract is available. */}
            {(text === "LLM Tracing" || text === "Sessions") && (
              <CustomTooltip
                show
                title="Exact CSV export is temporarily unavailable"
                arrow
                size="small"
                type="black"
              >
                <span>
                  <ObserveIconButton
                    size="small"
                    aria-label="Exact CSV export is temporarily unavailable"
                    disabled
                  >
                    <Iconify icon="mdi:download-outline" width={16} />
                  </ObserveIconButton>
                </span>
              </CustomTooltip>
            )}

            {/* View Docs */}
            <CustomTooltip
              show
              title="View Docs"
              arrow
              size="small"
              type="black"
            >
              <ObserveIconButton
                size="small"
                onClick={() =>
                  window.open(handleDocLink(), "_blank", "noopener,noreferrer")
                }
              >
                <Iconify icon="mdi:book-open-page-variant-outline" width={16} />
              </ObserveIconButton>
            </CustomTooltip>

            {/* Settings/Configure */}
            <CustomTooltip
              show
              title="Settings"
              arrow
              size="small"
              type="black"
            >
              <ObserveIconButton size="small" onClick={handleProjectSelect}>
                <Iconify icon="solar:settings-linear" width={16} />
              </ObserveIconButton>
            </CustomTooltip>

            {/* Share */}
            <CustomTooltip show title="Share" arrow size="small" type="black">
              <ObserveIconButton
                size="small"
                onClick={() => setOpenShareUrl(true)}
              >
                <Iconify icon="basil:share-outline" width={16} />
              </ObserveIconButton>
            </CustomTooltip>
          </Box>
        </Box>
      </Box>

      {/* Share Dialog */}
      <ShareDialog
        open={openShareUrl}
        onClose={() => setOpenShareUrl(false)}
        resourceType="project"
        resourceId={observeId}
      />

      {/* Configure Dialog */}
      <ConfigureProject
        open={openConfigDialog}
        id={observeId}
        module={"observe"}
        onClose={() => {
          queryClient.invalidateQueries({ queryKey: ["project-list"] });
          setOpenConfigDialog(false);
        }}
        refreshGrid={refreshData}
      />
    </Box>
  );
};

ObserveHeader.propTypes = {
  text: PropTypes.string,
  refreshData: PropTypes.func,
  resetFilters: PropTypes.func,
};

export default ObserveHeader;
