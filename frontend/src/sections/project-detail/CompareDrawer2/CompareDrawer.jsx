import React, { useState, useEffect, useMemo } from "react";
import { Drawer, Box, Tabs, Tab } from "@mui/material";
import PropTypes from "prop-types";
import CompareDrawerSkeleton from "src/sections/project-detail/CompareDrawer/Skeletons/CompareDrawerSkeleton";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import CustomBreadcrumbs from "src/components/custom-breadcrumbs";
import { ShowComponent } from "src/components/show";
import { commonBorder } from "src/sections/experiment-detail/ExperimentData/Common";

import CompareNavigateButtons from "../CompareDrawer/CompareNavigateButtons";
import CompareExperimentRightSection from "../CompareDrawer/CompareExperimentRightSection";

import RunEvaluationsWrapper from "./RunEvaluationsWrapper";
import RunDetailsWrapper from "./RunDetailsWrapper";
import RunAnnotationsWrapper from "./RunAnnotationsWrapper";
import RunTraceWrapper from "./RunTraceWrapper";
import DraggableIcon from "./DraggableIcon";

const MIN_GAP = 60; // Minimum gap between the two draggable icons

const normalizeCompareRunTrace = (traceData = {}) => ({
  ...traceData,
  projectVersionName:
    traceData.projectVersionName || traceData.project_version_name,
  evalsMetrics: traceData.evalsMetrics || traceData.evals_metrics || {},
  systemMetrics: normalizeSystemMetrics(
    traceData.systemMetrics || traceData.system_metrics || {},
  ),
  observationSpans:
    traceData.observationSpans || traceData.observation_spans || [],
});

const normalizeSystemMetrics = (systemMetrics = {}) => ({
  ...systemMetrics,
  avgCost: systemMetrics.avgCost ?? systemMetrics.avg_cost ?? 0,
  avgLatencyMs: systemMetrics.avgLatencyMs ?? systemMetrics.avg_latency_ms ?? 0,
});

const normalizeCompareRunsPayload = (payload = {}) => {
  const rawComparison =
    payload.traceComparison || payload.trace_comparison || {};
  const traceComparison = Object.fromEntries(
    Object.entries(rawComparison).map(([projectVersionId, traceData]) => [
      projectVersionId,
      normalizeCompareRunTrace(traceData),
    ]),
  );

  return {
    ...payload,
    traceComparison,
    totalTraces: payload.totalTraces ?? payload.total_traces ?? 0,
    totalEvalConfigs:
      payload.totalEvalConfigs || payload.total_eval_configs || {},
  };
};

const getEvalList = (compareData) => {
  if (!compareData?.traceComparison) {
    return [];
  }
  const eachRun = Object.values(compareData?.traceComparison || {})?.[0];
  if (!eachRun?.evalsMetrics) {
    return [];
  }

  return Object.entries(eachRun?.evalsMetrics || {}).map(
    ([id, evalMetric]) => ({ label: evalMetric?.name, value: id }),
  );
};

const CompareRunsDrawerChild = ({
  open,
  onClose,
  selectedRows,
  projectDetail,
}) => {
  const [currentRow, setCurrentRow] = useState(0);

  const [selectedRuns, setSelectedRuns] = useState(() =>
    selectedRows.map((row) => row.id),
  );

  const [selectedEvals, setSelectedEvals] = useState([]);
  const [activeTab, setActiveTab] = useState(0);

  const {
    data: compareData,
    refetch: refetchCompareData,
    isLoading: isLoadingCompareData,
  } = useQuery({
    queryKey: ["project-compare-runs", currentRow, selectedRuns],
    queryFn: () =>
      axios.post(endpoints.project.compareTraces, {
        project_version_ids: selectedRuns,
        index: currentRow,
      }),
    enabled: selectedRuns.length > 0,
    select: (data) => normalizeCompareRunsPayload(data.data?.result),
  });

  useEffect(() => {
    if (!compareData) {
      return;
    }
    setSelectedEvals(getEvalList(compareData).map((item) => item.value));
  }, [compareData]);

  const evalList = useMemo(() => getEvalList(compareData), [compareData]);

  const traceCount = Object.keys(compareData?.traceComparison || {}).length;

  const containerHeight =
    typeof window !== "undefined" ? window.innerHeight : 800;

  const [middleSectionDragIconTop, setMiddleSectionDragIconTop] = useState(
    containerHeight * 0.333,
  );
  const [bottomSectionDragIconTop, setBottomSectionDragIconTop] = useState(
    containerHeight * 0.555,
  );

  const topSectionHeight = middleSectionDragIconTop;
  const middleSectionHeight =
    bottomSectionDragIconTop - middleSectionDragIconTop - MIN_GAP;
  const bottomSectionHeight =
    containerHeight - bottomSectionDragIconTop - MIN_GAP;

  const totalHeight =
    topSectionHeight + middleSectionHeight + bottomSectionHeight;

  const heightDifference = containerHeight - totalHeight;

  const adjustedTopSectionHeight = topSectionHeight + heightDifference / 2;
  const adjustedMiddleSectionHeight =
    middleSectionHeight + heightDifference / 2;
  const adjustedBottomSectionHeight =
    bottomSectionHeight + heightDifference / 2;

  useEffect(() => {
    if (!open) {
      const initialIcon1Top = containerHeight * 0.322;
      const initialIcon2Top = containerHeight * 0.655;

      setMiddleSectionDragIconTop(initialIcon1Top);
      setBottomSectionDragIconTop(initialIcon2Top);
    }
  }, [open, containerHeight]);

  const renderTraceBlocks = (Component) => {
    const widthPercentage =
      traceCount === 2 ? 50 : traceCount === 3 ? 33.33 : 33.33;

    return Object.entries(compareData?.traceComparison || {}).map(
      ([id, traceData], index) => (
        <Box
          key={id}
          width={`${widthPercentage}vw`}
          sx={{
            height: "100%",
            borderColor: commonBorder.borderColor,
          }}
        >
          <Component
            traceData={traceData}
            selectedEvals={selectedEvals}
            totalRuns={selectedRuns.length}
            index={index}
            refetchCompareData={refetchCompareData}
          />
        </Box>
      ),
    );
  };

  return (
    <>
      <Box
        sx={{
          width: "100vw",
          height: "100vh",
          position: "relative",
          overflowX: "auto",
          overflowY: "hidden",
          "&::-webkit-scrollbar": {
            height: "6px !important",
          },
          "&::-webkit-scrollbar-thumb": {
            backgroundColor: "rgba(0, 0, 0, 0.3)",
            borderRadius: "3px",
          },
          "&::-webkit-scrollbar-track": {
            backgroundColor: "transparent",
          },
        }}
      >
        <ShowComponent condition={selectedRuns.length === 0}>
          <Box
            sx={{
              height: "100%",
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              color: "text.secondary",
              fontSize: "1.2rem",
            }}
          >
            No runs selected for comparison
          </Box>
        </ShowComponent>
        <ShowComponent
          condition={selectedRuns.length > 0 && !isLoadingCompareData}
        >
          <Box sx={{ height: "100%", mt: "80px", position: "relative" }}>
            {/* Top Section - Lowest z-index */}
            <Box
              sx={{
                height: `${adjustedTopSectionHeight}px`,
                minWidth: traceCount < 4 ? "100vw" : "max-content",
                position: "absolute",
                top: 0,
                left: 0,
                zIndex: 1,
                backgroundColor: "background.paper",
              }}
            >
              <Box display="flex" alignItems="center" height="100%">
                {renderTraceBlocks(RunEvaluationsWrapper)}
              </Box>
            </Box>

            {/* Middle Section - Higher z-index */}
            <Box
              sx={{
                height: `${adjustedMiddleSectionHeight}px`,
                borderTop: commonBorder.border,
                borderColor: commonBorder.borderColor,
                minWidth: traceCount < 4 ? "100vw" : "max-content",
                position: "absolute",
                top: `${adjustedTopSectionHeight}px`,
                left: 0,
                zIndex: 2,
                backgroundColor: "background.paper",
              }}
            >
              <Box display="flex" height="100%">
                {renderTraceBlocks(RunDetailsWrapper)}
              </Box>
            </Box>

            {/* Bottom Section - Highest z-index */}
            <Box
              sx={{
                height: `${adjustedBottomSectionHeight - 40}px`,
                borderBottom: commonBorder.border,
                borderTop: commonBorder.border,
                borderColor: commonBorder.borderColor,
                minWidth: traceCount < 4 ? "100vw" : "max-content",
                position: "absolute",
                top: `${adjustedTopSectionHeight + adjustedMiddleSectionHeight}px`,
                left: 0,
                zIndex: 3,
                backgroundColor: "background.paper",
              }}
            >
              <Box sx={{ height: "100%", paddingTop: "58px" }}>
                <ShowComponent condition={activeTab === 0}>
                  <Box display="flex" height="100%">
                    {renderTraceBlocks(RunTraceWrapper)}
                  </Box>
                </ShowComponent>

                <ShowComponent condition={activeTab === 1}>
                  <Box display="flex" height="100%">
                    {renderTraceBlocks(RunAnnotationsWrapper)}
                  </Box>
                </ShowComponent>
              </Box>
            </Box>
          </Box>
        </ShowComponent>
        <ShowComponent
          condition={selectedRuns.length > 0 && isLoadingCompareData}
        >
          <Box
            sx={{
              mt: "100px",
            }}
          >
            <CompareDrawerSkeleton />
          </Box>
        </ShowComponent>
      </Box>
      {open ? (
        <Box
          sx={{
            position: "fixed",
            top: 0,
            left: 0,
            zIndex: 2000,
          }}
        >
          {" "}
          <Box
            sx={{
              height: "80px",
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              borderBottom: commonBorder.border,
              borderColor: commonBorder.borderColor,
            }}
          >
            <Box
              display="flex"
              width="100vw"
              justifyContent="space-between"
              alignItems="center"
              paddingX={2}
              paddingTop={2}
            >
              <CustomBreadcrumbs
                links={[
                  {
                    name: projectDetail?.name || "Project",
                    href: "/dashboard/prototype",
                  },
                  {
                    name: "All Runs",
                    href: "/dashboard/projects/project",
                  },
                ]}
              />
              <CompareExperimentRightSection
                onClose={onClose}
                selectedRuns={selectedRuns}
                setSelectedRuns={setSelectedRuns}
                isLoading={isLoadingCompareData}
                evalList={evalList}
                selectedEvals={selectedEvals}
                setSelectedEvals={setSelectedEvals}
              />
            </Box>
            <Box sx={{ paddingX: 2 }}>
              <CompareNavigateButtons
                totalCount={compareData?.totalTraces}
                currentCount={currentRow + 1}
                onNext={() => {
                  setCurrentRow(currentRow + 1);
                }}
                onPrevious={() => {
                  setCurrentRow(currentRow - 1);
                }}
              />
            </Box>
          </Box>
          {selectedRuns.length > 0 && !isLoadingCompareData && (
            <>
              <DraggableIcon
                top={middleSectionDragIconTop}
                setTop={setMiddleSectionDragIconTop}
                minTop={-1}
                maxTop={bottomSectionDragIconTop - MIN_GAP - 20}
                offset={130}
              />
              <DraggableIcon
                top={bottomSectionDragIconTop}
                setTop={setBottomSectionDragIconTop}
                minTop={middleSectionDragIconTop + MIN_GAP + 20}
                maxTop={containerHeight - 200}
                offset={130}
              />
              <Box
                sx={{
                  position: "absolute",
                  top: `${bottomSectionDragIconTop + 150}px`,
                  zIndex: 2001,
                  width: "100%",
                  borderBottom: commonBorder.border,
                  borderColor: commonBorder.borderColor,
                }}
              >
                <Box
                  sx={{
                    px: 2,
                  }}
                >
                  <Tabs
                    value={activeTab}
                    onChange={(e, newValue) => setActiveTab(newValue)}
                    aria-label="compare tabs"
                    sx={{
                      "& .Mui-selected": {
                        color: "primary.main",
                      },
                      "& .MuiTabs-indicator": {
                        backgroundColor: "primary.main",
                      },
                    }}
                  >
                    <Tab label="Traces" />
                    <Tab label="Annotations" />
                  </Tabs>
                </Box>
              </Box>
            </>
          )}
        </Box>
      ) : null}
    </>
  );
};

CompareRunsDrawerChild.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  selectedRows: PropTypes.array,
  projectDetail: PropTypes.object,
};

const CompareRunsDrawer = ({ open, onClose, selectedRows, projectDetail }) => {
  return (
    <Drawer
      anchor="right"
      open={open}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <CompareRunsDrawerChild
        open={open}
        onClose={onClose}
        selectedRows={selectedRows}
        projectDetail={projectDetail}
      />
    </Drawer>
  );
};

CompareRunsDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  selectedRows: PropTypes.array,
  projectDetail: PropTypes.object,
};

export default CompareRunsDrawer;
