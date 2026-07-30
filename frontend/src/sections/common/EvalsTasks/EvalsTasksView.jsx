import {
  Badge,
  Box,
  Button,
  CircularProgress,
  Collapse,
  Divider,
  IconButton,
  Typography,
} from "@mui/material";
import React, { useEffect, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import EvalsGrid from "./EvalsGrid";
import NewTaskDrawer from "./NewTaskDrawer/NewTaskDrawer";
import PropTypes from "prop-types";
import ComplexFilter from "src/components/ComplexFilter/ComplexFilter";
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
import HeadingAndSubheading from "src/components/HeadingAndSubheading/HeadingAndSubheading";
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
      <HeadingAndSubheading
        heading={
          observeId === null && (
            <Typography
              color={"text.primary"}
              typography={"m2"}
              fontWeight={"fontWeightBold"}
            >
              Tasks
            </Typography>
          )
        }
        subHeading={
          observeId === null && (
            <Typography
              typography="s1"
              color="text.primary"
              fontWeight={"fontWeightRegular"}
            >
              Create and run automated actions on your data
            </Typography>
          )
        }
      />
      <Box
        sx={{
          flex: 1,
          display: shouldShowLoading ? "flex" : "none",
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        <CircularProgress />
      </Box>
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
                sx={{
                  px: "24px",
                  borderRadius: "8px",
                  height: "38px",
                }}
                startIcon={
                  <Iconify
                    icon="octicon:plus-24"
                    color="background.paper"
                    sx={{
                      width: "20px",
                      height: "20px",
                    }}
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
              <Box
                sx={{
                  display: "flex",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "4px",
                  paddingLeft: 1.5,
                  paddingY: 0.5,
                  alignItems: "center",
                  gap: 1,
                  marginRight: 1.5,
                  height: "38px",
                }}
              >
                <Typography
                  fontWeight="500"
                  typography="s1"
                  color={"primary.main"}
                >
                  {selectedRowsData.length} Selected
                </Typography>
                <Divider
                  orientation="vertical"
                  flexItem
                  sx={{
                    borderRightWidth: "1px",
                    marginLeft: 1,
                    height: "20px",
                    mt: 0.5,
                  }}
                />
                {selectedRowsData.length == 1 && (
                  <Button
                    startIcon={
                      <Iconify
                        icon="lucide:pencil"
                        color="text.disabled"
                        sx={{ width: "14px" }}
                      />
                    }
                    size="small"
                    sx={{ color: "text.primary", fontWeight: 400 }}
                    onClick={handleEdit}
                    disabled={
                      !RolePermission.OBSERVABILITY[
                        PERMISSIONS.ADD_TASKS_ALERTS
                      ][role]
                    }
                  >
                    Edit
                  </Button>
                )}
                <Button
                  startIcon={
                    <SvgColor
                      src="/icons/datasets/delete.svg"
                      sx={{
                        height: "16px",
                        width: "16px",
                        cursor: "pointer",
                        bgcolor: "text.disabled",
                      }}
                    />
                  }
                  size="small"
                  sx={{ color: "text.primary", fontWeight: 400, ml: 0.5 }}
                  onClick={handleDelete}
                  disabled={
                    !RolePermission.OBSERVABILITY[PERMISSIONS.ADD_TASKS_ALERTS][
                      role
                    ]
                  }
                >
                  Delete
                </Button>
                <Divider
                  orientation="vertical"
                  flexItem
                  sx={{
                    borderRightWidth: "1px",
                    marginLeft: 0.5,
                    height: "20px",
                    mt: 0.5,
                    color: "divider",
                  }}
                />
                <Button
                  size="small"
                  sx={{ color: "text.primary", fontWeight: 400 }}
                  onClick={clearSelection}
                >
                  Cancel
                </Button>
              </Box>
            ) : (
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  paddingX: 1.5,
                }}
              >
                <IconButton
                  size="small"
                  sx={{
                    color: "text.primary",
                    borderRadius: 1,
                    paddingX: 1.3,
                    height: "20px",
                    marginRight: 1,
                  }}
                  onClick={() => setShowFilter(!showFilter)}
                >
                  {hasActiveFilter ? (
                    <Badge
                      variant="dot"
                      color="error"
                      overlap="circular"
                      anchorOrigin={{ vertical: "top", horizontal: "right" }}
                    >
                      <SvgColor
                        src={`/assets/icons/components/ic_newfilter.svg`}
                        sx={{
                          color: "text.disabled",
                        }}
                      />
                    </Badge>
                  ) : (
                    <SvgColor
                      src={`/assets/icons/components/ic_newfilter.svg`}
                      sx={{
                        width: "18px",
                        height: "18px",
                        color: "text.secondary",
                      }}
                    />
                  )}
                </IconButton>
                <Button
                  size="small"
                  sx={{
                    padding: 1.3,
                    width: 25,
                    height: 25,
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
                      padding: 1.5,
                      fontSize: "14px",
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
                        icon="ic:round-plus"
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
          <Collapse in={showFilter} sx={{ mt: 0.5 }}>
            <ComplexFilter
              defaultFilter={defaultFilter}
              filterDefinition={EvalTaskFilterDefinition(observeId)}
              filters={filters}
              setFilters={setFilters}
              onClose={() => setShowFilter(false)}
            />
          </Collapse>
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
