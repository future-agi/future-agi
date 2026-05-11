import React, { useState, useCallback, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useAuthContext } from "src/auth/hooks";
import FormSearchSelectFieldState from "src/components/FromSearchSelectField/FormSearchSelectFieldState";
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  LinearProgress,
  Menu,
  MenuItem,
  Stack,
  Tab,
  Tabs,
  ToggleButton,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  getAnnotationTabSx,
  getAnnotationTabIndicatorProps,
} from "../../view/annotation-tab-styles";
import {
  useAnnotationQueueDetail,
  useQueueItems,
  useQueueProgress,
  useRemoveQueueItem,
  useBulkRemoveQueueItems,
  useAssignQueueItems,
  useDownloadAnnotationQueueExport,
  useUpdateAnnotationQueueStatus,
} from "src/api/annotation-queues/annotation-queues";
import StatusBadge from "../components/status-badge";
import QueueItemsTable from "../items/queue-items-table";
import QueueItemsEmpty from "../items/queue-items-empty";
import AddItemsDialog from "../items/add-items-dialog";
import QueueSettingsTab from "./queue-settings-tab";
import QueueAnalyticsTab from "./queue-analytics-tab";
import QueueAgreementTab from "./queue-agreement-tab";
import ExportToDatasetDialog from "./export-to-dataset-dialog";
import AutomationRulesTab from "./automation-rules-tab";
import { paths } from "src/routes/paths";
import { enqueueSnackbar } from "src/components/snackbar";
import { isQueueAnnotatorRole } from "../constants";

const STATUS_OPTIONS = [
  { value: "", label: "All Statuses" },
  { value: "pending", label: "Pending" },
  { value: "in_progress", label: "In Progress" },
  { value: "completed", label: "Completed" },
  { value: "skipped", label: "Skipped" },
];

const SOURCE_OPTIONS = [
  { value: "", label: "All Sources" },
  { value: "dataset_row", label: "Dataset Row" },
  { value: "trace", label: "Trace" },
  { value: "observation_span", label: "Span" },
  { value: "trace_session", label: "Session" },
  { value: "prototype_run", label: "Prototype" },
  { value: "call_execution", label: "Simulation" },
];

const REVIEW_STATUS_OPTIONS = [
  { value: "", label: "All Reviews" },
  { value: "pending_review", label: "Pending Review" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
];

export default function QueueDetailView() {
  const { queueId } = useParams();
  const navigate = useNavigate();
  const theme = useTheme();
  const { user } = useAuthContext();
  const [filters, setFilters] = useState({
    status: "",
    source_type: "",
    assigned_to: "",
    review_status: "",
  });
  const [activeTab, setActiveTab] = useState(0);
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [exportDialogOpen, setExportDialogOpen] = useState(false);
  const [exportMenuAnchor, setExportMenuAnchor] = useState(null);
  const [bulkAssignOpen, setBulkAssignOpen] = useState(false);
  const [bulkAssignUserIds, setBulkAssignUserIds] = useState(new Set());
  const [selectedIds, setSelectedIds] = useState(new Set());

  const { data: queue } = useAnnotationQueueDetail(queueId);
  const {
    data: itemsData,
    isLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useQueueItems(queueId, { ...filters, limit: 25 });
  const { mutate: removeItem } = useRemoveQueueItem();
  const { mutate: bulkRemove } = useBulkRemoveQueueItems();
  const { mutate: assignItems } = useAssignQueueItems();
  const { mutate: downloadExport, isPending: isDownloadingExport } =
    useDownloadAnnotationQueueExport();
  const { mutate: updateStatus, isPending: isUpdatingStatus } =
    useUpdateAnnotationQueueStatus();
  const { data: progress } = useQueueProgress(queueId);

  const items = useMemo(() => itemsData?.results || [], [itemsData?.results]);
  const totalCount = itemsData?.count || 0;

  const isManager = useMemo(() => {
    if (!queue || !user) return false;
    const annotators = queue.annotators || [];
    const me = annotators.find((a) => a.user_id === (user.id || user.pk));
    return me?.role === "manager";
  }, [queue, user]);

  const queueAnnotators = useMemo(
    () =>
      (queue?.annotators || []).filter(
        (annotator) => isQueueAnnotatorRole(annotator) && annotator.user_id,
      ),
    [queue?.annotators],
  );

  const canStartAnnotating =
    totalCount > 0 &&
    (queue?.status === "active" ||
      (queue?.status === "completed" && (progress?.skipped || 0) > 0));

  // Ordered tab labels based on role — items is always 0
  const tabLabels = useMemo(
    () =>
      [
        "items",
        isManager && "settings",
        "analytics",
        "agreement",
        isManager && "rules",
      ].filter(Boolean),
    [isManager],
  );

  const currentTab = tabLabels[activeTab] || "items";

  const handleFilterChange = useCallback((field, value) => {
    setFilters((prev) => ({ ...prev, [field]: value, page: 0 }));
  }, []);

  const handleSelectToggle = useCallback((id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleSelectAll = useCallback(() => {
    setSelectedIds((prev) => {
      if (prev.size === items.length) return new Set();
      return new Set(items.map((i) => i.id));
    });
  }, [items]);

  const handleRemove = useCallback(
    (item) => {
      removeItem({ queueId, itemId: item.id });
    },
    [queueId, removeItem],
  );

  const handleBulkRemove = useCallback(() => {
    bulkRemove(
      { queueId, itemIds: Array.from(selectedIds) },
      { onSuccess: () => setSelectedIds(new Set()) },
    );
  }, [queueId, selectedIds, bulkRemove]);

  const handleAssign = useCallback(
    ({ itemIds, userId, userIds, action }) => {
      assignItems({ queueId, itemIds, userId, userIds, action });
    },
    [queueId, assignItems],
  );

  const handleOpenBulkAssign = useCallback(() => {
    setBulkAssignUserIds(new Set());
    setBulkAssignOpen(true);
  }, []);

  const handleDownloadExport = useCallback(() => {
    setExportMenuAnchor(null);
    downloadExport({ queueId });
  }, [downloadExport, queueId]);

  const handleOpenDatasetExport = useCallback(() => {
    setExportMenuAnchor(null);
    setExportDialogOpen(true);
  }, []);

  const handleApplyBulkAssign = useCallback(() => {
    assignItems(
      {
        queueId,
        itemIds: Array.from(selectedIds),
        userIds: Array.from(bulkAssignUserIds),
        action: "set",
      },
      {
        onSuccess: () => {
          setBulkAssignOpen(false);
          setSelectedIds(new Set());
          setBulkAssignUserIds(new Set());
        },
      },
    );
  }, [assignItems, queueId, selectedIds, bulkAssignUserIds]);

  const isEmpty =
    !isLoading &&
    items.length === 0 &&
    !filters.status &&
    !filters.source_type &&
    !filters.assigned_to &&
    !filters.review_status;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        backgroundColor: "background.paper",
      }}
    >
      {/* Header */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 3, pt: 3, pb: 2 }}
        flexShrink={0}
      >
        <IconButton
          onClick={() => navigate(paths.dashboard.annotations.queues)}
          size="small"
        >
          <Iconify icon="eva:arrow-back-fill" />
        </IconButton>
        <Typography variant="h4">{queue?.name || "Queue"}</Typography>
        {queue?.status && <StatusBadge status={queue.status} />}
        <Box sx={{ flex: 1 }} />
        {isManager && queue?.status && queue.status !== "active" && (
          <Button
            variant="outlined"
            disabled={isUpdatingStatus}
            startIcon={<Iconify icon="eva:play-circle-fill" />}
            onClick={() => updateStatus({ id: queueId, status: "active" })}
          >
            Activate
          </Button>
        )}
        <Button
          variant="outlined"
          disabled={totalCount === 0 || isDownloadingExport}
          startIcon={<Iconify icon="eva:download-fill" />}
          endIcon={<Iconify icon="eva:arrow-ios-downward-fill" />}
          onClick={(event) => setExportMenuAnchor(event.currentTarget)}
        >
          Export
        </Button>
        <Menu
          anchorEl={exportMenuAnchor}
          open={Boolean(exportMenuAnchor)}
          onClose={() => setExportMenuAnchor(null)}
        >
          <MenuItem onClick={handleDownloadExport}>
            <Iconify icon="eva:download-outline" sx={{ mr: 1 }} />
            Download
          </MenuItem>
          <MenuItem onClick={handleOpenDatasetExport}>
            <Iconify icon="eva:archive-outline" sx={{ mr: 1 }} />
            Export to Dataset
          </MenuItem>
        </Menu>
        {canStartAnnotating && (
          <Button
            variant="contained"
            color="primary"
            startIcon={<Iconify icon="eva:edit-2-fill" />}
            onClick={() =>
              navigate(paths.dashboard.annotations.annotate(queueId))
            }
          >
            {queue?.status === "completed"
              ? "Resume Skipped"
              : "Start Annotating"}
          </Button>
        )}
      </Stack>

      {/* Progress */}
      {progress && progress.total > 0 && (
        <Box sx={{ mb: 2, flexShrink: 0, px: 3 }}>
          {/* User's own progress (if they have assigned items) */}
          {progress.user_progress && progress.user_progress.total > 0 && (
            <Box sx={{ mb: 1.5 }}>
              <Stack
                direction="row"
                justifyContent="space-between"
                sx={{ mb: 0.5 }}
              >
                <Typography variant="body2" color="text.secondary">
                  Your Progress: {progress.user_progress.completed}/
                  {progress.user_progress.total} completed
                </Typography>
                <Typography variant="body2" fontWeight={600}>
                  {progress.user_progress.progress_pct ?? 0}%
                </Typography>
              </Stack>
              <LinearProgress
                variant="determinate"
                value={progress.user_progress.progress_pct ?? 0}
                sx={{ height: 4, borderRadius: 2 }}
              />
            </Box>
          )}
          {/* Overall progress */}
          <Stack
            direction="row"
            justifyContent="space-between"
            sx={{ mb: 0.5 }}
          >
            <Typography variant="body2" color="text.secondary">
              Overall: {progress.completed}/{progress.total} completed
              {progress.pending > 0 && ` \u00b7 ${progress.pending} pending`}
              {progress.in_progress > 0 &&
                ` \u00b7 ${progress.in_progress} in progress`}
              {progress.skipped > 0 && ` \u00b7 ${progress.skipped} skipped`}
            </Typography>
            <Typography variant="body2" fontWeight={600}>
              {progress.progress_pct ?? 0}%
            </Typography>
          </Stack>
          <LinearProgress
            variant="determinate"
            value={progress.progress_pct ?? 0}
            sx={{
              height: 3,
              borderRadius: 2,
              backgroundColor: "action.disabled",
              "& .MuiLinearProgress-bar": {
                backgroundColor: "success.main",
              },
            }}
          />
        </Box>
      )}

      {/* Tabs */}
      <Tabs
        value={activeTab}
        onChange={(_, v) => setActiveTab(v)}
        TabIndicatorProps={getAnnotationTabIndicatorProps(theme)}
        sx={{
          ...getAnnotationTabSx(theme),
          px: 3,
        }}
      >
        <Tab label="Items" />
        {isManager && <Tab label="Settings" />}
        <Tab label="Analytics" />
        <Tab label="Agreement" />
        {isManager && <Tab label="Rules" />}
      </Tabs>

      {currentTab === "settings" && (
        <Box sx={{ px: 3, overflow: "auto", flex: 1 }}>
          <QueueSettingsTab
            queue={queue}
            queueId={queueId}
            creatorId={queue?.created_by}
          />
        </Box>
      )}
      {currentTab === "analytics" && (
        <Box sx={{ px: 3, overflow: "auto", flex: 1 }}>
          <QueueAnalyticsTab queueId={queueId} />
        </Box>
      )}
      {currentTab === "agreement" && (
        <Box sx={{ px: 3, overflow: "auto", flex: 1 }}>
          <QueueAgreementTab queueId={queueId} />
        </Box>
      )}
      {currentTab === "rules" && (
        <Box sx={{ px: 3, overflow: "auto", flex: 1 }}>
          <AutomationRulesTab queueId={queueId} queue={queue} />
        </Box>
      )}

      {/* Items tab */}
      {currentTab === "items" && (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            flex: 1,
            overflow: "hidden",
            px: 3,
          }}
        >
          {/* Toolbar */}
          {!isEmpty && (
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              mb={2}
              flexShrink={0}
            >
              <Stack direction="row" spacing={2}>
                <FormSearchSelectFieldState
                  size="small"
                  value={filters.status}
                  onChange={(e) => handleFilterChange("status", e.target.value)}
                  options={STATUS_OPTIONS.map((o) => ({
                    label: o.label,
                    value: o.value,
                  }))}
                  placeholder="All Statuses"
                  showClear={!!filters.status}
                  sx={{ minWidth: 160 }}
                />
                <FormSearchSelectFieldState
                  size="small"
                  value={filters.source_type}
                  onChange={(e) =>
                    handleFilterChange("source_type", e.target.value)
                  }
                  options={SOURCE_OPTIONS.map((o) => ({
                    label: o.label,
                    value: o.value,
                  }))}
                  placeholder="All Sources"
                  showClear={!!filters.source_type}
                  sx={{ minWidth: 160 }}
                />
                {queue?.requires_review && (
                  <FormSearchSelectFieldState
                    size="small"
                    value={filters.review_status}
                    onChange={(e) =>
                      handleFilterChange("review_status", e.target.value)
                    }
                    options={REVIEW_STATUS_OPTIONS.map((o) => ({
                      label: o.label,
                      value: o.value,
                    }))}
                    placeholder="All Reviews"
                    showClear={!!filters.review_status}
                    sx={{ minWidth: 160 }}
                  />
                )}
                <ToggleButton
                  value="mine"
                  selected={filters.assigned_to === "me"}
                  onChange={() =>
                    handleFilterChange(
                      "assigned_to",
                      filters.assigned_to === "me" ? "" : "me",
                    )
                  }
                  size="small"
                  sx={{ textTransform: "none", px: 2 }}
                >
                  My Items
                </ToggleButton>
              </Stack>

              <Stack direction="row" spacing={1}>
                {selectedIds.size > 0 && (
                  <>
                    {isManager && !queue?.auto_assign && (
                      <Button
                        variant="outlined"
                        size="medium"
                        onClick={handleOpenBulkAssign}
                      >
                        Assign Selected ({selectedIds.size})
                      </Button>
                    )}
                    <Button
                      color="error"
                      variant="outlined"
                      size="medium"
                      onClick={handleBulkRemove}
                    >
                      Remove Selected ({selectedIds.size})
                    </Button>
                  </>
                )}
                <Button
                  variant="contained"
                  color="primary"
                  startIcon={<Iconify icon="mingcute:add-line" />}
                  onClick={() => setAddDialogOpen(true)}
                >
                  Add Items
                </Button>
              </Stack>
            </Stack>
          )}

          {/* Content */}
          {isEmpty ? (
            <Box
              sx={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                flex: 1,
              }}
            >
              <QueueItemsEmpty onAddClick={() => setAddDialogOpen(true)} />
            </Box>
          ) : (
            <QueueItemsTable
              data={items}
              loading={isLoading}
              totalCount={totalCount}
              hasNextPage={hasNextPage}
              isFetchingNextPage={isFetchingNextPage}
              onLoadMore={fetchNextPage}
              selectedIds={selectedIds}
              onSelectToggle={handleSelectToggle}
              onSelectAll={handleSelectAll}
              onRemove={handleRemove}
              onItemClick={(item) => {
                if (queue?.status === "active") {
                  navigate(
                    `${paths.dashboard.annotations.annotate(queueId)}?itemId=${item.id}`,
                  );
                } else {
                  enqueueSnackbar(
                    "You can only annotate when the queue is in active state. Manage status in settings tab",
                    { variant: "info" },
                  );
                }
              }}
              annotators={queueAnnotators}
              onAssign={isManager ? handleAssign : undefined}
              autoAssign={queue?.auto_assign ?? false}
            />
          )}
        </Box>
      )}

      <AddItemsDialog
        open={addDialogOpen}
        onClose={() => setAddDialogOpen(false)}
        queueId={queueId}
      />

      <ExportToDatasetDialog
        open={exportDialogOpen}
        onClose={() => setExportDialogOpen(false)}
        queueId={queueId}
      />

      <Dialog
        open={bulkAssignOpen}
        onClose={() => setBulkAssignOpen(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Assign Selected Items</DialogTitle>
        <DialogContent>
          <Stack spacing={0.5} sx={{ mt: 1 }}>
            {queueAnnotators.map((annotator) => {
              const uid = String(annotator.user_id);
              return (
                <FormControlLabel
                  key={uid}
                  control={
                    <Checkbox
                      checked={bulkAssignUserIds.has(uid)}
                      onChange={(event) => {
                        setBulkAssignUserIds((prev) => {
                          const next = new Set(prev);
                          if (event.target.checked) next.add(uid);
                          else next.delete(uid);
                          return next;
                        });
                      }}
                    />
                  }
                  label={annotator.name || annotator.email}
                />
              );
            })}
            {queueAnnotators.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                No annotators are configured for this queue.
              </Typography>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setBulkAssignOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleApplyBulkAssign}
            disabled={queueAnnotators.length === 0}
          >
            Apply
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
