import { useState, useCallback, useRef, useEffect } from "react";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import SvgColor from "src/components/svg-color";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  useAnnotationQueuesList,
  useUpdateAnnotationQueueStatus,
  useDeleteAnnotationQueue,
  useRestoreAnnotationQueue,
} from "src/api/annotation-queues/annotation-queues";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import AnnotationsTabs from "../../view/annotations-tabs";
import AnnotationQueueTable from "../annotation-queue-table";
import AnnotationQueueEmpty from "../annotation-queue-empty";
import CreateQueueDrawer from "../create-queue-drawer";

const STATUS_OPTIONS = [
  { value: "", label: "All Statuses" },
  { value: "draft", label: "Draft" },
  { value: "active", label: "Active" },
  { value: "paused", label: "Paused" },
  { value: "completed", label: "Completed" },
];

export default function AnnotationQueuesView() {
  const { role } = useAuthContext();
  const canWrite = RolePermission.DATASETS[PERMISSIONS.CREATE][role];
  const [filters, setFilters] = useState({
    search: "",
    status: "",
    page: 0,
    limit: 10,
    include_counts: true,
  });

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editQueue, setEditQueue] = useState(null);
  const [archiveQueue, setArchiveQueue] = useState(null);
  const [completeConfirm, setCompleteConfirm] = useState(null);

  const { data, isLoading } = useAnnotationQueuesList({
    ...filters,
    page: filters.page + 1, // API is 1-indexed
  });
  const { mutate: updateStatus } = useUpdateAnnotationQueueStatus();
  const { mutate: deleteQueue, isPending: isDeleting } =
    useDeleteAnnotationQueue();
  const { mutate: restoreQueue } = useRestoreAnnotationQueue();

  const results = data?.results || [];
  const count = data?.count || 0;

  const [searchInput, setSearchInput] = useState("");
  const searchTimerRef = useRef(null);

  useEffect(() => () => clearTimeout(searchTimerRef.current), []);

  const handleSearch = useCallback((event) => {
    const value = event.target.value;
    setSearchInput(value);
    clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      setFilters((prev) => ({ ...prev, search: value, page: 0 }));
    }, 300);
  }, []);

  const handleStatusFilter = useCallback((event) => {
    setFilters((prev) => ({ ...prev, status: event.target.value, page: 0 }));
  }, []);

  const handlePageChange = useCallback((newPage) => {
    setFilters((prev) => ({ ...prev, page: newPage }));
  }, []);

  const handleRowsPerPageChange = useCallback((newRowsPerPage) => {
    setFilters((prev) => ({ ...prev, limit: newRowsPerPage, page: 0 }));
  }, []);

  const handleEdit = useCallback((queue) => {
    setEditQueue(queue);
    setDrawerOpen(true);
  }, []);

  const handleDuplicate = useCallback((queue) => {
    setEditQueue({
      ...queue,
      id: undefined,
      name: `Copy of ${queue.name}`,
      status: "draft",
      _isDuplicate: true,
    });
    setDrawerOpen(true);
  }, []);

  const handleArchive = useCallback((queue) => {
    setArchiveQueue(queue);
  }, []);

  const handleConfirmDelete = useCallback(() => {
    if (!archiveQueue) return;
    deleteQueue(archiveQueue.id, {
      onSuccess: () => {
        const queueId = archiveQueue.id;
        // Soft archive — explicit Undo button so users who clicked the
        // wrong queue can recover instantly. The default-queue
        // restore-on-recreate happens server-side too, but this is the
        // explicit one-click path.
        enqueueSnackbar(
          archiveQueue.is_default
            ? "Default queue archived. Rules paused; will restore on next visit."
            : "Queue archived. Rules paused.",
          {
            variant: "info",
            action: () => (
              <Button
                color="inherit"
                size="small"
                onClick={() => restoreQueue(queueId)}
              >
                Undo
              </Button>
            ),
          },
        );
        setArchiveQueue(null);
      },
    });
  }, [archiveQueue, deleteQueue, restoreQueue]);

  const handleStatusChange = useCallback(
    (queue, newStatus) => {
      if (newStatus === "completed") {
        const total = queue.item_count ?? 0;
        const done = queue.completed_count ?? 0;
        if (total > 0 && done < total) {
          setCompleteConfirm(queue);
          return;
        }
      }
      updateStatus({ id: queue.id, status: newStatus });
    },
    [updateStatus],
  );

  const handleConfirmComplete = useCallback(() => {
    if (completeConfirm) {
      updateStatus({ id: completeConfirm.id, status: "completed" });
      setCompleteConfirm(null);
    }
  }, [completeConfirm, updateStatus]);

  const handleCreateNew = useCallback(() => {
    setEditQueue(null);
    setDrawerOpen(true);
  }, []);

  const handleDrawerClose = useCallback(() => {
    setDrawerOpen(false);
    setEditQueue(null);
  }, []);

  const isEmpty =
    !isLoading && results.length === 0 && !filters.search && !filters.status;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        backgroundColor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="flex-start"
        sx={{
          gap: 2,
          px: 3,
          pt: 2,
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: "2px",
            flex: 1,
            minWidth: 0,
          }}
        >
          <Typography
            color="text.primary"
            typography="m2"
            fontWeight={"fontWeightSemiBold"}
          >
            Annotation queues
          </Typography>
          <Box sx={{ display: "flex", gap: 0.5, alignItems: "center" }}>
            <Typography
              typography="s1"
              color="text.primary"
              fontWeight={"fontWeightRegular"}
            >
              Queue annotation projects for human review to support model
              evaluation and training
            </Typography>
          </Box>
        </Box>
        <Button
          variant="outlined"
          size="small"
          sx={{
            borderRadius: "4px",
            height: "30px",
            px: "4px",
            width: "105px",
            flexShrink: 0,
          }}
          onClick={() => {
            window.open(
              "https://docs.futureagi.com/docs/sdk/annotation-queues/",
              "_blank",
            );
          }}
        >
          <SvgColor
            src="/assets/icons/agent/docs.svg"
            sx={{ height: 16, width: 16, mr: 1 }}
          />
          <Typography typography="s2" fontWeight="fontWeightMedium">
            View Docs
          </Typography>
        </Button>
      </Stack>

      <Box sx={{ flexShrink: 0, px: 3 }}>
        <AnnotationsTabs />
      </Box>

      {isEmpty ? (
        <AnnotationQueueEmpty onCreateClick={handleCreateNew} />
      ) : (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            flex: 1,
            overflow: "hidden",
            px: 3,
          }}
        >
          <Stack
            direction="row"
            spacing={2}
            mb={2}
            alignItems="center"
            flexShrink={0}
          >
            <FormSearchField
              size="small"
              placeholder="Search"
              searchQuery={searchInput}
              onChange={handleSearch}
              sx={{
                minWidth: "250px",
                "& .MuiOutlinedInput-root": { height: "30px" },
              }}
            />
            <TextField
              size="small"
              select
              value={filters.status}
              onChange={handleStatusFilter}
              sx={{ minWidth: 160 }}
              SelectProps={{
                displayEmpty: true,
                renderValue: (v) =>
                  STATUS_OPTIONS.find((o) => o.value === v)?.label ||
                  "All Statuses",
              }}
            >
              {STATUS_OPTIONS.map((opt) => (
                <MenuItem key={opt.value} value={opt.value}>
                  {opt.label}
                </MenuItem>
              ))}
            </TextField>
            <Box sx={{ flex: 1 }} />
            <Button
              variant="contained"
              color="primary"
              startIcon={<Iconify icon="mingcute:add-line" />}
              onClick={handleCreateNew}
              disabled={!canWrite}
            >
              Create Queue
            </Button>
          </Stack>

          <AnnotationQueueTable
            data={results}
            loading={isLoading}
            page={filters.page}
            rowsPerPage={filters.limit}
            totalCount={count}
            onPageChange={handlePageChange}
            onRowsPerPageChange={handleRowsPerPageChange}
            onEdit={handleEdit}
            onDuplicate={handleDuplicate}
            onArchive={handleArchive}
            onStatusChange={handleStatusChange}
          />
        </Box>
      )}

      <CreateQueueDrawer
        open={drawerOpen}
        onClose={handleDrawerClose}
        editQueue={editQueue}
      />

      <ConfirmDialog
        open={!!archiveQueue}
        onClose={() => setArchiveQueue(null)}
        title="Archive Queue"
        content={
          <>
            <Typography>
              Archive <strong>{archiveQueue?.name}</strong>?
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Automation rules will pause and items will be hidden, but
              everything stays recoverable.
              {archiveQueue?.is_default
                ? " Visiting the project page again will restore this queue automatically."
                : " You can restore from the archived tab any time."}
            </Typography>
          </>
        }
        action={
          <LoadingButton
            size="small"
            variant="contained"
            color="warning"
            loading={isDeleting}
            onClick={handleConfirmDelete}
          >
            Archive
          </LoadingButton>
        }
      />

      <ConfirmDialog
        open={!!completeConfirm}
        onClose={() => setCompleteConfirm(null)}
        title="Complete Queue"
        content={
          <>
            <Typography>
              Some items in <strong>{completeConfirm?.name}</strong> are still
              pending annotation or review.
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Are you sure you want to mark this queue as completed?
            </Typography>
          </>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={handleConfirmComplete}
          >
            Complete
          </Button>
        }
      />
    </Box>
  );
}
