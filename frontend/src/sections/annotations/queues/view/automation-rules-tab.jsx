/* eslint-disable react/prop-types */
import { useState, useMemo, useCallback, useRef } from "react";
import {
  Box,
  Button,
  Chip,
  IconButton,
  Skeleton,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import Iconify from "src/components/iconify";
import {
  useAutomationRules,
  useUpdateAutomationRule,
  useDeleteAutomationRule,
  useEvaluateRule,
} from "src/api/annotation-queues/annotation-queues";
import { fDateTime } from "src/utils/format-time";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import "src/styles/clean-data-table.css";
import CreateRuleDialog, {
  TRIGGER_FREQUENCY_OPTIONS,
} from "./create-rule-dialog";
import EditRuleDialog from "./edit-rule-dialog";

// ---------------------------------------------------------------------------
// Skeleton placeholder
// ---------------------------------------------------------------------------
const SkeletonCell = () => (
  <Box sx={{ display: "flex", alignItems: "center", height: "100%", px: 1 }}>
    <Skeleton variant="rounded" width="100%" height={20} />
  </Box>
);

const SKELETON_ROWS = Array.from({ length: 3 }, (_, i) => ({
  id: `skeleton-${i}`,
  _skeleton: true,
}));

// ---------------------------------------------------------------------------
// Cell renderers
// ---------------------------------------------------------------------------
function NameCellRenderer({ data }) {
  if (!data) return null;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Typography variant="body2" fontWeight={600} noWrap>
        {data.name}
      </Typography>
    </Box>
  );
}

function SourceCellRenderer({ data }) {
  if (!data) return null;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Chip label={data.source_type} size="small" variant="outlined" />
    </Box>
  );
}

function EnabledCellRenderer({ data, context }) {
  if (!data) return null;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Switch
        checked={data.enabled}
        onChange={() => context?.onToggleEnabled(data)}
        size="small"
      />
    </Box>
  );
}

function TriggerFrequencyCellRenderer({ data }) {
  if (!data) return null;
  const label =
    TRIGGER_FREQUENCY_OPTIONS.find(
      (option) => option.value === (data.trigger_frequency || "manual"),
    )?.label || "Manually";
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Chip label={label} size="small" variant="outlined" />
    </Box>
  );
}

function TriggersCellRenderer({ data }) {
  if (!data) return null;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Typography variant="body2">{data.trigger_count || 0}</Typography>
    </Box>
  );
}

function LastTriggeredCellRenderer({ data }) {
  if (!data) return null;
  const date = data.last_triggered_at;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Typography variant="body2" color="text.secondary">
        {date ? fDateTime(date) : "Never"}
      </Typography>
    </Box>
  );
}

function ActionsCellRenderer({ data, context }) {
  if (!data) return null;
  return (
    <Box
      sx={{ display: "flex", alignItems: "center", height: "100%", gap: 0.5 }}
    >
      <Button
        size="small"
        onClick={(e) => {
          e.stopPropagation();
          context?.onRunNow(data);
        }}
        disabled={context?.evaluatingRuleId != null || !data.enabled}
      >
        Run Now
      </Button>
      <IconButton
        size="small"
        color="error"
        onClick={(e) => {
          e.stopPropagation();
          context?.onDeleteConfirm(data);
        }}
      >
        <Iconify icon="mingcute:close-line" width={18} />
      </IconButton>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function AutomationRulesTab({ queueId, queue }) {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const gridRef = useRef(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const { data: rules = [], isLoading } = useAutomationRules(queueId);
  const { mutate: updateRule } = useUpdateAutomationRule();
  const { mutate: deleteRule } = useDeleteAutomationRule();
  const [evaluatingRuleId, setEvaluatingRuleId] = useState(null);
  const { mutate: evaluateRule } = useEvaluateRule();

  const rulesList = Array.isArray(rules) ? rules : [];

  const columnDefs = useMemo(
    () => [
      {
        field: "name",
        headerName: "Name",
        flex: 2,
        minWidth: 200,
        cellRenderer: isLoading ? SkeletonCell : NameCellRenderer,
      },
      {
        field: "source_type",
        headerName: "Source",
        flex: 1,
        minWidth: 120,
        cellRenderer: isLoading ? SkeletonCell : SourceCellRenderer,
      },
      {
        field: "enabled",
        headerName: "Enabled",
        flex: 0.8,
        minWidth: 100,
        cellRenderer: isLoading ? SkeletonCell : EnabledCellRenderer,
      },
      {
        field: "trigger_frequency",
        headerName: "Trigger",
        flex: 1,
        minWidth: 130,
        cellRenderer: isLoading ? SkeletonCell : TriggerFrequencyCellRenderer,
      },
      {
        field: "trigger_count",
        headerName: "Triggers",
        flex: 0.8,
        minWidth: 100,
        cellRenderer: isLoading ? SkeletonCell : TriggersCellRenderer,
      },
      {
        field: "last_triggered_at",
        headerName: "Last Triggered",
        flex: 1.5,
        minWidth: 180,
        cellRenderer: isLoading ? SkeletonCell : LastTriggeredCellRenderer,
      },
      {
        field: "actions",
        headerName: "",
        flex: 1.2,
        minWidth: 160,
        cellRenderer: isLoading ? SkeletonCell : ActionsCellRenderer,
        sortable: false,
        resizable: false,
      },
    ],
    [isLoading],
  );

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      filter: false,
      sortable: false,
      resizable: false,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
    }),
    [],
  );

  const gridContext = useMemo(
    () => ({
      onToggleEnabled: (rule) =>
        updateRule({ queueId, ruleId: rule.id, enabled: !rule.enabled }),
      onRunNow: (rule) => {
        setEvaluatingRuleId(rule.id);
        evaluateRule(
          { queueId, ruleId: rule.id },
          {
            onSettled: (_data, error) => {
              if (error) {
                setEvaluatingRuleId(null);
                return;
              }
              // Keep the button disabled for 30s after the request settles —
              // matches the backend's 30s "already running" 409 guard so a
              // second click can't fire a duplicate workflow or land on the
              // 409 path. 30s is short enough that legitimate re-runs aren't
              // impacted; users see a brief "Running…" / disabled state.
              setTimeout(() => setEvaluatingRuleId(null), 30_000);
            },
          },
        );
      },
      onDeleteConfirm: (rule) => setDeleteTarget(rule),
      evaluatingRuleId,
    }),
    [queueId, updateRule, evaluateRule, evaluatingRuleId],
  );

  const onCellClicked = useCallback((event) => {
    if (!event?.data) return;
    const colId = event.column?.getColId();
    if (colId === "actions" || colId === "enabled") return;
    setEditTarget(event.data);
  }, []);

  const getRowId = useCallback((params) => params.data?.id, []);

  const CustomNoRowsOverlay = useCallback(
    () => (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          gap: 0.5,
        }}
      >
        <Typography color="text.secondary">
          No automation rules configured.
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Create a rule to automatically add items to this queue.
        </Typography>
      </Box>
    ),
    [],
  );

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ py: 1, flexShrink: 0 }}
      >
        <Typography variant="subtitle1">Automation Rules</Typography>
        <Button
          variant="contained"
          color="primary"
          startIcon={<Iconify icon="mingcute:add-line" width={16} />}
          onClick={() => setCreateOpen(true)}
        >
          Add Rule
        </Button>
      </Stack>

      <Box>
        <AgGridReact
          ref={gridRef}
          theme={agTheme}
          domLayout="autoHeight"
          rowData={isLoading ? SKELETON_ROWS : rulesList}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          context={gridContext}
          rowHeight={52}
          headerHeight={42}
          pagination={false}
          animateRows={false}
          suppressRowClickSelection
          rowStyle={{ cursor: isLoading ? "default" : "pointer" }}
          onCellClicked={isLoading ? undefined : onCellClicked}
          getRowId={getRowId}
          noRowsOverlayComponent={CustomNoRowsOverlay}
        />
      </Box>

      <CreateRuleDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        queueId={queueId}
        queue={queue}
      />

      <EditRuleDialog
        open={!!editTarget}
        onClose={() => setEditTarget(null)}
        queueId={queueId}
        rule={editTarget}
        queue={queue}
      />

      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="Delete Automation Rule"
        content={`Are you sure you want to delete the rule "${deleteTarget?.name || ""}"? This action cannot be undone.`}
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => {
              deleteRule(
                { queueId, ruleId: deleteTarget.id },
                {
                  onSettled: () => setDeleteTarget(null),
                },
              );
            }}
          >
            Delete
          </Button>
        }
      />
    </Box>
  );
}
