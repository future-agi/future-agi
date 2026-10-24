import SelectionBar from "src/components/selection-bar";
import PropTypes from "prop-types";
import { Avatar, AvatarGroup, Box, Button, Typography } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import React, { useCallback, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { format } from "date-fns";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import { useAgentPlaygroundStoreShallow } from "../store";
import useCanEditAgent from "../hooks/useCanEditAgent";
import DeleteAgentsDialog from "../components/DeleteAgentsDialog";
import {
  useCreateGraph,
  useDeleteGraphs,
} from "../../../api/agent-playground/agent-playground";
import ListToolbar from "src/components/list-toolbar";
import PageHeader from "src/components/page-header";

const PAGE_SIZE_DEFAULT = 25;

const mapGraph = (graph) => {
  const createdByRaw = graph.created_by ?? graph.createdBy;
  return {
    id: graph.id,
    name: graph.name,
    description: graph.description,
    noOfNodes: graph.node_count ?? graph.nodeCount ?? 0,
    createdBy:
      (typeof createdByRaw === "object" ? createdByRaw?.name : createdByRaw) ??
      "",
    collaborators: graph.collaborators || [],
    activeVersionId: graph.active_version_id ?? graph.activeVersionId ?? null,
    created: graph.created_at,
    updated: graph.updated_at,
  };
};

export default function AgentListView({ embedded = false }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { setCurrentAgent } = useAgentPlaygroundStoreShallow((s) => ({
    setCurrentAgent: s.setCurrentAgent,
  }));

  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(PAGE_SIZE_DEFAULT);
  const [rowSelection, setRowSelection] = useState({});
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const debouncedSearch = useDebounce(searchQuery.trim(), 500);

  const { data, isLoading } = useQuery({
    queryKey: [
      "agent-playground",
      "graphs",
      { page: page + 1, search: debouncedSearch, pageSize },
    ],
    queryFn: () =>
      axios.get(endpoints.agentPlayground.listGraphs, {
        params: {
          page_number: page + 1,
          page_size: pageSize,
          ...(debouncedSearch && { search: debouncedSearch }),
        },
      }),
    select: (d) => d.data?.result,
    keepPreviousData: true,
  });

  const items = useMemo(() => (data?.graphs ?? []).map(mapGraph), [data]);
  const total = data?.metadata?.total_count ?? 0;

  const selectedItems = useMemo(
    () =>
      Object.keys(rowSelection)
        .filter((k) => rowSelection[k])
        .map((k) => items[parseInt(k, 10)])
        .filter(Boolean),
    [rowSelection, items],
  );

  const selectedCount = selectedItems.length;

  const deleteMutation = useDeleteGraphs({
    onSuccess: () => {
      setDeleteDialogOpen(false);
      setRowSelection({});
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graphs"],
      });
    },
  });

  const handleDeleteConfirm = () => {
    const ids = selectedItems.map((item) => item.id);
    deleteMutation.mutate({ ids });
  };

  const { mutate: createAgent, isPending: isCreatingAgent } = useCreateGraph({
    navigate,
    setCurrentAgent,
  });

  const { canCreate: canCreateAgent, canDelete: canDeleteAgent } =
    useCanEditAgent();

  const handleRowClick = useCallback(
    (row) => {
      if (!row?.activeVersionId) {
        navigate(`/dashboard/agents/playground/${row.id}/build`);
      } else {
        navigate(
          `/dashboard/agents/playground/${row.id}/build?version=${row.activeVersionId}`,
        );
      }
    },
    [navigate],
  );

  const columns = useMemo(
    () => [
      {
        id: "name",
        accessorKey: "name",
        header: "Agent Name",
        meta: { flex: 2 },
        minSize: 280,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
            {getValue()}
          </Typography>
        ),
      },
      {
        id: "noOfNodes",
        accessorKey: "noOfNodes",
        header: "No. of nodes",
        size: 120,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography variant="body2" sx={{ fontSize: 13 }}>
            {getValue()}
          </Typography>
        ),
      },
      {
        id: "createdBy",
        accessorKey: "createdBy",
        header: "Created by",
        meta: { flex: 1 },
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
            {getValue() || "-"}
          </Typography>
        ),
      },
      {
        id: "collaborators",
        accessorKey: "collaborators",
        header: "Collaborators",
        size: 150,
        enableSorting: false,
        cell: ({ getValue }) => {
          const collabs = getValue();
          if (!collabs || collabs.length === 0) {
            return (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ fontSize: 13 }}
              >
                -
              </Typography>
            );
          }
          return (
            <AvatarGroup
              sx={{
                justifyContent: "flex-start",
                "& .MuiAvatar-root": {
                  width: 24,
                  height: 24,
                  fontSize: 12,
                },
              }}
            >
              {collabs.map((c, i) => (
                <CustomTooltip size="small" arrow key={i} title={c?.email} show>
                  <Avatar sx={{ width: 24, height: 24, fontSize: 12 }}>
                    {(c?.name?.[0] || "?").toUpperCase()}
                  </Avatar>
                </CustomTooltip>
              ))}
            </AvatarGroup>
          );
        },
      },
      {
        id: "created",
        accessorKey: "created",
        header: "Created at",
        size: 180,
        enableSorting: false,
        cell: ({ getValue }) => {
          const val = getValue();
          if (!val)
            return (
              <Typography variant="body2" color="text.disabled">
                -
              </Typography>
            );
          try {
            return (
              <Typography variant="body2" sx={{ fontSize: 13 }}>
                {format(new Date(val), "dd-MM-yyyy, h:mm a")}
              </Typography>
            );
          } catch {
            return (
              <Typography variant="body2" color="text.disabled">
                -
              </Typography>
            );
          }
        },
      },
      {
        id: "updated",
        accessorKey: "updated",
        header: "Updated at",
        size: 180,
        enableSorting: false,
        cell: ({ getValue }) => {
          const val = getValue();
          if (!val)
            return (
              <Typography variant="body2" color="text.disabled">
                -
              </Typography>
            );
          try {
            return (
              <Typography variant="body2" sx={{ fontSize: 13 }}>
                {format(new Date(val), "dd-MM-yyyy, h:mm a")}
              </Typography>
            );
          } catch {
            return (
              <Typography variant="body2" color="text.disabled">
                -
              </Typography>
            );
          }
        },
      },
    ],
    [],
  );

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        p: embedded ? 0 : 2,
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      {/* Header — the Agents landing supplies its own when embedded. */}
      {!embedded && (
        <PageHeader
          title="Agent playground"
          description="Break down complex tasks into sequential steps that build upon each other"
          sx={{ mb: 0 }}
          actions={
            <Button
              variant="outlined"
              size="small"
              sx={{ minWidth: 105 }}
              component="a"
              href="https://docs.futureagi.com/docs/agent-playground"
              target="_blank"
            >
              <SvgColor
                src="/assets/icons/agent/docs.svg"
                sx={{ height: 16, width: 16, mr: 1 }}
              />
              <Typography typography="s2" fontWeight="fontWeightMedium">
                View Docs
              </Typography>
            </Button>
          }
        />
      )}

      {/* Search + bulk actions / create */}
      <ListToolbar>
        <FormSearchField
          searchQuery={searchQuery}
          onChange={(e) => {
            setSearchQuery(e.target.value);
            setPage(0);
          }}
          size="small"
          placeholder="Search"
          sx={{ width: 280 }}
        />
        <Box sx={{ flex: 1 }} />
        {selectedCount > 0 ? (
          <SelectionBar
            count={selectedCount}
            onCancel={() => setRowSelection({})}
            actions={[
              {
                label: "Delete",
                icon: "solar:trash-bin-trash-linear",
                onClick: () => setDeleteDialogOpen(true),
                disabled: !canDeleteAgent,
                tooltip: canDeleteAgent
                  ? undefined
                  : "You don't have permission to delete agents.",
              },
            ]}
          />
        ) : embedded ? null : (
          <Box sx={{ display: "flex", gap: 2, alignItems: "center" }}>
            <Button
              variant="outlined"
              size="small"
              startIcon={
                <SvgColor src="/assets/icons/ic_docs_single.svg" width={20} />
              }
              component="a"
              href="https://docs.futureagi.com/docs/agent-playground"
              target="_blank"
            >
              View Docs
            </Button>
            <CustomTooltip
              show={!canCreateAgent}
              type=""
              size="small"
              title="You don't have permission to create agents."
              arrow
            >
              <span>
                <LoadingButton
                  variant="contained"
                  color="primary"
                  size="small"
                  loading={isCreatingAgent}
                  onClick={() => createAgent()}
                  disabled={!canCreateAgent}
                  startIcon={
                    <SvgColor src="/assets/icons/ic_add.svg" width={20} />
                  }
                >
                  Create Agent
                </LoadingButton>
              </span>
            </CustomTooltip>
          </Box>
        )}
      </ListToolbar>

      {/* Table */}
      <DataTable
        columns={columns}
        data={items}
        isLoading={isLoading}
        rowCount={total}
        rowSelection={rowSelection}
        onRowSelectionChange={setRowSelection}
        onRowClick={handleRowClick}
        getRowId={(row) => row.id}
        enableSelection
        emptyMessage="No agents found"
      />

      {/* Pagination */}
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

      <DeleteAgentsDialog
        open={deleteDialogOpen}
        onClose={() => {
          if (!deleteMutation.isPending) setDeleteDialogOpen(false);
        }}
        onConfirm={handleDeleteConfirm}
        agentCount={selectedCount}
        isLoading={deleteMutation.isPending}
      />
    </Box>
  );
}

AgentListView.propTypes = { embedded: PropTypes.bool };
