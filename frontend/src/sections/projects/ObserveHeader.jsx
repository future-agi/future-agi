import React, { useState, useEffect, useMemo, useRef } from "react";
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
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
// palette import removed — no longer used
import { useUrlState } from "src/routes/hooks/use-url-state";
import { getStorage, setStorage } from "src/hooks/use-local-storage";
import { ShareDialog } from "src/components/share-dialog";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useDebounce } from "src/hooks/use-debounce";
import { objectCamelToSnake } from "src/utils/utils";

import { useProjectList, DOC_LINKS } from "./LLMTracing/common";
import { resetTraceGridStore, resetSpanGridStore } from "./LLMTracing/states";
import TagEditor from "src/sections/project/TagEditor";
import ConfigureProject from "../project-detail/ConfigureProject";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { ObserveIconButton } from "./SharedComponents";
import { useGetProjectDetails } from "src/api/project/project-detail";

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

const ObserveHeader = ({
  text,
  filterTrace,
  filterSpan,
  selectedTab,
  filterSession,
  refreshData,
  resetFilters,
}) => {
  const [openConfigDialog, setOpenConfigDialog] = useState(false);
  const queryClient = useQueryClient();
  const [openShareUrl, setOpenShareUrl] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(() => new Date());
  const [autoRefresh, _setAutoRefresh] = useState(
    () => getStorage("autoRefresh") ?? false,
  );
  const setAutoRefresh = (value) => {
    _setAutoRefresh(value);
    setStorage("autoRefresh", value);
  };
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
        refreshData?.();
        setLastUpdated(new Date());
      }, 10000);
    }
    return () => {
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  }, [autoRefresh, refreshData]);

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

  const { mutate: exportData, isPending: isExportData } = useMutation({
    mutationFn: () => {
      let url;
      let filters;

      if (text === "Sessions") {
        url = endpoints.project.projectSessionListExport;
        filters = filterSession;
      } else if (selectedTab === "spans") {
        url = endpoints.project.getSpansForObserveExport;
        filters = filterSpan;
      } else {
        // Default to trace export
        url = endpoints.project.getTraceForObserveExport;
        filters = filterTrace || [];
      }

      return axios.get(url, {
        params: {
          project_id: observeId,
          filters: JSON.stringify(objectCamelToSnake(filters)),
        },
      });
    },

    onSuccess: (response) => {
      const fileSuffix =
        text === "Sessions"
          ? "sessions"
          : selectedTab === "trace"
            ? "traces"
            : selectedTab === "spans"
              ? "spans"
              : "data";

      enqueueSnackbar(
        `${fileSuffix.charAt(0).toUpperCase() + fileSuffix.slice(1)} downloaded successfully`,
        {
          variant: "success",
        },
      );

      const blob = new Blob([response.data], {
        type: "text/csv;charset=utf-8;",
      });

      const link = document.createElement("a");
      const url = window.URL.createObjectURL(blob);
      link.href = url;
      link.setAttribute(
        "download",
        `${currentProject?.label || "project"}-${fileSuffix}.csv`,
      );
      document.body.appendChild(link);
      link.click();

      link.remove();
      window.URL.revokeObjectURL(url);
    },
  });

  const handleExportClick = () => {
    exportData();
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
              title="Reload data"
              arrow
              size="small"
              type="black"
            >
              <ObserveIconButton
                size="small"
                onClick={() => {
                  // Use refreshData from LLMTracingView if available
                  refreshData?.();
                  setLastUpdated(new Date());
                  // Also invalidate React Query caches
                  queryClient.invalidateQueries({
                    queryKey: ["llm-tracing-graph"],
                  });
                  queryClient.invalidateQueries({
                    queryKey: ["observe-projects"],
                  });
                  queryClient.invalidateQueries({ queryKey: ["callLogs"] });
                  // Dispatch a custom event that the grid can listen to
                  window.dispatchEvent(new CustomEvent("observe-refresh"));
                }}
              >
                <Iconify icon="mdi:refresh" width={16} />
              </ObserveIconButton>
            </CustomTooltip>

            {/* Export/Download */}
            <CustomTooltip
              show
              title={isExportData ? "Exporting..." : "Export CSV"}
              arrow
              size="small"
              type="black"
            >
              <span>
                <ObserveIconButton
                  size="small"
                  onClick={handleExportClick}
                  disabled={isExportData}
                >
                  <Iconify icon="mdi:download-outline" width={16} />
                </ObserveIconButton>
              </span>
            </CustomTooltip>

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
  filterTrace: PropTypes.array,
  filterSpan: PropTypes.array,
  selectedTab: PropTypes.string,
  filterSession: PropTypes.array,
  refreshData: PropTypes.func,
  resetFilters: PropTypes.func,
};

export default ObserveHeader;
