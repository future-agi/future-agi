import React, {
  useState,
  useCallback,
  useMemo,
  useRef,
  useEffect,
} from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useAuthContext } from "src/auth/hooks";
import FormSearchSelectFieldState from "src/components/FromSearchSelectField/FormSearchSelectFieldState";
import { LoadingButton } from "@mui/lab";
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
  annotateKeys,
  annotationQueueKeys,
  queueItemKeys,
  useAnnotationQueueDetail,
  useQueueItems,
  useQueueProgress,
  useRemoveQueueItem,
  useBulkRemoveQueueItems,
  useBulkReviewItems,
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
import { QUEUE_ROLES, hasQueueRole, isQueueAnnotatorRole } from "../constants";
import {
  canOpenSubmissionWorkspace,
  resolveQueueItemWorkspaceMode,
} from "../annotate/annotation-view-mode";
import {
  ALL_SOURCE_VALUES,
  ALL_STATUS_VALUES,
  ITEM_SOURCE_FILTER_OPTIONS,
  ITEM_STATUS_FILTER_OPTIONS,
  buildQueueItemQueryFilters,
} from "./queue-item-filters";

const REVIEW_STATUS_OPTIONS = [
  { value: "", label: "All Reviews" },
  { value: "pending_review", label: "Pending Review" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
];

export default function QueueDetailView() {
  const { queueId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const theme = useTheme();
  const { user } = useAuthContext();
  const [filters, setFilters] = useState({
    status: ALL_STATUS_VALUES,
    source_type: ALL_SOURCE_VALUES,
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
  const [itemOrdering, setItemOrdering] = useState("-created_at");
  const gridRef = useRef(null);

  const { data: queue } = useAnnotationQueueDetail(queueId);
  const itemQueryFilters = useMemo(
    () => buildQueueItemQueryFilters(filters),
    [filters],
  );
  const {
    data: itemsData,
    isLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useQueueItems(queueId, {
    ...itemQueryFilters,
    ordering: itemOrdering,
    limit: 25,
  });
  const { mutate: removeItem } = useRemoveQueueItem();
  const { mutate: bulkRemove, isPending: isBulkRemoving } =
    useBulkRemoveQueueItems();
  const { mutate: bulkReview, isPending: isBulkReviewing } =
    useBulkReviewItems();
  const { mutate: assignItems, isPending: isAssigningItems } =
    useAssignQueueItems();
  const { mutate: downloadExport, isPending: isDownloadingExport } =
    useDownloadAnnotationQueueExport();
  const { mutate: updateStatus, isPending: isUpdatingStatus } =
    useUpdateAnnotationQueueStatus();
  const { data: progress } = useQueueProgress(queueId);

  const items = useMemo(() => itemsData?.results || [], [itemsData?.results]);
  const totalCount = itemsData?.count || 0;

  const currentUserId = String(
    user?.id ||
      (typeof window !== "undefined"
        ? window.sessionStorage.getItem("currentUserId")
        : "") ||
      "",
  );
  const myQueueMembership = useMemo(() => {
    if (!queue || !user) return null;
    if (Array.isArray(queue.viewer_roles) && queue.viewer_roles.length > 0) {
      return { role: queue.viewer_role, roles: queue.viewer_roles };
    }
    const annotators = queue.annotators || [];
    return annotators.find((a) => String(a.user_id) === currentUserId) || null;
  }, [queue, user, currentUserId]);

  const isManager = hasQueueRole(myQueueMembership, QUEUE_ROLES.MANAGER);
  const canAnnotateQueue =
    hasQueueRole(myQueueMembership, QUEUE_ROLES.ANNOTATOR) || isManager;
  const canViewSubmissions =
    hasQueueRole(myQueueMembership, QUEUE_ROLES.REVIEWER) || isManager;

  const queueAnnotators = useMemo(
    () =>
      (queue?.annotators || []).filter(
        (annotator) => isQueueAnnotatorRole(annotator) && annotator.user_id,
      ),
    [queue?.annotators],
  );

  const queueItemCount = progress?.total ?? totalCount;
  const canStartWork =
    queueItemCount > 0 &&
    (queue?.status === "active" ||
      (queue?.status === "completed" && (progress?.skipped || 0) > 0));
  const canOpenSubmissions = canOpenSubmissionWorkspace({
    itemCount: queueItemCount,
    canViewSubmissions,
    queueStatus: queue?.status,
  });

  const selectedReviewableItems = useMemo(
    () =>
      items.filter(
        (item) =>
          selectedIds.has(item.id) && item.review_status === "pending_review",
      ),
    [items, selectedIds],
  );

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

  const handleBack = useCallback(() => {
    if (currentTab === "settings") {
      setActiveTab(0);
      return;
    }
    navigate(paths.dashboard.annotations.queues);
  }, [currentTab, navigate]);

  const handleFilterChange = useCallback((field, value) => {
    setFilters((prev) => ({ ...prev, [field]: value, page: 0 }));
  }, []);

  const handleMultiFilterChange = useCallback((field, value) => {
    setFilters((prev) => ({
      ...prev,
      [field]: Array.isArray(value) ? value : [],
      page: 0,
    }));
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

  const clearSelectedItems = useCallback(() => {
    gridRef.current?.api?.deselectAll();
    setSelectedIds(new Set());
  }, []);

  const handleBulkRemove = useCallback(() => {
    if (selectedIds.size === 0 || isBulkRemoving) return;
    bulkRemove(
      { queueId, itemIds: Array.from(selectedIds) },
      { onSuccess: clearSelectedItems },
    );
  }, [queueId, selectedIds, bulkRemove, clearSelectedItems, isBulkRemoving]);

  const handleBulkApprove = useCallback(() => {
    if (selectedReviewableItems.length === 0 || isBulkReviewing) return;
    bulkReview(
      {
        queueId,
        itemIds: selectedReviewableItems.map((item) => item.id),
        action: "approve",
      },
      { onSuccess: clearSelectedItems },
    );
  }, [
    bulkReview,
    clearSelectedItems,
    isBulkReviewing,
    queueId,
    selectedReviewableItems,
  ]);

  const handleAssign = useCallback(
    ({ itemIds, userIds, action }) => {
      assignItems({
        queueId,
        itemIds,
        userIds,
        action,
        assignees: queueAnnotators,
      });
    },
    [queueId, assignItems, queueAnnotators],
  );

  const handleOpenBulkAssign = useCallback(() => {
    setBulkAssignUserIds(new Set());
    setBulkAssignOpen(true);
  }, []);

  // Keep bulkAssignUserIds in sync if queueAnnotators change while dialog is open.
  // - Remove any selected ids that are no longer present in queueAnnotators
  // - Preserve other selections when annotators list shrinks/changes
  useEffect(() => {
    if (!bulkAssignOpen) return;
    const annotatorIds = new Set(queueAnnotators.map((a) => String(a.user_id)));
    setBulkAssignUserIds((prev) => {
      const next = new Set();
      for (const id of prev) if (annotatorIds.has(id)) next.add(id);
      return next;
    });
  }, [bulkAssignOpen, queueAnnotators]);

  const handleAddedSortChange = useCallback((direction) => {
    setItemOrdering(direction === "asc" ? "created_at" : "-created_at");
  }, []);

  const handleDownloadExport = useCallback(() => {
    setExportMenuAnchor(null);
    downloadExport({ queueId });
  }, [downloadExport, queueId]);

  const handleOpenAnnotationWorkspace = useCallback(
    (mode) => {
      // The annotation workspace chooses its first item from `/next-item/`.
      // Clear cached entry state so Start/Review sees newly added items,
      // current roles, progress, and queue metadata before selecting.
      queryClient.removeQueries({ queryKey: annotateKeys.nextItem(queueId) });
      queryClient.removeQueries({
        queryKey: annotationQueueKeys.detail(queueId),
      });
      queryClient.removeQueries({
        queryKey: annotationQueueKeys.progress(queueId),
      });
      queryClient.invalidateQueries({ queryKey: queueItemKeys.all(queueId) });
      navigate(`${paths.dashboard.annotations.annotate(queueId)}?mode=${mode}`);
    },
    [navigate, queryClient, queueId],
  );

  const handleOpenDatasetExport = useCallback(() => {
    setExportMenuAnchor(null);
    setExportDialogOpen(true);
  }, []);

  const handleApplyBulkAssign = useCallback(() => {
    if (
      selectedIds.size === 0 ||
      queueAnnotators.length === 0 ||
      isAssigningItems
    ) {
      return;
    }

    assignItems(
      {
        queueId,
        itemIds: Array.from(selectedIds),
        userIds: Array.from(bulkAssignUserIds),
        action: "set",
        assignees: queueAnnotators,
      },
      {
        onSuccess: () => {
          setBulkAssignOpen(false);
          setBulkAssignUserIds(new Set());
          clearSelectedItems();
        },
      },
    );
  }, [
    assignItems,
    queueId,
    selectedIds,
    bulkAssignUserIds,
    queueAnnotators,
    isAssigningItems,
    clearSelectedItems,
  ]);

  const isEmpty =
    !isLoading &&
    items.length === 0 &&
    filters.status.length === ALL_STATUS_VALUES.length &&
    filters.source_type.length === ALL_SOURCE_VALUES.length &&
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
        useFlexGap
        flexWrap="wrap"
        sx={{ px: 3, pt: 3, pb: 2, minWidth: 0 }}
        flexShrink={0}
      >
        <IconButton
          aria-label={
            currentTab === "settings" ? "Back to queue items" : "Back to queues"
          }
          onClick={handleBack}
          size="small"
        >
          <Iconify icon="eva:arrow-back-fill" />
        </IconButton>
        <Typography variant="h4" sx={{ minWidth: 0 }} noWrap>
          {queue?.name || "Queue"}
        </Typography>
        {queue?.status && <StatusBadge status={queue.status} />}
        <Box sx={{ flex: "1 1 auto", minWidth: 0 }} />
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
        {canOpenSubmissions && (
          <Button
            variant={canAnnotateQueue ? "outlined" : "contained"}
            color="primary"
            startIcon={<Iconify icon="solar:checklist-bold" />}
            onClick={() => handleOpenAnnotationWorkspace("review")}
          >
            {queue?.requires_review ? "Review Items" : "View Submissions"}
          </Button>
        )}
        {canStartWork && canAnnotateQueue && (
          <Button
            variant="contained"
            color="primary"
            startIcon={<Iconify icon="eva:edit-2-fill" />}
            onClick={() => handleOpenAnnotationWorkspace("annotate")}
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
              {progress.in_review > 0 &&
                ` \u00b7 ${progress.in_review} in review`}
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
              alignItems="flex-start"
              justifyContent="space-between"
              useFlexGap
              flexWrap="wrap"
              mb={2}
              flexShrink={0}
              sx={{ gap: 1.5, minWidth: 0 }}
            >
              <Stack
                direction="row"
                spacing={2}
                useFlexGap
                flexWrap="wrap"
                sx={{ minWidth: 0, flex: "1 1 560px" }}
              >
                <FormSearchSelectFieldState
                  size="small"
                  value={filters.status}
                  onChange={(e) =>
                    handleMultiFilterChange("status", e.target.value)
                  }
                  options={ITEM_STATUS_FILTER_OPTIONS.map((o) => ({
                    label: o.label,
                    value: o.value,
                  }))}
                  placeholder="All Item Statuses"
                  multiple
                  checkbox
                  selectAll
                  multipleAllLabel="All Item Statuses"
                  showClear={false}
                  sx={{ minWidth: 160, flex: "1 1 170px" }}
                />
                <FormSearchSelectFieldState
                  size="small"
                  value={filters.source_type}
                  onChange={(e) =>
                    handleMultiFilterChange("source_type", e.target.value)
                  }
                  options={ITEM_SOURCE_FILTER_OPTIONS.map((o) => ({
                    label: o.label,
                    value: o.value,
                  }))}
                  placeholder="All Sources"
                  multiple
                  checkbox
                  selectAll
                  multipleAllLabel="All Sources"
                  showClear={false}
                  sx={{ minWidth: 160, flex: "1 1 170px" }}
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
                    sx={{ minWidth: 140, flex: "1 1 150px" }}
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
                  sx={{
                    textTransform: "none",
                    px: 2,
                    flexShrink: 0,
                    whiteSpace: "nowrap",
                  }}
                >
                  My Items
                </ToggleButton>
              </Stack>

              <Stack
                direction="row"
                spacing={1}
                useFlexGap
                flexWrap="wrap"
                justifyContent="flex-end"
                sx={{
                  minWidth: 0,
                  flex: "0 1 auto",
                  "& .MuiButton-root": {
                    minWidth: 124,
                    flexShrink: 0,
                    whiteSpace: "nowrap",
                  },
                  "& .MuiButton-startIcon": {
                    flexShrink: 0,
                  },
                }}
              >
                {isManager && selectedIds.size > 0 && (
                  <>
                    <LoadingButton
                      variant="outlined"
                      size="medium"
                      onClick={handleOpenBulkAssign}
                      loading={isAssigningItems}
                      disabled={isAssigningItems || isBulkRemoving}
                    >
                      Assign Selected ({selectedIds.size})
                    </LoadingButton>
                    <LoadingButton
                      color="error"
                      variant="outlined"
                      size="medium"
                      onClick={handleBulkRemove}
                      loading={isBulkRemoving}
                      disabled={isAssigningItems}
                    >
                      Remove Selected ({selectedIds.size})
                    </LoadingButton>
                  </>
                )}
                {canViewSubmissions && selectedReviewableItems.length > 0 && (
                  <LoadingButton
                    variant="outlined"
                    size="medium"
                    color="success"
                    onClick={handleBulkApprove}
                    loading={isBulkReviewing}
                    disabled={isBulkReviewing}
                  >
                    Approve Selected ({selectedReviewableItems.length})
                  </LoadingButton>
                )}
                {isManager && (
                  <Button
                    variant="contained"
                    color="primary"
                    startIcon={<Iconify icon="mingcute:add-line" />}
                    onClick={() => setAddDialogOpen(true)}
                  >
                    Add Items
                  </Button>
                )}
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
              <QueueItemsEmpty
                onAddClick={
                  isManager ? () => setAddDialogOpen(true) : undefined
                }
              />
            </Box>
          ) : (
            <QueueItemsTable
              gridRef={gridRef}
              data={items}
              loading={isLoading}
              totalCount={totalCount}
              hasNextPage={hasNextPage}
              isFetchingNextPage={isFetchingNextPage}
              onLoadMore={fetchNextPage}
              selectedIds={selectedIds}
              onSelectToggle={handleSelectToggle}
              onSelectAll={handleSelectAll}
              onRemove={isManager ? handleRemove : undefined}
              onItemClick={(item) => {
                const assignedUsers = item?.assigned_users || [];
                const assignedToOther =
                  queue?.auto_assign === false &&
          
                  !assignedUsers?.some(
                    (a) => String(a.id) === currentUserId,
                  );
                if (assignedToOther && !canViewSubmissions && !isManager) {
                  enqueueSnackbar(
                    "You cannot annotate items that are not assigned to you",
                    { variant: "warning" },
                  );
                  return;
                }
                if (
                  queue?.status === "active" ||
                  queue?.status === "completed"
                ) {
                  const mode = resolveQueueItemWorkspaceMode({
                    item,
                    canViewSubmissions,
                    canAnnotate: canAnnotateQueue,
                  });
                  navigate(
                    `${paths.dashboard.annotations.annotate(queueId)}?itemId=${item.id}&mode=${mode}`,
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
              canManageItems={isManager}
              canSelectItems={isManager || canViewSubmissions}
              addedSortDirection={
                itemOrdering === "created_at" ? "asc" : "desc"
              }
              onAddedSortChange={handleAddedSortChange}
            />
          )}
        </Box>
      )}

      <AddItemsDialog
        open={addDialogOpen}
        onClose={() => setAddDialogOpen(false)}
        queueId={queueId}
        queue={queue}
      />

      <ExportToDatasetDialog
        open={exportDialogOpen}
        onClose={() => setExportDialogOpen(false)}
        queueId={queueId}
      />

      <Dialog
        open={bulkAssignOpen}
        onClose={() => {
          if (!isAssigningItems) setBulkAssignOpen(false);
        }}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Assign Selected Items</DialogTitle>
        <DialogContent>
          {queueAnnotators.length > 1 && (
            <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
              <Button
                size="small"
                onClick={() => {
                  const allUserIds = new Set(
                    queueAnnotators.map((a) => String(a.user_id)),
                  );
                  const allSelected =
                    bulkAssignUserIds.size === queueAnnotators.length;
                  setBulkAssignUserIds(allSelected ? new Set() : allUserIds);
                }}
                disabled={isAssigningItems}
              >
                {bulkAssignUserIds.size === queueAnnotators.length
                  ? "Deselect All"
                  : "Select All"}
              </Button>
              <Button
                size="small"
                onClick={() => setBulkAssignUserIds(new Set())}
                disabled={isAssigningItems}
              >
                Select None
              </Button>
            </Stack>
          )}

          <Stack spacing={0.5} sx={{ mt: 1 }}>
            {queueAnnotators.map((annotator) => {
              const uid = String(annotator.user_id);
              return (
                <FormControlLabel
                  key={uid}
                  control={
                    <Checkbox
                      checked={bulkAssignUserIds.has(uid)}
                      disabled={isAssigningItems}
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
          <Button
            onClick={() => setBulkAssignOpen(false)}
            disabled={isAssigningItems}
          >
            Cancel
          </Button>
          <LoadingButton
            variant="contained"
            onClick={handleApplyBulkAssign}
            loading={isAssigningItems}
            disabled={queueAnnotators.length === 0 || selectedIds.size === 0}
          >
            Assign
          </LoadingButton>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
