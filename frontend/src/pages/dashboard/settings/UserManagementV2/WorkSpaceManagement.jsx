import {
  Box,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Divider,
  InputAdornment,
  TextField,
  Typography,
  styled,
} from "@mui/material";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Helmet } from "react-helmet-async";
import GridTable from "./GridTable";
import { getWorkspaceQueryOptions } from "./getWorkspaceQueryOptions";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import AllActionForm from "./AllActionForm";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import logger from "src/utils/logger";
import PropTypes from "prop-types";
import { useNavigate } from "react-router";
import { APP_CONSTANTS } from "src/utils/constants";
import { useCreateWorkspace } from "src/api/workspaces/create";
import { useSnackbar } from "notistack";
import { useOrganization } from "src/contexts/OrganizationContext";

const SelectionHeader = (props) => {
  const onCheckboxClick = (e) => {
    logger.debug("onCheckboxClick", e);
    e.stopPropagation(); // Stop event from reaching the header
    const api = props.api;
    const { selectAll } = api.getServerSideSelectionState();
    if (selectAll) {
      api.setServerSideSelectionState({ selectAll: false, toggledNodes: [] });
    } else {
      api.setServerSideSelectionState({ selectAll: true, toggledNodes: [] });
    }
  };

  return (
    <div className="">
      <div onClick={onCheckboxClick} className=""></div>
    </div>
  );
};

SelectionHeader.propTypes = {
  api: PropTypes.shape({
    getServerSideSelectionState: PropTypes.func.isRequired,
    setServerSideSelectionState: PropTypes.func.isRequired,
  }).isRequired,
};

const selectionColumnDef = {
  pinned: true,
  lockPinned: true,
  headerComponent: SelectionHeader,
};

const StyledBox = styled(Box)(({ theme }) => ({
  gap: "12px",
  display: "flex",
  alignItems: "center",
  padding: "0px 16px",
  borderRadius: theme.shape.borderRadius,
  border: "1px solid",
  borderColor: theme.palette.action.hover,
}));

const StyledTypography = styled(Typography)(({ theme }) => ({
  color: theme.palette.primary.main,
  fontWeight: theme.typography["fontWeightMedium"], // "500",
  ...theme.typography["s1"],
}));

const StyledButton = styled(Button)(({ theme }) => ({
  color: theme.palette.text.primary,
  fontWeight: theme.typography["fontWeightRegular"], // "500",
  ...theme.typography["s1"],
  "&:hover": {
    backgroundColor: theme.palette.action.hover,
  },
}));

const WorkSpaceManagement = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const { orgLevel } = useOrganization();
  const canCreateWorkspace = typeof orgLevel === "number" && orgLevel >= 8;
  const gridApiRef = useRef(null);
  const overlayTimeoutRef = useRef(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [toggledNodes, setToggleNodes] = useState([]);
  const [selectedAll, setSelectedAll] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [newWorkspaceName, setNewWorkspaceName] = useState("");

  const createMutation = useCreateWorkspace({
    onSuccess: () => {
      enqueueSnackbar("Workspace created", { variant: "success" });
      setCreateOpen(false);
      setNewWorkspaceName("");
      queryClient.invalidateQueries({ queryKey: ["Workspace-detail"] });
      gridApiRef?.current?.api?.refreshServerSide({ purge: true });
    },
    onError: (err) => {
      enqueueSnackbar(
        err?.response?.data?.message || "Failed to create workspace",
        { variant: "error" },
      );
    },
  });

  const handleCreateWorkspace = () => {
    if (!newWorkspaceName.trim()) return;
    createMutation.mutate({
      name: newWorkspaceName.trim(),
      display_name: newWorkspaceName.trim(),
      emails: [],
      role: "workspace_admin", // Backend expects lowercase with underscore, not "Workspace Admin"
    });
  };

  const columnDefs = useMemo(
    () => [
      {
        headerName: "Workspace name",
        field: "display_name",
        flex: 1,
        valueGetter: (params) =>
          params?.data?.display_name || params?.data?.name || "",
      },
      {
        headerName: "Admin names",
        field: "admin_names.0.name",
        flex: 1,
      },
      {
        headerName: "Start date",
        field: "start_data",
        flex: 1,
        valueFormatter: (params) =>
          params?.value
            ? format(new Date(params?.value), "dd/MM/yyyy, h:mm aaa")
            : "",
      },
      {
        headerName: "Last updated date",
        field: "last_update_date",
        flex: 1,
        valueFormatter: (params) =>
          params?.value
            ? format(new Date(params?.value), "dd/MM/yyyy, h:mm aaa")
            : "",
      },
    ],
    [],
  );

  const getDataSource = (queryClient, overlayTimeoutRef, searchQuery) => {
    return {
      getRows: async (params) => {
        const { request } = params;
        const pageNumber = Math.floor(request.startRow / 20);
        const sort = request?.sortModel?.map(({ colId, sort }) => ({
          columnId: colId,
          type: sort === "asc" ? "ascending" : "descending",
        }));
        const search = searchQuery || "";

        if (overlayTimeoutRef.current) {
          clearTimeout(overlayTimeoutRef.current);
          overlayTimeoutRef.current = null;
        }
        try {
          const queryOptions = getWorkspaceQueryOptions(
            {
              pageNumber,
              sort,
              search: search,
            },
            {},
          );
          const data = await queryClient.fetchQuery({ ...queryOptions });
          const rows = data?.data?.results;

          const totalRows = data?.data?.count;

          params.api.setGridOption("context", {
            totalRowCount: totalRows,
          });

          params.success({
            rowData: rows || [],
            rowCount: totalRows,
          });
        } catch (e) {
          params.fail();
          overlayTimeoutRef.current = setTimeout(() => {
            params.api.showLoadingOverlay();
          }, 100);
        }
      },
    };
  };

  const getSelectedRowData = useCallback(() => {
    if (!gridApiRef?.current?.api) {
      return [];
    }

    const selectedNodes = gridApiRef.current.api.getSelectedNodes();
    return selectedNodes
      .map((node) => node.data)
      .filter((data) => data !== undefined);
  }, []);

  const onHeaderClicked = (params) => {
    logger.info(params);
    // const { api, column, event } = params
    // Check if click is from checkbox - if so, don't handle header click
    // if (event?.target?.classList?.contains("ag-selection-checkbox")) {
    //   return;
    // }
    // if (column?.colId === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN) {
    //   const { selectAll } = api.getServerSideSelectionState();
    //   if (selectAll) {
    //     api.setServerSideSelectionState({ selectAll: false, toggledNodes: [] });
    //     setSelectedAll(false);
    //     setToggleNodes([]);
    //   } else {
    //     api.setServerSideSelectionState({ selectAll: true, toggledNodes: [] });
    //     setSelectedAll(true);
    //     setToggleNodes([]);
    //   }
    // }
  };

  const onRowSelectionChanged = ({ api, context, source }) => {
    logger.debug("onRowSelectionChanged", { api, context, source });
    const totalRowCount = context?.totalRowCount;
    const { selectAll, toggledNodes } = api.getServerSideSelectionState();

    if (selectAll && totalRowCount - toggledNodes.length === 0) {
      api.deselectAll();
    }

    setSelectedAll(selectAll);
    const data = getSelectedRowData();
    setToggleNodes(data);
  };

  const unCheckedHandler = () => {
    setSelectedAll(false);
    setToggleNodes([]);
    gridApiRef?.current?.api?.deselectAll?.();
  };

  useEffect(() => {
    if (gridApiRef?.current?.api) {
      const dataSource = getDataSource(
        queryClient,
        overlayTimeoutRef,
        searchQuery,
      );
      gridApiRef.current?.api?.setGridOption(
        "serverSideDatasource",
        dataSource,
      );
      // Optionally refresh the data
      gridApiRef?.current?.api?.refreshServerSide({ purge: true });
    }
  }, [searchQuery, queryClient]);

  const onGridReady = useCallback(
    (params) => {
      const dataSource = getDataSource(
        queryClient,
        overlayTimeoutRef,
        searchQuery,
      );
      params.api.setGridOption("serverSideDatasource", dataSource);
      params.api.setGridOption("onSelectionChanged", onRowSelectionChanged);
      params.api.setGridOption("onColumnHeaderClicked", onHeaderClicked);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [queryClient, searchQuery],
  );

  const selectedCount = useMemo(() => {
    const context = gridApiRef?.current?.api?.getGridOption("context");
    if (selectedAll) {
      return context.totalRowCount - toggledNodes.length;
    } else {
      return toggledNodes.length;
    }
  }, [toggledNodes, selectedAll, gridApiRef]);

  return (
    <>
      <Helmet>
        <title>Workspaces</title>
      </Helmet>
      <Box sx={{ paddingX: "2px" }}>
        <Box>
          <Typography
            sx={{
              typography: "m2",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
          >
            Workspaces
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.primary",
              marginTop: (theme) => theme.spacing(0.5),
            }}
          >
            Manage your organization workspaces
          </Typography>
        </Box>
        <Box
          sx={{
            paddingX: 0,
            paddingY: 2,
            display: "flex",
            gap: 2,
            width: "100%",
            justifyContent: "space-between",
          }}
        >
          <FormSearchField
            autoFocus
            searchQuery={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            size="small"
            sx={{ width: 400 }}
            placeholder="Search by name or email"
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    // @ts-ignore
                    icon="eva:search-fill"
                    sx={{ color: "text.disabled" }}
                  />
                </InputAdornment>
              ),
            }}
          />
          <Box sx={{ display: "flex", gap: 2, alignItems: "center" }}>
            {(selectedAll || toggledNodes.length > 0) && (
              <StyledBox>
                <Box sx={{ display: "flex", alignItems: "center" }}>
                  <StyledTypography>{selectedCount} Selected</StyledTypography>
                </Box>
                <Divider
                  orientation="vertical"
                  flexItem
                  sx={{ height: "24px", alignSelf: "center" }}
                />
                <StyledButton
                  variant="text"
                  size="small"
                  onClick={() => setInviteOpen(true)}
                  startIcon={
                    <Iconify
                      // @ts-ignore
                      icon="solar:share-linear"
                      sx={{ color: "text.primary" }}
                    />
                  }
                  sx={{
                    padding: 0,
                    minWidth: 0,
                    color: "text.primary",
                  }}
                >
                  Share invite
                </StyledButton>
                <Divider
                  orientation="vertical"
                  flexItem
                  sx={{ height: "24px", alignSelf: "center" }}
                />
                <StyledButton
                  variant="text"
                  size="small"
                  onClick={() => unCheckedHandler()}
                  sx={{
                    padding: 0,
                    minWidth: 0,
                    color: "text.primary",
                  }}
                >
                  Cancel
                </StyledButton>
              </StyledBox>
            )}
            {canCreateWorkspace && (
              <Button
                variant="contained"
                size="medium"
                color="primary"
                onClick={() => setCreateOpen(true)}
                startIcon={
                  <Iconify
                    // @ts-ignore
                    icon="mingcute:add-line"
                  />
                }
              >
                Create New Workspace
              </Button>
            )}
          </Box>
        </Box>

        {inviteOpen && (
          <AllActionForm
            openActionForm={{ action: "invite-workspace-user" }}
            onClose={() => setInviteOpen(null)}
            gridApi={gridApiRef?.current?.api}
          />
        )}
        {/* tablde data */}
        <Box sx={{ height: "calc(100vh - 160px)" }}>
          <GridTable
            // @ts-ignore
            onGridReady={onGridReady}
            ref={gridApiRef}
            columnDefs={columnDefs}
            otherGridOption={{
              rowSelection: { mode: "multiRow" },
              selectionColumnDef: selectionColumnDef,
              onCellClicked: (params) => {
                if (
                  params?.column?.getColId() ===
                  APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
                ) {
                  const selected = params.node.isSelected();
                  params.node.setSelected(!selected);
                  return;
                } else {
                  navigate(`/dashboard/settings/workspace/${params.data.id}`, {
                    state: {
                      workspaceName:
                        params?.data?.display_name || params?.data?.name,
                    },
                  });
                }
              },
            }}
          />
        </Box>
      </Box>

      <Dialog
        open={createOpen}
        onClose={() => {
          setCreateOpen(false);
          setNewWorkspaceName("");
        }}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Create New Workspace</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label="Workspace Name"
            fullWidth
            value={newWorkspaceName}
            onChange={(e) => setNewWorkspaceName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCreateWorkspace();
            }}
            placeholder="Enter workspace name"
          />
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => {
              setCreateOpen(false);
              setNewWorkspaceName("");
            }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleCreateWorkspace}
            disabled={!newWorkspaceName.trim() || createMutation.isPending}
          >
            {createMutation.isPending ? "Creating..." : "Create"}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

export default WorkSpaceManagement;
