import { Box, Button, Collapse, LinearProgress } from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "src/routes/hooks";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import { enqueueSnackbar } from "src/components/snackbar";
import { useGetProjectDetails } from "src/api/project/project-detail";
import ComplexFilter from "src/components/ComplexFilter/ComplexFilter";
import { getRandomId } from "src/utils/utils";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import useReverseEvalFilters from "src/hooks/use-reverse-eval-filters";
import Share from "src/components/Share/Share";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import SvgColor from "src/components/svg-color";
import ChooseProjectExperimentWinnerDrawer from "../project/ChooseWinner/ChooseProjectExperimentWinnerDrawer";
import ConfigureProject from "./ConfigureProject";
import CompareRunsDrawer from "./CompareDrawer2/CompareDrawer";
import { generateFilterDefinition, getFilterExtraProperties } from "./common";
import DeleteRuns from "./DeleteRuns";
import RightSection from "./RightSection";
import ColumnConfigureDropDown from "./ColumnDropdown/ColumnConfigureDropDown";
import RunsList from "./RunsList";
import ProjectBreadCrumbs from "./ProjectBreadCrumbs";

const defaultFilter = {
  column_id: "",
  filter_config: {
    filter_type: "",
    filter_op: "",
    filter_value: "",
  },
};

const ProjectDetailView = () => {
  const { projectId } = useParams();
  const columnConfigureRef = useRef();
  const [openColumnConfigure, setOpenColumnConfigure] = useState(false);
  const [columns, setColumns] = useState([]);
  const [openChooseWinnerDrawer, setOpenChooseWinnerDrawer] = useState(false);
  const gridRef = useRef(null);
  const [search, setSearch] = useState("");
  const [selectedRows, setSelectedRows] = useState([]);
  const [deleteRun, setDeleteRun] = useState(false);
  const [openCompareDrawer, setOpenCompareDrawer] = useState(false);
  const debouncedSearch = useDebounce(search.trim(), 500);
  const [openProjectConfigure, setOpenProjectConfigure] = useState(false);
  const [isShareOpen, setShareOpen] = useState(false);
  const [filters, setFilters] = useState([
    { ...defaultFilter, id: getRandomId() },
  ]);
  const [openFilter, setOpenFilter] = useState(false);

  const { mutate: updateProjectColumnVisibility } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.project.updateProjectColumnVisibility(), {
        project_id: projectId,
        visibility: data,
      }),
  });

  const onColumnVisibilityChange = (updatedData) => {
    setColumns((cols) => {
      const newCols = cols.map((col) => ({
        ...col,
        isVisible: updatedData[col.id],
      }));
      return newCols;
    });
    updateProjectColumnVisibility(updatedData);
  };

  const { data: projectDetail, isLoading: isProjectDetailLoading } =
    useGetProjectDetails(projectId);

  const { mutate: exportMutation } = useMutation({
    mutationFn: () => {
      const data = { project_id: projectId };

      if (selectedRows.length > 0) {
        data.runs_ids = selectedRows.map((row) => row.id);
      }

      return axios.post(endpoints.project.exportRuns(), data, {
        responseType: "blob",
      });
    },
    onSuccess: (response) => {
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `Run-${projectDetail?.name}.csv`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      enqueueSnackbar("Runs data downloaded successfully", {
        variant: "success",
      });
      if (selectedRows.length) {
        clearSelection();
      }
    },
  });

  const refreshGrid = () => {
    gridRef.current?.api?.refreshServerSide();
  };

  const updateSelectedRows = () => {
    const api = gridRef.current?.api;
    setSelectedRows(api.getSelectedRows());
  };

  const handleShare = () => {
    setShareOpen(true);
  };

  useEffect(() => {
    const gridRefApi = gridRef.current?.api;
    return () => {
      if (gridRefApi && !gridRefApi.isDestroyed()) {
        // api.removeEventListener("modelUpdated", updateStatusBar);
        gridRefApi?.removeEventListener("selectionChanged", updateSelectedRows);
      }
    };
  }, []);

  const onGridReady = () => {
    const api = gridRef.current?.api;
    // api.addEventListener("modelUpdated", updateStatusBar);
    api.addEventListener("selectionChanged", updateSelectedRows);
  };

  const clearSelection = () => {
    if (gridRef.current) {
      gridRef.current?.api.deselectAll();
      const timeoutId = setTimeout(() => {
        setSelectedRows([]);
      }, 300);

      return () => clearTimeout(timeoutId);
    }
  };

  const onExportClick = () => {
    exportMutation();
  };

  const filterDefinition = useMemo(
    () => generateFilterDefinition(columns),
    [columns],
  );

  const reversePrimaryEvalColumnIds = useMemo(() => {
    return columns.filter((c) => c?.reverseOutput).map((c) => c.id);
  }, [columns]);

  const validatedFilters = useReverseEvalFilters(
    filters,
    reversePrimaryEvalColumnIds,
    getFilterExtraProperties,
  );

  const debouncedValidatedFilters = useDebounce(validatedFilters, 500);

  useEffect(() => {
    trackEvent(Events.pExperimentColumnFilterApplied, {
      [PropertyName.formFields]: { filters: debouncedValidatedFilters },
    });
  }, [debouncedValidatedFilters]);

  const handleProjectConfigure = () => {
    setOpenProjectConfigure(true);
  };

  if (isProjectDetailLoading) {
    return <LinearProgress />;
  }

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        height: "100%",
        padding: "16px",
        display: "flex",
        flexDirection: "column",
        gap: "13px",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <ProjectBreadCrumbs
          links={[
            {
              name: projectDetail?.name || "Project",
              href: "/dashboard/prototype",
            },
            { name: "All runs", href: `/dashboard/prototype/${projectId}` },
          ]}
        />
        <Box sx={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <Button
            variant="outlined"
            size="small"
            // sx={{
            //   color: "text.disabled",
            //   fontSize: "14px",
            //   fontWeight: 500,
            //   width: "119px",
            //   height: "38px",
            // }}
            startIcon={
              <SvgColor src="/assets/icons/action_buttons/ic_download.svg" />
            }
            onClick={onExportClick}
          >
            Export
          </Button>
          <Button
            size="small"
            variant="outlined"
            // sx={{
            //   color: "text.disabled",
            //   fontSize: "14px",
            //   fontWeight: 500,
            //   width: "138px",
            //   height: "38px",
            // }}
            startIcon={
              <SvgColor src="/assets/icons/action_buttons/ic_configure.svg" />
            }
            onClick={handleProjectConfigure}
          >
            Configure
          </Button>
          <Button
            variant="outlined"
            size="small"
            // sx={{
            //   color: "text.disabled",
            //   fontSize: "14px",
            //   fontWeight: 500,
            //   width: "114px",
            //   height: "38px",
            // }}
            startIcon={
              <SvgColor src="/assets/icons/action_buttons/ic_share.svg" />
            }
            onClick={handleShare}
          >
            Share
          </Button>
        </Box>
      </Box>
      <Box>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-end",
          }}
        >
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
            <FormSearchField
              size="small"
              placeholder="Search"
              sx={{ width: "400px" }}
              searchQuery={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </Box>
          <RightSection
            ref={columnConfigureRef}
            setOpenColumnConfigure={setOpenColumnConfigure}
            setOpenChooseWinnerDrawer={setOpenChooseWinnerDrawer}
            selectedRows={selectedRows}
            clearSelection={clearSelection}
            onDelete={() => setDeleteRun(true)}
            onExport={onExportClick}
            onCompare={() => setOpenCompareDrawer(true)}
            setOpenFilter={setOpenFilter}
            filterOpen={openFilter}
            totalFilters={validatedFilters.length}
          />
        </Box>
        <Collapse in={openFilter}>
          <Box sx={{ paddingTop: "13px" }}>
            <ComplexFilter
              filters={filters}
              defaultFilter={defaultFilter}
              setFilters={setFilters}
              filterDefinition={filterDefinition}
              onClose={() => setOpenFilter(false)}
            />
          </Box>
        </Collapse>
      </Box>

      <RunsList
        columns={columns}
        setColumns={setColumns}
        ref={gridRef}
        search={debouncedSearch}
        onGridReady={onGridReady}
        filters={validatedFilters}
        setFilters={setFilters}
        setSelectedRowsData={setSelectedRows}
        setFilterOpen={setOpenFilter}
      />
      <ColumnConfigureDropDown
        open={openColumnConfigure}
        onClose={() => setOpenColumnConfigure(false)}
        anchorEl={columnConfigureRef?.current}
        columns={columns}
        onColumnVisibilityChange={onColumnVisibilityChange}
        setColumns={setColumns}
        useGrouping={true}
      />
      <ChooseProjectExperimentWinnerDrawer
        open={openChooseWinnerDrawer}
        onClose={() => setOpenChooseWinnerDrawer(false)}
        columns={columns}
        refreshGrid={refreshGrid}
      />
      <DeleteRuns
        open={deleteRun}
        onClose={() => {
          setDeleteRun(false);
          clearSelection();
        }}
        selectedRows={selectedRows}
        refreshGrid={refreshGrid}
      />
      <CompareRunsDrawer
        open={openCompareDrawer}
        onClose={() => setOpenCompareDrawer(false)}
        selectedRows={selectedRows}
        projectDetail={projectDetail}
      />
      <ConfigureProject
        id={projectId}
        module={"prototype"}
        open={openProjectConfigure}
        onClose={() => setOpenProjectConfigure(false)}
        refreshGrid={refreshGrid}
      />
      <Share
        open={isShareOpen}
        onClose={() => setShareOpen(false)}
        title="Share as link"
        body="Share this link to give others access to the selected runs. Anyone with the link will be able to view the data."
      />
    </Box>
  );
};

export default ProjectDetailView;
