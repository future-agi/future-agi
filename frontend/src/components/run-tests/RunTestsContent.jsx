/* eslint-disable react/prop-types */
import {
  alpha,
  Box,
  Button,
  Chip,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Popover,
  Stack,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { ShowComponent } from "src/components/show";
import CustomDialog from "src/sections/develop-detail/Common/CustomDialog/CustomDialog";

import { useAuthContext } from "src/auth/hooks";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

import { SIMULATION_TYPE } from "./common";

// ── Hover popover with chip list (same pattern as TaskListView) ──

function HoverChipList({ items, label, emptyText, getLabel, getTooltip }) {
  const [anchorEl, setAnchorEl] = useState(null);
  const open = Boolean(anchorEl);

  const resolveLabel = (item) => {
    if (!item) return "—";
    if (typeof item === "string") return item;
    return getLabel ? getLabel(item) : item.name || "—";
  };

  if (!items?.length) {
    return (
      <Typography variant="caption" color="text.disabled">
        {emptyText}
      </Typography>
    );
  }

  const firstItem = resolveLabel(items[0]);
  const remaining = items.length - 1;

  const chipStyles = {
    backgroundColor: (theme) =>
      alpha(
        theme.palette.primary.main,
        theme.palette.mode === "dark" ? 0.24 : 0.1,
      ),
    "&:hover": {
      backgroundColor: (theme) =>
        alpha(
          theme.palette.primary.main,
          theme.palette.mode === "dark" ? 0.32 : 0.16,
        ),
    },
    color: (theme) =>
      theme.palette.mode === "dark"
        ? theme.palette.primary.light
        : theme.palette.primary.main,
    border: "1px solid",
    borderColor: (theme) =>
      alpha(
        theme.palette.primary.main,
        theme.palette.mode === "dark" ? 0.4 : 0.2,
      ),
    borderRadius: "4px",
    fontWeight: 500,
    fontSize: "12px",
    height: 22,
    "& .MuiChip-label": { px: 0.75 },
  };

  return (
    <>
      <Box
        onMouseEnter={(e) => setAnchorEl(e.currentTarget)}
        onMouseLeave={() => setAnchorEl(null)}
        sx={{
          display: "flex",
          alignItems: "center",
          height: "100%",
          gap: 0.5,
          minWidth: 0,
        }}
      >
        <Chip
          label={firstItem}
          size="small"
          sx={{ ...chipStyles, maxWidth: 180 }}
        />
        {remaining > 0 && (
          <Typography
            variant="caption"
            sx={{ color: "text.secondary", fontSize: "12px", pl: 0.5 }}
          >
            +{remaining} other{remaining > 1 ? "s" : ""}
          </Typography>
        )}
      </Box>
      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={() => setAnchorEl(null)}
        sx={{ pointerEvents: "none" }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        disableRestoreFocus
        PaperProps={{
          sx: {
            pointerEvents: "auto",
            p: 1.5,
            maxWidth: 360,
            maxHeight: 320,
            overflowY: "auto",
            bgcolor: "background.paper",
            boxShadow: (theme) =>
              theme.customShadows?.dropdown || theme.shadows[8],
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
          },
        }}
      >
        <Typography
          variant="caption"
          fontWeight={600}
          sx={{ display: "block", mb: 1, color: "text.primary" }}
        >
          {label} ({items.length})
        </Typography>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
          {items.map((item, idx) => {
            const labelText = resolveLabel(item);
            const tooltip = getTooltip ? getTooltip(item) : "";
            const chip = (
              <Chip
                label={labelText}
                size="small"
                sx={{
                  ...chipStyles,
                  alignSelf: "flex-start",
                  maxWidth: "100%",
                }}
              />
            );
            return (
              <Box key={idx}>
                {tooltip ? (
                  <Tooltip title={tooltip} placement="right" arrow>
                    <Box sx={{ display: "inline-flex", maxWidth: "100%" }}>
                      {chip}
                    </Box>
                  </Tooltip>
                ) : (
                  chip
                )}
              </Box>
            );
          })}
        </Box>
      </Popover>
    </>
  );
}

// ── Row actions menu (inlined so DataTable cell can stopPropagation) ──

function RunTestActionMenu({ test, onView, onDelete }) {
  const [anchorEl, setAnchorEl] = useState(null);
  const open = Boolean(anchorEl);
  const theme = useTheme();

  const handleOpen = (event) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
  };
  const handleClose = () => setAnchorEl(null);

  return (
    <>
      <Button
        variant="outlined"
        size="small"
        onClick={handleOpen}
        sx={{
          display: "flex",
          gap: 1,
          borderRadius: theme.spacing(1),
          borderColor: `${theme.palette.divider} !important`,
          minWidth: 71,
          px: 1,
          py: 0.25,
        }}
      >
        <SvgColor
          sx={{ bgcolor: "text.disabled", height: 16.5, width: 16.5 }}
          src="/assets/icons/action_buttons/ic_configure.svg"
        />
        <SvgColor
          sx={{
            bgcolor: "text.disabled",
            height: 16.5,
            width: 16.5,
            transform: "rotate(90deg)",
          }}
          src="/assets/icons/custom/lucide--chevron-right.svg"
        />
      </Button>
      <Menu
        anchorEl={anchorEl}
        open={open}
        onClose={handleClose}
        PaperProps={{ sx: { mt: 1, minWidth: 100, p: 0.5 } }}
        transformOrigin={{ horizontal: "right", vertical: "top" }}
        anchorOrigin={{ horizontal: "right", vertical: "bottom" }}
      >
        <MenuItem
          onClick={(e) => {
            e.stopPropagation();
            onView?.(test);
            handleClose();
          }}
          sx={{ px: 1.25, py: 0.75 }}
        >
          <ListItemIcon sx={{ minWidth: "unset", mr: 1 }}>
            <SvgColor
              src="/assets/icons/ic_hide.svg"
              sx={{ width: 20, height: 20 }}
            />
          </ListItemIcon>
          <ListItemText
            primary="View details"
            primaryTypographyProps={{ typography: "s1", fontWeight: 500 }}
          />
        </MenuItem>
        <MenuItem
          onClick={(e) => {
            e.stopPropagation();
            onDelete?.(test);
            handleClose();
          }}
          sx={{ color: "error.main", px: 1.25, py: 0.75 }}
        >
          <ListItemIcon sx={{ minWidth: "unset", mr: 1 }}>
            <SvgColor
              src="/assets/icons/ic_delete.svg"
              sx={{ height: 20, width: 20 }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Delete"
            primaryTypographyProps={{ typography: "s1", fontWeight: 500 }}
          />
        </MenuItem>
      </Menu>
    </>
  );
}

// ── Component ──

const RunTestsContent = ({
  simulationType,
  promptTemplateId = null,
  showHeader = true,
  showSearch = true,
  onRowClick: customRowClick = null,
  onCreateClick: customCreateClick = null,
  createButtonText = "Create a Simulation",
  emptyTitle = "No tests found",
  emptyDescription = "Get started by creating your first test to evaluate your AI agents.",
  emptyIcon = "/assets/icons/navbar/ic_evaluate.svg",
  gridRef: externalGridRef = null,
}) => {
  const { role } = useAuthContext();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [searchQuery, setSearchQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState(""); // "" = All types
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [testToDelete, setTestToDelete] = useState(null);

  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["run-tests"] });
  }, [queryClient]);

  // Backward-compat shim: expose `ref.current.api.refreshServerSide({ purge })`
  // so existing callers (RunTests.jsx, SimulateContent.jsx) that still use the
  // AG-Grid-style imperative refresh keep working.
  useEffect(() => {
    if (!externalGridRef) return;
    externalGridRef.current = {
      api: {
        refreshServerSide: () => invalidate(),
      },
    };
  }, [externalGridRef, invalidate]);

  const handleRowClick = useCallback(
    (row) => {
      if (!row?.id) return;
      trackEvent(Events.runTestTaskClicked, { [PropertyName.id]: row.id });
      if (customRowClick) {
        customRowClick(row);
      } else {
        navigate(`/dashboard/simulate/test/${row.id}`);
      }
    },
    [customRowClick, navigate],
  );

  const { mutate: deleteTest, isPending: deleteTestPending } = useMutation({
    mutationFn: (data) => axios.delete(endpoints.runTests.detail(data.id)),
    onSuccess: () => {
      enqueueSnackbar({
        message: "Test deleted successfully",
        variant: "success",
      });
      setTestToDelete(null);
      invalidate();
    },
  });

  const handleDeleteTest = useCallback((data) => setTestToDelete(data), []);

  const handleAddRunTest = useCallback(() => {
    trackEvent(Events.runTestCreatenewtaskClicked, {
      [PropertyName.click]: true,
    });
    customCreateClick?.();
  }, [customCreateClick]);

  const queryParams = useMemo(() => {
    const params = {
      page: page + 1,
      limit: pageSize,
      search: debouncedSearchQuery,
      simulation_type: simulationType,
      ...(typeFilter && { agent_type: typeFilter }),
    };
    if (simulationType === SIMULATION_TYPE.PROMPT && promptTemplateId) {
      params.prompt_template_id = promptTemplateId;
    }
    return params;
  }, [
    page,
    pageSize,
    debouncedSearchQuery,
    simulationType,
    promptTemplateId,
    typeFilter,
  ]);

  const { isPending, data } = useQuery({
    queryKey: [
      "run-tests",
      simulationType,
      promptTemplateId,
      page,
      pageSize,
      debouncedSearchQuery,
      typeFilter,
    ],
    queryFn: () => axios.get(endpoints.runTests.list, { params: queryParams }),
    select: (d) => d.data,
    keepPreviousData: true,
  });

  const items = useMemo(() => data?.results ?? [], [data]);
  const total = data?.count ?? 0;
  const hasData = items.length > 0;

  const showEmptyScreen =
    !isPending &&
    data &&
    total === 0 &&
    debouncedSearchQuery === "" &&
    !typeFilter;

  const canManage =
    RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role] &&
    RolePermission.SIMULATION_AGENT[PERMISSIONS.DELETE][role];

  const columns = useMemo(() => {
    const cols = [
      {
        id: "name",
        accessorKey: "name",
        header: "Name",
        meta: { flex: 1.4 },
        minSize: 180,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography
            variant="body2"
            noWrap
            sx={{ fontWeight: 500, color: "text.primary" }}
          >
            {getValue()}
          </Typography>
        ),
      },
      {
        id: "agent",
        accessorKey: "agent_definition_detail",
        header: "Agent",
        size: 200,
        enableSorting: false,
        cell: ({ row }) => {
          const agent = row.original.agent_definition_detail;
          if (!agent?.agent_name) {
            return (
              <Typography variant="caption" color="text.disabled">
                —
              </Typography>
            );
          }
          const tooltip = [
            `Name: ${agent.agent_name}`,
            agent.agent_type ? `Type: ${agent.agent_type}` : null,
            agent.provider ? `Provider: ${agent.provider}` : null,
            agent.contact_number ? `Phone: ${agent.contact_number}` : null,
          ]
            .filter(Boolean)
            .join("\n");
          return (
            <Tooltip
              title={<Box sx={{ whiteSpace: "pre-line" }}>{tooltip}</Box>}
              placement="top"
              arrow
            >
              <Typography
                variant="body2"
                noWrap
                sx={{ fontSize: 13, color: "text.primary" }}
              >
                {agent.agent_name}
              </Typography>
            </Tooltip>
          );
        },
      },
      {
        id: "scenarios",
        accessorKey: "scenarios_detail",
        header: "Scenarios",
        size: 240,
        enableSorting: false,
        cell: ({ row }) => (
          <HoverChipList
            items={row.original.scenarios_detail || []}
            label="Scenarios"
            emptyText="—"
            getLabel={(item) => item.name}
            getTooltip={(item) =>
              [
                item.name ? `Name: ${item.name}` : null,
                item.scenario_type_display
                  ? `Type: ${item.scenario_type_display}`
                  : item.scenario_type
                    ? `Type: ${item.scenario_type}`
                    : null,
                item.dataset_rows != null
                  ? `Datapoints: ${item.dataset_rows}`
                  : null,
                item.description ? `\n${item.description}` : null,
              ]
                .filter(Boolean)
                .join("\n")
            }
          />
        ),
      },
      {
        id: "evals",
        accessorKey: "evals_detail",
        header: "Evals",
        size: 240,
        enableSorting: false,
        cell: ({ row }) => (
          <HoverChipList
            items={row.original.evals_detail || []}
            label="Evals"
            emptyText="—"
            getLabel={(item) => item.name}
            getTooltip={(item) =>
              [
                item.name ? `Name: ${item.name}` : null,
                item.model_type ? `Model: ${item.model_type}` : null,
                item.status ? `Status: ${item.status}` : null,
                item.eval_group ? `Group: ${item.eval_group}` : null,
              ]
                .filter(Boolean)
                .join("\n")
            }
          />
        ),
      },
      {
        id: "last_run_at",
        accessorKey: "last_run_at",
        header: "Last Run",
        size: 150,
        enableSorting: false,
        cell: ({ row }) => {
          const v = row.original.last_run_at;
          return (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ fontSize: 13 }}
            >
              {v ? formatDistanceToNow(new Date(v), { addSuffix: true }) : "-"}
            </Typography>
          );
        },
      },
    ];
    if (canManage) {
      cols.push({
        id: "actions",
        accessorKey: "id",
        header: "",
        size: 100,
        enableSorting: false,
        cell: ({ row }) => (
          <Box
            onClick={(e) => e.stopPropagation()}
            sx={{ display: "flex", alignItems: "center" }}
          >
            <RunTestActionMenu
              test={row.original}
              onView={handleRowClick}
              onDelete={handleDeleteTest}
            />
          </Box>
        ),
      });
    }
    return cols;
  }, [canManage, handleRowClick, handleDeleteTest]);

  return (
    <>
      <Box
        sx={{
          display: "flex",
          backgroundColor: showHeader ? "background.paper" : "transparent",
          flexDirection: "column",
          height: "100%",
          gap: 1.5,
          minHeight: 0,
          overflow: "hidden",
        }}
      >
        <ShowComponent condition={showHeader}>
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <Box>
              <Typography typography="m2" fontWeight={600}>
                Run Simulation
              </Typography>
              <Typography typography="s1" color="text.secondary">
                Create and manage comprehensive tests for your AI agents with
                scenarios, evaluations, and automated runs.
              </Typography>
            </Box>
          </Box>
        </ShowComponent>

        {showEmptyScreen ? (
          <EmptyLayout
            title={emptyTitle}
            description={emptyDescription}
            action={
              <Button
                variant="contained"
                startIcon={<Iconify icon="eva:plus-fill" />}
                onClick={handleAddRunTest}
                sx={{
                  bgcolor: "primary.main",
                  "&:hover": { bgcolor: "primary.dark" },
                }}
                disabled={
                  !RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role]
                }
              >
                {createButtonText}
              </Button>
            }
            icon={emptyIcon}
          />
        ) : (
          <>
            {/* Search + create */}
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 2,
              }}
            >
              <ShowComponent condition={showSearch}>
                <Stack direction="row" spacing={1.5} alignItems="center">
                  <FormSearchField
                    value={searchQuery}
                    onChange={(e) => {
                      setSearchQuery(e.target.value);
                      setPage(0);
                    }}
                    size="small"
                    placeholder="Search"
                    sx={{
                      minWidth: "250px",
                      "& .MuiOutlinedInput-root": { height: "30px" },
                    }}
                  />
                  <TextField
                    select
                    size="small"
                    value={typeFilter}
                    onChange={(e) => {
                      setTypeFilter(e.target.value);
                      setPage(0); // reset to first page when the filter changes
                    }}
                    SelectProps={{ displayEmpty: true }}
                    sx={{
                      minWidth: 140,
                      "& .MuiOutlinedInput-root": { height: "30px" },
                    }}
                  >
                    <MenuItem value="">All types</MenuItem>
                    <MenuItem value={AGENT_TYPES.VOICE}>Voice</MenuItem>
                    <MenuItem value={AGENT_TYPES.CHAT}>Chat</MenuItem>
                  </TextField>
                </Stack>
              </ShowComponent>
              <ShowComponent condition={!showSearch}>
                <Box />
              </ShowComponent>
              <ShowComponent condition={hasData || debouncedSearchQuery !== ""}>
                <Box sx={{ display: "flex", gap: 1.5, alignItems: "center" }}>
                  <ShowComponent condition={showHeader}>
                    <Button
                      variant="outlined"
                      size="small"
                      sx={{
                        color: "text.primary",
                        borderColor: "divider",
                        px: 1.5,
                        height: 38,
                      }}
                      startIcon={
                        <SvgColor src="/assets/icons/ic_docs_single.svg" />
                      }
                      component="a"
                      href="https://docs.futureagi.com/docs/simulation"
                      target="_blank"
                    >
                      View Docs
                    </Button>
                  </ShowComponent>
                  <Button
                    variant="contained"
                    startIcon={<Iconify icon="eva:plus-fill" />}
                    onClick={handleAddRunTest}
                    sx={{
                      bgcolor: "primary.main",
                      color: "primary.contrastText",
                      "&:hover": { bgcolor: "primary.dark" },
                    }}
                    disabled={
                      !RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role]
                    }
                  >
                    {createButtonText}
                  </Button>
                </Box>
              </ShowComponent>
            </Box>

            {/* Table */}
            <DataTable
              columns={columns}
              data={items}
              isLoading={isPending}
              rowCount={total}
              onRowClick={handleRowClick}
              getRowId={(row) => row.id}
              rowHeight={44}
              emptyMessage="No tests found"
            />

            <DataTablePagination
              page={page}
              pageSize={pageSize}
              total={total}
              onPageChange={setPage}
              onPageSizeChange={(size) => {
                setPageSize(size);
                setPage(0);
              }}
            />
          </>
        )}
      </Box>

      <CustomDialog
        title={"Delete selected test?"}
        open={Boolean(testToDelete)}
        actionButton={"Delete"}
        onClickAction={() => deleteTest(testToDelete)}
        onClose={() => setTestToDelete(null)}
        loading={deleteTestPending}
        color="error"
      >
        <Box>
          <Box
            sx={{
              mt: 2,
              p: 2,
              bgcolor: "background.default",
              borderRadius: 1,
            }}
          >
            <Typography variant="subtitle2" color="text.secondary">
              Test Name:
            </Typography>
            <Typography variant="body2" fontWeight="medium">
              {testToDelete?.name}
            </Typography>
          </Box>
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ mt: 2, mx: 1 }}
          >
            This action cannot be undone*
          </Typography>
        </Box>
      </CustomDialog>
    </>
  );
};

RunTestsContent.propTypes = {
  simulationType: PropTypes.oneOf(Object.values(SIMULATION_TYPE)),
  promptTemplateId: PropTypes.string,
  showHeader: PropTypes.bool,
  showSearch: PropTypes.bool,
  onRowClick: PropTypes.func,
  onCreateClick: PropTypes.func,
  createButtonText: PropTypes.string,
  emptyTitle: PropTypes.string,
  emptyDescription: PropTypes.string,
  emptyIcon: PropTypes.string,
  gridRef: PropTypes.object,
};

export default RunTestsContent;
