import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useNavigate } from "react-router-dom";

import { paths } from "src/routes/paths";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
} from "../../components/frame";
import { migrateColumnState } from "../../components/frame/column-state";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { isStoredChecksMap } from "../../hooks/useConfigPatch";
import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import { useOrgConfig } from "../../providers/hooks/useOrgConfig";
import { catalogEntry, isShownCheck } from "../catalog";
import { normalizeGuardrails, ruleRows } from "../catalog/rule-model";
import {
  useGuardrailOverview,
  useGuardrailRules,
} from "../hooks/useGuardrailAnalytics";
import { useGuardrailFeedbackSummary } from "../hooks/useGuardrailFeedback";
import { useRuleActions } from "../hooks/useRuleActions";
import { exampleForCheck } from "../test/examples";
import AddCheckDialog from "./AddCheckDialog";
import PipelineSettings, { NodePipelineNote } from "./PipelineSettings";
import RuleDrawer from "./RuleDrawer";
import StoredRuleDrawer from "./StoredRuleDrawer";
import { STORED_ONLY_CHECKS } from "../../constants/storedOnlyChecks";
import {
  storedCheckEntry,
  storedCheckOn,
  storedCheckThreshold,
} from "../../settings/storedCheckSummary";
import StatStrip from "./StatStrip";
import { FILTER_SELECT } from "./filter-select";
import { ROW_ACTION, ruleColumns } from "./rule-columns";
import { EMPTY } from "../../utils/formatters";
import {
  NOT_ENFORCED_STATUS,
  guardrailsAreOff,
} from "../../utils/guardrailPipeline";

const STATUS_FILTERS = [
  { value: "all", label: "All rules" },
  { value: "on", label: "On" },
  { value: "off", label: "Off" },
];

// The user's column order, visibility and sort, kept in this browser. A
// layout saved before the columns were narrowed and On/Status pinned right
// is carried over without its widths and pin, so the new ones apply and
// the switch stays on screen.
const LAYOUT_KEY = "gateway-guardrail-rules-v2";
const OLD_LAYOUT_KEY = "gateway-guardrail-rules";
const DROPPED_KEYS = new Set(["width", "flex", "pinned"]);
const carriedLayout = (state) =>
  state
    .filter((col) => col && typeof col === "object")
    .map((col) =>
      Object.fromEntries(
        Object.entries(col).filter(([key]) => !DROPPED_KEYS.has(key)),
      ),
    );

// The last 7 days, fixed for the life of the page so the query key is stable.
function useLastWeek() {
  return useMemo(() => {
    const end = new Date();
    end.setSeconds(0, 0);
    const start = new Date(end.getTime() - 7 * 24 * 3600 * 1000);
    return { start: start.toISOString(), end: end.toISOString() };
  }, []);
}

// Said when any rule reads the model's answer: which answers those checks
// read, and what a block does to a stream.
const AFTER_LLM_NOTE =
  "After LLM checks read the text of the model's answer, not the prompt, on chat, completions, Anthropic Messages, Gemini and Responses requests, streamed or not. Other APIs' output (transcriptions, OCR, Assistants, Realtime) is not read. A stream that an After LLM check in block mode reads is held until the check passes, so its answer starts once it is complete; a blocked stream carries none of the answer and ends with the guardrail's error. A background Responses request is refused when an After LLM check would block it, as its answer never reaches the gateway.";

const formatCount = (value) =>
  value === undefined || value === null
    ? EMPTY
    : Number(value).toLocaleString();

/**
 * The org's guardrail rules: a 7-day strip, one grid (sortable, resizable,
 * remembered per browser), a drawer per rule, the catalog to add a check, and
 * the section-wide pipeline settings. Every write goes through
 * useRuleActions and reports whether the gateway has the change; only an
 * Owner or Admin may make one (useGatewayAdmin), and for anyone else each
 * write control shows disabled and says why.
 */
export default function RulesView({ gatewayId }) {
  // Before the grid reads its saved layout (on grid ready).
  useState(() => migrateColumnState(OLD_LAYOUT_KEY, LAYOUT_KEY, carriedLayout));
  const navigate = useNavigate();
  const orgConfig = useOrgConfig();
  const saved = orgConfig.data?.guardrails;
  const asMap = isStoredChecksMap(saved);
  const { canEdit } = useGatewayAdmin();
  const gatewayConfig = useGatewayConfig(gatewayId);
  // 850(a): stored rules remain editable while the node pipeline is off.
  // Keep stored enabled state distinct from current enforcement.
  const off = guardrailsAreOff(gatewayConfig);
  const week = useLastWeek();
  const overview = useGuardrailOverview(week);
  const ruleStats = useGuardrailRules(week);
  const feedback = useGuardrailFeedbackSummary();
  const actions = useRuleActions({ gatewayId, saved });

  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [picking, setPicking] = useState(false);
  // {row} for an existing rule, {meta} for a new one from the catalog.
  const [open, setOpen] = useState(null);

  // A partner check is listed only once its account is configured
  // (isShownCheck); one stored without it stays stored, unlisted.
  const rows = useMemo(
    () =>
      ruleRows(normalizeGuardrails(saved || {}), {
        ruleStats: ruleStats.data,
        feedback: feedback.data,
        asMap,
      })
        .map((row) =>
          STORED_ONLY_CHECKS.has(row.id)
            ? {
                ...row,
                storedOnly: true,
                check: storedCheckEntry(saved, row.id),
                enabled: storedCheckOn(saved, row.id),
                threshold: storedCheckThreshold(saved, row.id),
                needsSetup: false,
              }
            : row,
        )
        .filter((row) => isShownCheck(catalogEntry(row.id), row.check)),
    [saved, asMap, ruleStats.data, feedback.data],
  );

  const shown = useMemo(() => {
    const query = search.trim().toLowerCase();
    return rows.filter(
      (row) =>
        (status === "all" || (status === "on") === row.enabled) &&
        (!query ||
          `${row.label} ${row.ruleName} ${row.description}`
            .toLowerCase()
            .includes(query)),
    );
  }, [rows, search, status]);

  const openRow = useCallback(
    (row) => setOpen({ row, meta: catalogEntry(row.id) }),
    [],
  );

  const columns = useMemo(
    () =>
      ruleColumns({
        onToggle: (row, enabled) =>
          actions.toggleRule(row, enabled).catch(() => {}),
        onSetUp: openRow,
        busy: actions.isSaving,
        canEdit,
        enforced: !off,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [actions.isSaving, actions.toggleRule, openRow, canEdit, off],
  );

  const configured = useMemo(() => new Set(rows.map((row) => row.id)), [rows]);
  const active = rows.filter((row) => row.enabled && !row.needsSetup).length;
  const readsAnswer = rows.some((row) => row.stage === "response");
  const o = overview.data || {};

  if (orgConfig.isError) {
    return (
      <GatewayEmptyState
        icon="guardrails"
        title="Guardrails could not be loaded"
        description="The organization's gateway config did not load, so its rules are not shown. Nothing has changed."
        actionLabel="Retry"
        onAction={() => orgConfig.refetch()}
      />
    );
  }

  const save = async (form, { tryAfterSave = false } = {}) => {
    const checkName = open.row?.id || open.meta.name;
    const result = await actions.saveRule({
      catalogName: checkName,
      meta: open.meta,
      check: open.row?.check || null,
      label: open.row?.label || open.meta?.label,
      form,
    });
    if (!tryAfterSave) return result;
    // The shared hook rejects a stored-but-not-applied save. A missing receipt
    // (including a no-op) is not positive evidence either; keep the form open.
    if (result?.gateway_synced !== true) {
      throw new Error(
        "Gateway apply was not confirmed. Your changes may be stored, but this view cannot confirm that requests use this check. Review the rule before testing.",
        { cause: "gateway_apply_unconfirmed" },
      );
    }
    if (canEdit && !off && gatewayId && exampleForCheck(checkName)) {
      navigate(paths.dashboard.gateway.guardrails.playground, {
        state: { guardrailTest: { gatewayId, checkName } },
      });
    }
    return result;
  };

  const isEmpty = !orgConfig.isLoading && rows.length === 0;
  const clearFilters = () => {
    setSearch("");
    setStatus("all");
  };

  return (
    <>
      <StatStrip
        label="Last 7 days"
        loading={orgConfig.isLoading || overview.isLoading}
        error={overview.isError}
        items={[
          off
            ? {
                label: "Active rules",
                value: NOT_ENFORCED_STATUS,
                detail: `${rows.length} saved`,
              }
            : { label: "Active rules", value: `${active} of ${rows.length}` },
          { label: "Requests", value: formatCount(o.total_requests) },
          {
            label: "Matched",
            value: formatCount(o.guardrail_triggered),
            detail:
              o.trigger_rate !== undefined
                ? `${Number(o.trigger_rate).toFixed(1)}%`
                : null,
          },
          { label: "Blocked", value: formatCount(o.block_count) },
          { label: "Warned", value: formatCount(o.warn_count) },
        ]}
      />

      {isEmpty ? (
        <>
          <GatewayEmptyState
            icon="guardrails"
            title="No guardrails"
            description={
              off
                ? "No check is set up for this organization."
                : canEdit
                  ? "Add a check to scan requests for personal data, secrets or prompt injection before they reach a model."
                  : "An Owner or Admin can add a check to scan requests for personal data, secrets or prompt injection before they reach a model."
            }
            actionLabel="Add check"
            actionDisabled={!canEdit}
            onAction={() => canEdit && setPicking(true)}
          />
          {/* With no checks, how this gateway would run them, read-only. */}
          <NodePipelineNote
            nodePipeline={gatewayConfig.data?.guardrail_pipeline}
          />
        </>
      ) : (
        <>
          <GatewayFilterBar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search rules"
            actions={
              <GatewayAdminOnly allowed={canEdit}>
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  disabled={!canEdit}
                  onClick={() => setPicking(true)}
                >
                  Add check
                </Button>
              </GatewayAdminOnly>
            }
          >
            <TextField
              select
              size="small"
              inputProps={{ "aria-label": "Status" }}
              sx={{ ...FILTER_SELECT, minWidth: 140 }}
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              {STATUS_FILTERS.map((option) => (
                <MenuItem key={option.value} value={option.value}>
                  {option.label}
                </MenuItem>
              ))}
            </TextField>
          </GatewayFilterBar>
          <GatewayGrid
            rowData={orgConfig.isLoading ? undefined : shown}
            loading={orgConfig.isLoading}
            columnDefs={columns}
            stateKey={LAYOUT_KEY}
            getRowId={(params) => params.data.id}
            height="auto"
            domLayout="autoHeight"
            rowHeight={52}
            onRowClicked={(event) => {
              if (event.event?.target?.closest?.(`[${ROW_ACTION}]`)) return;
              openRow(event.data);
            }}
            rowStyle={{ cursor: "pointer" }}
            emptyState={{
              variant: "no-results",
              title: "No rule matches",
              onAction: clearFilters,
            }}
          />
          {readsAnswer && (
            <Typography typography="s3" color="text.secondary">
              {AFTER_LLM_NOTE}
            </Typography>
          )}
          {!orgConfig.isLoading && (
            <PipelineSettings
              guardrails={saved}
              busy={actions.isSaving}
              canEdit={canEdit}
              nodePipeline={gatewayConfig.data?.guardrail_pipeline}
              onSaveTimeout={(ms) => actions.saveTimeout(ms).catch(() => {})}
            />
          )}
        </>
      )}

      <AddCheckDialog
        open={picking}
        configured={configured}
        onClose={() => setPicking(false)}
        onPick={(meta) => {
          setPicking(false);
          setOpen({ row: null, meta });
        }}
      />
      <StoredRuleDrawer
        open={Boolean(open?.row?.storedOnly)}
        row={open?.row}
        meta={open?.meta}
        canEdit={canEdit}
        enforced={!off}
        onClose={() => setOpen(null)}
        onTurnOff={() => actions.toggleRule(open.row, false)}
        onRemove={() => actions.removeRule(open.row)}
      />
      <RuleDrawer
        open={Boolean(open) && !open?.row?.storedOnly}
        meta={open?.meta || null}
        row={open?.row || null}
        asMap={asMap}
        canEdit={canEdit}
        enforced={!off}
        onClose={() => setOpen(null)}
        allowTry={Boolean(gatewayId)}
        onSave={save}
        onRemove={open?.row ? () => actions.removeRule(open.row) : undefined}
      />
    </>
  );
}

RulesView.propTypes = {
  gatewayId: PropTypes.string,
};
