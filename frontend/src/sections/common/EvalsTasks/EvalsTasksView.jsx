import SelectionBar from "src/components/selection-bar";
import { Box, Button, Divider, IconButton, Typography } from "@mui/material";
import { LoadingScreen } from "src/components/loading-screen";
import React, { useEffect, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import EvalsGrid from "./EvalsGrid";
import NewTaskDrawer from "./NewTaskDrawer/NewTaskDrawer";
import PropTypes from "prop-types";
import { FilterButton, useComplexFilterBinding } from "src/components/filter";
import { EvalTaskFilterDefinition } from "./common";
import { getRandomId } from "src/utils/utils";
import { useGetValidatedFilters } from "src/hooks/use-get-validated-filters";
import { useGetProjectById } from "src/api/project/evals-task";
import DeleteConfirmation from "./DeleteConfirmation";
import axiosInstance, { endpoints } from "src/utils/axios";
import { useSnackbar } from "notistack";
import SvgColor from "src/components/svg-color";
import { useDebounce } from "src/hooks/use-debounce";
import EditTaskDrawer from "./EditTaskDrawer/EditTaskDrawer";
import ColumnConfigureDropDown from "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import PageHeader from "src/components/page-header";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { handleOnDocsClicked } from "src/utils/Mixpanel";

const defaultFilter = {
  column_id: "",
  filter_config: {
    filter_type: "",
    filter_op: "",
    filter_value: "",
  },
};

const EvalsTasksView = ({ observeId = null }) => {
  const { role } = useAuthContext();
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [columns, setColumns] = useState([]);
  const gridRef = useRef(null);
  const columnConfigureRef = useRef(null);
  const [openColumnConfigure, setOpenColumnConfigure] = useState(false);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [selectedRowsData, setSelectedRowsData] = useState([]);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [openDrawer, setOpenDrawer] = useState(null);
  const [isView, setIsView] = useState(false);
  const [hasData, setHasData] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [searchState, setSearchState] = useState("loading");

  const { enqueueSnackbar } = useSnackbar();
  const [searchQuery, setSearchQuery] = useState("");

  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);

  const [filters, setFilters] = useState([
    { ...defaultFilter, id: getRandomId() },
  ]);
  const [showFilter, setShowFilter] = useState(false);
  const filterDefinition = React.useMemo(
    () => EvalTaskFilterDefinition(observeId),
    [observeId],
  );
  const filterBinding = useComplexFilterBinding({
    filters,
    setFilters,
    filterDefinition,
    defaultFilter,
  });

  const onColumnVisibilityChange = (updatedData) => {
    setColumns((cols) => {
      const newCols = cols.map((col) => ({
        ...col,
        isVisible: updatedData[col.id],
      }));
      return newCols;
    });
  };

  const { data: projectDetails } = useGetProjectById(observeId, {
    enabled: !!observeId,
  });

  const toggleDrawer = (open) => {
    setIsDrawerOpen(open);
  };

  const refreshGrid = () => {
    gridRef.current?.api?.refreshServerSide();
  };

  const clearSelection = () => {
    if (gridRef.current) {
      gridRef.current?.api.deselectAll();
      const timeoutId = setTimeout(() => {
        setSelectedRowsData([]);
      }, 300);

      return () => clearTimeout(timeoutId);
    }
  };

  const handleDelete = () => {
    setDeleteModalOpen(true);
  };

  const handleEdit = () => {
    if (selectedRowsData.length === 1) {
      setOpenDrawer(selectedRowsData[0]);
      setIsView(true);
    }
  };

  const confirmDelete = async () => {
    if (gridRef.current) {
      gridRef.current?.api.deselectAll();
      setDeleteLoading(true);
      try {
        const ids = selectedRowsData.map((row) => row.id);

        await axiosInstance.post(endpoints.project.markEvalsDeleted(), {
          evalTaskIds: ids,
        });

        const deletedTitles = selectedRowsData.map((row) => row.name);

        const message =
          deletedTitles.length > 1
            ? `${deletedTitles.length} tasks have been deleted`
            : "Task has been deleted";

        setDeleteModalOpen(false);
        enqueueSnackbar(message, { variant: "success" });

        gridRef?.current?.api?.refreshServerSide();
        setSelectedRowsData([]);
        setDeleteLoading(false);
      } catch (error) {
        enqueueSnackbar(error.message, { variant: "error" });
      } finally {
        setDeleteLoading(false);
      }
    }
  };

  const confirmUpdate = () => {
    clearSelection();
    refreshGrid();
  };

  const validatedFilters = useGetValidatedFilters(filters);

  const handleOpenColumnConfig = (event) => {
    columnConfigureRef.current = event.currentTarget;
    setOpenColumnConfigure(true);
  };

  const hasActiveFilter = React.useMemo(() => {
    return filters?.some((f) =>
      f.filter_config?.filter_value &&
      Array.isArray(f.filter_config.filter_value)
        ? f.filter_config.filter_value.length > 0
        : f.filter_config.filter_value !== "",
    );
  }, [filters]);

  useEffect(() => {
    if (debouncedSearchQuery === "") {
      setSearchState("loading");
      setIsLoading(true);
    } else {
      setSearchState("searching");
      setIsLoading(true);
    }
  }, [debouncedSearchQuery]);

  const shouldShowEmptyLayout =
    hasData === false &&
    !isLoading &&
    searchState === "empty" &&
    !hasActiveFilter;
  const shouldShowGrid =
    hasData === true ||
    (isLoading && searchState !== "empty") ||
    searchState === "searching" ||
    hasActiveFilter;

  const shouldShowLoading = isLoading && hasData === null;

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        height: "100%",
        padding: 2,
        // paddingBottom: "15px",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {observeId === null && (
        <PageHeader
          title="Tasks"
          description="Create and run automated actions on your data"
          sx={{ mb: 0 }}
        />
      )}
      <LoadingScreen
        variant="orbit"
        sx={{ flex: 1, display: shouldShowLoading ? "flex" : "none" }}
      />
      <Box
        sx={{
          flex: 1,
          visibility: shouldShowLoading ? "hidden" : "visible",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Box
          sx={{
            display: shouldShowEmptyLayout ? "block" : "none",
            flex: 1,
          }}
        >
          <EmptyLayout
            title="Add your first task"
            description="Run functions on your data to evaluate LLM outputs, automate testing, and track performance in one place"
            link="https://docs.futureagi.com/docs/observe/features/evals"
            linkText="Check docs"
            onLinkClick={() => handleOnDocsClicked("tasks_page")}
            action={
              <Button
                variant="contained"
                color="primary"
                startIcon={
                  <Iconify
                    width={20}
                    icon="solar:add-linear"
                    color="background.paper"
                  />
                }
                onClick={() => toggleDrawer(true)}
                disabled={
                  !RolePermission.OBSERVABILITY[PERMISSIONS.ADD_TASKS_ALERTS][
                    role
                  ]
                }
              >
                <Typography variant="s1" fontWeight={"fontWeightSemiBold"}>
                  Add Task
                </Typography>
              </Button>
            }
            icon="/assets/icons/navbar/ic_dash_tasks.svg"
          />
        </Box>
        <Box
          sx={{
            display: shouldShowGrid ? "flex" : "none",
            flexDirection: "column",
            height: "100%",
          }}
        >
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              mt: observeId === null && 1,
              alignItems: "center",
            }}
          >
            <FormSearchField
              size="small"
              placeholder="Search"
              sx={{ minWidth: "360px", mt: observeId === null && 2 }}
              searchQuery={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {selectedRowsData.length > 0 ? (
              <SelectionBar
                count={selectedRowsData.length}
                onCancel={clearSelection}
                sx={{ mr: 1.5 }}
                actions={[
                  {
                    label: "Edit",
                    icon: "solar:pen-linear",
                    onClick: handleEdit,
                    hidden: selectedRowsData.length !== 1,
                    disabled:
                      !RolePermission.OBSERVABILITY[
                        PERMISSIONS.ADD_TASKS_ALERTS
                      ][role],
                  },
                  {
                    label: "Delete",
                    icon: "solar:trash-bin-trash-linear",
                    onClick: handleDelete,
                    disabled:
                      !RolePermission.OBSERVABILITY[
                        PERMISSIONS.ADD_TASKS_ALERTS
                      ][role],
                  },
                ]}
              />
            ) : (
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  paddingX: 1.5,
                }}
              >
                <Box sx={{ mr: 1 }}>
                  <FilterButton
                    fields={filterBinding.fields}
                    value={filterBinding.value}
                    onChange={filterBinding.onChange}
                    open={showFilter}
                    onOpenChange={setShowFilter}
                  />
                </Box>
                <Button
                  size="small"
                  sx={{
                    width: 25,
                    marginRight: 1.3,
                    minWidth: 0,
                    "& .MuiButton-startIcon": {
                      margin: 0,
                    },
                  }}
                  onClick={handleOpenColumnConfig}
                >
                  <IconButton>
                    <SvgColor
                      src="/assets/icons/action_buttons/ic_column.svg"
                      sx={{
                        height: "16px",
                        width: "16px",
                        color: "text.primary",
                      }}
                    />
                  </IconButton>{" "}
                </Button>
                <Divider
                  orientation="vertical"
                  variant="middle"
                  sx={{
                    borderRightWidth: "1px",
                    marginLeft: 0.5,
                    mr: 2.5,
                    height: "20px",
                    mt: observeId === null ? 2 : 1.2,
                    color: "divider",
                  }}
                  flexItem
                />
                <Box sx={{ display: "flex", gap: 1.5, alignItems: "center" }}>
                  <Button
                    variant="outlined"
                    sx={{
                      color: "text.primary",
                      borderColor: "divider",
                    }}
                    startIcon={
                      <SvgColor src="/assets/icons/ic_docs_single.svg" />
                    }
                    component="a"
                    href="https://docs.futureagi.com/docs/observe/features/evals"
                    target="_blank"
                  >
                    View Docs
                  </Button>
                  <Button
                    startIcon={
                      <Iconify
                        icon="solar:add-linear"
                        color="primary"
                        width="20px"
                      />
                    }
                    variant="contained"
                    color="primary"
                    sx={{
                      marginLeft: "auto",
                      // typography: "s1",
                      // fontWeight: "fontWeightMedium",
                      // px: theme => theme.spacing(3),
                    }}
                    onClick={() => toggleDrawer(true)}
                    disabled={
                      !RolePermission.OBSERVABILITY[
                        PERMISSIONS.ADD_TASKS_ALERTS
                      ][role]
                    }
                  >
                    New Task
                  </Button>
                </Box>
              </Box>
            )}
          </Box>
          <EvalsGrid
            ref={gridRef}
            columns={columns}
            setColumns={setColumns}
            filters={validatedFilters}
            setSelectedRowsData={setSelectedRowsData}
            observeId={observeId}
            debouncedSearchQuery={debouncedSearchQuery}
            setOpenDrawer={setOpenDrawer}
            setIsView={setIsView}
            setHasData={setHasData}
            setIsLoading={setIsLoading}
            setSearchState={setSearchState}
            hasActiveFilter={hasActiveFilter}
          />
        </Box>
      </Box>
      <ColumnConfigureDropDown
        open={openColumnConfigure}
        onClose={() => setOpenColumnConfigure(false)}
        anchorEl={columnConfigureRef.current}
        columns={columns}
        setColumns={setColumns}
        onColumnVisibilityChange={onColumnVisibilityChange}
        useGrouping={false}
      />
      <EditTaskDrawer
        open={Boolean(openDrawer)}
        onClose={() => {
          setOpenDrawer(null);
          setIsView(false);
        }}
        selectedRow={openDrawer}
        observeId={observeId}
        refreshGrid={confirmUpdate}
        isEdit={true}
        isView={isView}
      />
      <NewTaskDrawer
        open={isDrawerOpen}
        onClose={() => {
          toggleDrawer(false);
          setIsView(false);
        }}
        projectDetails={projectDetails}
        refreshGrid={refreshGrid}
        observeId={observeId}
      />

      <DeleteConfirmation
        selectedItems={selectedRowsData}
        open={deleteModalOpen}
        onClose={() => setDeleteModalOpen(false)}
        onConfirm={confirmDelete}
        isLoading={deleteLoading}
      />
    </Box>
  );
};

EvalsTasksView.propTypes = {
  observeId: PropTypes.string,
};

export default EvalsTasksView;
