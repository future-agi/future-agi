import React, { useRef, useState } from "react";
import { Paper, Box } from "@mui/material";
import RunInsights from "src/components/run-insights/run-insights";
import Iconify from "src/components/iconify";
import { useParams, useSearchParams } from "src/routes/hooks";
import { ShowComponent } from "src/components/show";
import SpanTab from "src/components/run-insights/spans-tab/spans-tab";
import RunInsideBar from "src/components/run-insights/run-inside-bar";
import TraceTab from "src/components/run-insights/traces-tab/traces-tab";
import ProjectBreadCrumbs from "src/sections/project-detail/ProjectBreadCrumbs";
import {
  useGetProjectDetails,
  useGetProjectVersionDetail,
} from "src/api/project/project-detail";
import TracesTabRightSection from "src/components/run-insights/traces-tab/traces-tab-right";
import ColumnConfigureDropDown from "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown";
import SpanTabRight from "src/components/run-insights/traces-tab/SpanTabRight";
import TraceDetailDrawer from "src/components/traceDetailDrawer/trace-detail-drawer";
import { SelectedNodeProvider } from "src/components/traceDetailDrawer/selectedNodeContext";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { Helmet } from "react-helmet-async";

const RunInsidePage = () => {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const traceGridApiRef = useRef(null);
  const spanGridApiRef = useRef(null);
  const [params, setParams] = useSearchParams({
    tab: "Traces",
  });

  const { projectId, runId } = useParams();

  const { data: projectVersionDetail } = useGetProjectVersionDetail(runId);

  const { data: projectDetail } = useGetProjectDetails(projectId);

  const [traceColumns, setTraceColumns] = useState([]);
  const [spanColumns, setSpanColumns] = useState([]);
  const [openTraceColumnConfigure, setOpenTraceColumnConfigure] =
    useState(false);
  const [openSpanColumnConfigure, setOpenSpanColumnConfigure] = useState(false);
  const traceColumnConfigureRef = useRef(null);
  const spanColumnConfigureRef = useRef(null);
  const [traceDetailDrawerOpen, setTraceDetailDrawerOpen] = useState(null);
  const [selectedTraceIds, setSelectedTraceIds] = useState([]);

  const [traceFilterOpen, setTraceFilterOpen] = useState(false);
  const [spanFilterOpen, setSpanFilterOpen] = useState(false);
  const [isFilterApplied, setIsFilterApplied] = useState(false);

  const currentTab = params.tab;
  const setCurrentTab = (tab) => {
    setIsFilterApplied(false);
    setParams({ tab });
  };

  const { mutate: updateProjectVersionColumnVisibility } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.project.updateProjectVersionColumnVisibility(), {
        project_version_id: runId,
        visibility: data,
      }),
  });

  const onTraceColumnVisibilityChange = (updateObj) => {
    setTraceColumns((cols) =>
      cols.map((col) => ({ ...col, isVisible: updateObj[col.id] })),
    );
    updateProjectVersionColumnVisibility(updateObj);
  };

  const onSpanColumnVisiblityChange = (updateObj) => {
    setSpanColumns((cols) =>
      cols.map((col) => ({ ...col, isVisible: updateObj[col.id] })),
    );
    updateProjectVersionColumnVisibility(updateObj);
  };

  const handleCollapse = () => {
    setIsCollapsed(!isCollapsed);
  };

  const getRightSection = () => {
    if (currentTab === "Traces") {
      return (
        <TracesTabRightSection
          ref={traceColumnConfigureRef}
          setOpenColumnConfigure={setOpenTraceColumnConfigure}
          setOpenFilter={setTraceFilterOpen}
          filterOpen={traceFilterOpen}
          isFilterApplied={isFilterApplied}
        />
      );
    } else if (currentTab === "Spans") {
      return (
        <SpanTabRight
          ref={spanColumnConfigureRef}
          setOpenColumnConfigure={setOpenSpanColumnConfigure}
          setOpenFilter={setSpanFilterOpen}
          filterOpen={spanFilterOpen}
          isFilterApplied={isFilterApplied}
        />
      );
    }
  };
  return (
    <Paper
      elevation={3}
      sx={{
        width: "100%",
        height: "100vh",
        display: "flex",
        flexDirection: "row",
        bgcolor: "background.paper",
        borderRadius: 0,
        position: "relative", // Allows absolute positioning for the button
      }}
    >
      <Helmet>
        <title>Prototype - Run Insights</title>
      </Helmet>
      {/* Left Section */}
      <Box
        sx={{
          flex: isCollapsed ? "95%" : "75%", // Full width when collapsed
          bgcolor: "background.paper",

          borderColor: "divider",
          height: "100%",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Box sx={{ paddingX: "16px", paddingTop: "20px" }}>
          <ProjectBreadCrumbs
            links={[
              {
                name: projectDetail?.name || "Project",
                href: "/dashboard/prototype",
              },
              { name: "All runs", href: `/dashboard/prototype/${projectId}` },
              {
                name: projectVersionDetail?.name || "Project Run",
                href: `/dashboard/prototype/${projectId}/${runId}`,
              },
            ]}
          />
        </Box>

        <Box
          sx={{
            px: "14px",
            mt: 3,
          }}
        >
          <RunInsideBar
            currentTab={currentTab}
            setCurrentTab={setCurrentTab}
            rightSection={getRightSection()}
          />
        </Box>
        <ShowComponent condition={currentTab === "Traces"}>
          <TraceTab
            ref={traceGridApiRef}
            columns={traceColumns}
            setColumns={setTraceColumns}
            setTraceDetailDrawerOpen={setTraceDetailDrawerOpen}
            filterOpen={traceFilterOpen}
            setFilterOpen={setTraceFilterOpen}
            selectedTraceIds={selectedTraceIds}
            setIsFilterApplied={setIsFilterApplied}
          />
        </ShowComponent>
        <ShowComponent condition={currentTab === "Spans"}>
          <SpanTab
            ref={spanGridApiRef}
            columns={spanColumns}
            setColumns={setSpanColumns}
            setTraceDetailDrawerOpen={setTraceDetailDrawerOpen}
            filterOpen={spanFilterOpen}
            setFilterOpen={setSpanFilterOpen}
            setIsFilterApplied={setIsFilterApplied}
          />
        </ShowComponent>
      </Box>
      <ColumnConfigureDropDown
        open={openTraceColumnConfigure}
        onClose={() => setOpenTraceColumnConfigure(false)}
        anchorEl={traceColumnConfigureRef?.current}
        columns={traceColumns}
        onColumnVisibilityChange={onTraceColumnVisibilityChange}
        setColumns={setTraceColumns}
        defaultGrouping="Trace Columns"
      />
      <ColumnConfigureDropDown
        open={openSpanColumnConfigure}
        onClose={() => setOpenSpanColumnConfigure(false)}
        anchorEl={spanColumnConfigureRef?.current}
        columns={spanColumns}
        onColumnVisibilityChange={onSpanColumnVisiblityChange}
        setColumns={setSpanColumns}
        defaultGrouping="Span Columns"
      />
      <SelectedNodeProvider>
        <TraceDetailDrawer
          open={Boolean(traceDetailDrawerOpen)}
          onClose={() => setTraceDetailDrawerOpen(null)}
          traceData={traceDetailDrawerOpen}
          setTraceDetailDrawerOpen={setTraceDetailDrawerOpen}
          viewOptions={{ showEvalLoadingStates: true }}
          onAnnotationChanges={() => {
            if (traceGridApiRef.current) {
              traceGridApiRef.current.api.refreshServerSide();
            }
            if (spanGridApiRef.current) {
              spanGridApiRef.current.api.refreshServerSide();
            }
          }}
        />
      </SelectedNodeProvider>

      {/* Right Section */}
      <Box
        sx={{
          width: isCollapsed ? "12px" : "500px",
          bgcolor: "background.paper",
          position: "relative",
        }}
      >
        <Box
          onClick={handleCollapse}
          sx={{
            position: "absolute",
            top: 58,
            left: -9,
            height: "16px",
            width: "16px",
            cursor: "pointer",
            border: "1px solid",
            borderColor: "text.disabled",
            borderRadius: "20px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: "background.paper",
            "&:hover": {
              bgcolor: "background.neutral", // Background color change on hover
            },
          }}
        >
          <Iconify
            icon="iconamoon:arrow-right-1-light"
            width={12}
            color="text.disabled"
            sx={{ transform: isCollapsed ? "rotate(180deg)" : "" }}
          />
        </Box>
        {isCollapsed ? (
          <Box
            sx={{
              width: "100%",
              height: "100%",
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              border: "1px solid",
              borderColor: "divider",
            }}
          ></Box>
        ) : (
          <>
            <RunInsights setSelectedTraceIds={setSelectedTraceIds} />
          </>
        )}
      </Box>

      {/* Collapse Button */}
    </Paper>
  );
};

export default RunInsidePage;
