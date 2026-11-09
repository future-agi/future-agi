import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";

import axios, { endpoints } from "src/utils/axios";
import { paths } from "src/routes/paths";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayStatusChip,
} from "../../components/frame";
import { ruleLabel } from "../catalog/rule-model";
import { FILTER_SELECT } from "../rules/filter-select";
import { GATEWAY_MONO } from "../../utils/fonts";
import { EMPTY } from "../../utils/formatters";
import { apiKeyLabel } from "../../logs/explorerLabels";

const LIMIT = 100;

// metadata.guardrail_action, as the gateway writes it (guardrails/plugin.go).
// A request a check blocked, on the request or on the model's answer, is
// recorded with its 403.
export const ACTIONS = {
  blocked: { tone: "error", label: "Blocked" },
  warned: { tone: "warning", label: "Warned" },
  logged: { tone: "neutral", label: "Logged" },
};

// A check's own action in guardrail_results -> the request's action.
const CHECK_ACTIONS = [
  ["block", "blocked"],
  ["warn", "warned"],
  ["log", "logged"],
];

/**
 * What the request's checks did: the action the gateway recorded, else the
 * strongest action of the checks stored with it (block, then warn, then log).
 */
export function displayAction(action, results) {
  if (action) return action;
  const taken = new Set(storedChecks(results).map((check) => check?.action));
  return CHECK_ACTIONS.find(([check]) => taken.has(check))?.[1] || "";
}

const BLOCK_PREFIX = /^Request blocked by guardrail:\s*[^—]+?\s*—\s*/;

// The checks the gateway stored for a request (an object with `checks`, a
// bare list, or either as JSON text, as the request drawer reads them).
function storedChecks(raw) {
  let value = raw;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return [];
    }
  }
  if (Array.isArray(value)) return value;
  return Array.isArray(value?.checks) ? value.checks : [];
}

// Why the rule matched: the block message without its prefix, or the
// warnings the gateway recorded ("<rule>: <message>"), or, for a block on
// the answer (recorded with a 2xx, no error message), the acting checks'
// messages in guardrail_results.
export function matchReason(log) {
  const meta = log?.metadata || {};
  if (log?.error_message) return log.error_message.replace(BLOCK_PREFIX, "");
  if (meta.guardrail_warnings) {
    return meta.guardrail_warnings
      .split("; ")
      .map((warning) => warning.replace(/^[^:]+:\s*/, ""))
      .join("; ");
  }
  return storedChecks(log?.guardrail_results)
    .filter((check) => check?.action === "block" || check?.action === "warn")
    .map((check) => check.message || check.details)
    .filter((text) => typeof text === "string" && text !== "")
    .join("; ");
}

/** A Why cell: the reason, or the one empty mark. */
export const reasonText = (reason) => reason || EMPTY;

const ruleIds = (row) => row.rule.split(",").filter(Boolean);

function toRow(log) {
  const meta = log.metadata || {};
  const rule = meta.guardrail_name || "";
  return {
    id: log.id,
    requestId: log.request_id || log.id,
    time: log.started_at,
    rule,
    ruleLabel: rule.split(",").filter(Boolean).map(ruleLabel).join(", "),
    action: displayAction(meta.guardrail_action, log.guardrail_results),
    statusCode: log.status_code,
    model: log.model || "",
    key: apiKeyLabel(meta.auth_key_name) || "",
    reason: matchReason(log),
  };
}

const fixed = (width) => ({ width, minWidth: width, flex: 0 });

const COLUMNS = [
  {
    field: "time",
    headerName: "Time",
    valueFormatter: ({ value }) =>
      value ? new Date(value).toLocaleString() : EMPTY,
    sort: "desc",
    ...fixed(180),
  },
  // colId stays "rule" so a column layout saved before keeps applying.
  { colId: "rule", field: "ruleLabel", headerName: "Rule", ...fixed(190) },
  {
    field: "action",
    headerName: "Action",
    cellRenderer: ({ value }) =>
      ACTIONS[value] ? (
        <GatewayStatusChip
          tone={ACTIONS[value].tone}
          label={ACTIONS[value].label}
          showIcon={false}
        />
      ) : (
        value || EMPTY
      ),
    ...fixed(180),
  },
  {
    field: "statusCode",
    headerName: "Status",
    valueFormatter: ({ value }) => (value ? String(value) : EMPTY),
    ...fixed(100),
  },
  {
    field: "reason",
    headerName: "Why",
    minWidth: 260,
    flex: 3,
    valueFormatter: ({ value }) => reasonText(value),
    tooltipField: "reason",
  },
  { field: "model", headerName: "Model", ...fixed(150) },
  { field: "key", headerName: "API key", ...fixed(140) },
  {
    field: "requestId",
    headerName: "Request ID",
    ...fixed(250),
    cellRenderer: ({ value }) => (
      <Typography
        component="span"
        typography="s2"
        color="text.primary"
        noWrap
        sx={{ fontFamily: GATEWAY_MONO }}
      >
        {value}
      </Typography>
    ),
  },
];

/**
 * The latest requests a guardrail matched, newest first. A row opens the
 * request in the gateway's request logs. Filters work on the loaded rows.
 */
export default function TriggeredRequestsTab() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [rule, setRule] = useState(searchParams.get("rule") || "all");
  const [action, setAction] = useState("all");
  const [search, setSearch] = useState("");

  const logs = useQuery({
    queryKey: ["agentcc-guardrail-matched-requests"],
    queryFn: async () => {
      // No gateway_id: the request-logs contract does not take it (a dev build
      // refuses the request), and the list is already scoped to the org.
      const { data } = await axios.get(endpoints.gateway.requestLogs, {
        params: {
          guardrail_triggered: true,
          limit: LIMIT,
          page: 1,
          ordering: "-started_at",
        },
      });
      const payload = data?.result ?? data ?? {};
      const results = Array.isArray(payload) ? payload : payload.results || [];
      return {
        count: Array.isArray(payload)
          ? results.length
          : payload.count ?? results.length,
        rows: results.map(toRow),
      };
    },
    staleTime: 30000,
  });

  const rows = logs.data?.rows;
  // Rule ids in the loaded rows, ordered by the name shown for them.
  const rules = useMemo(
    () =>
      [...new Set((rows || []).flatMap(ruleIds))].sort((a, b) =>
        ruleLabel(a).localeCompare(ruleLabel(b)),
      ),
    [rows],
  );
  // A ?rule= that matched nothing in the loaded rows falls back to all rules.
  const activeRule = rows && !rules.includes(rule) ? "all" : rule;
  // While the rows load, the linked rule is the only option, so the select
  // shows it instead of an out-of-range blank.
  const ruleOptions = rows || rule === "all" ? rules : [rule];
  const shown = useMemo(() => {
    if (!rows) return undefined;
    const query = search.trim().toLowerCase();
    return rows.filter(
      (row) =>
        (activeRule === "all" || ruleIds(row).includes(activeRule)) &&
        (action === "all" || row.action === action) &&
        (!query ||
          `${row.ruleLabel} ${row.rule} ${row.reason} ${row.model} ${row.requestId} ${row.key}`
            .toLowerCase()
            .includes(query)),
    );
  }, [rows, activeRule, action, search]);

  if (logs.isError) {
    return (
      <GatewayEmptyState
        icon="logs"
        title="Matched requests could not be loaded"
        description="The request logs did not answer. Nothing is wrong with your rules."
        actionLabel="Retry"
        onAction={() => logs.refetch()}
      />
    );
  }
  if (rows && rows.length === 0) {
    return (
      <GatewayEmptyState
        icon="logs"
        title="No request has matched a rule yet"
        description="Requests a guardrail blocks, warns on or logs appear here. Try one from the Test tab."
        actionLabel="Open Test"
        onAction={() => navigate(paths.dashboard.gateway.guardrails.playground)}
      />
    );
  }

  const count = logs.data?.count ?? 0;
  const clear = () => {
    setRule("all");
    setAction("all");
    setSearch("");
  };

  return (
    <>
      <GatewayFilterBar
        search={search}
        onSearchChange={setSearch}
        searchPlaceholder="Search rule, reason, model, request id"
        actions={
          rows ? (
            <Typography typography="s2" color="text.secondary">
              {count > rows.length
                ? `The ${rows.length} most recent of ${count} requests that matched a rule`
                : `${count} requests matched a rule`}
            </Typography>
          ) : null
        }
      >
        <TextField
          select
          size="small"
          inputProps={{ "aria-label": "Rule" }}
          sx={{ ...FILTER_SELECT, minWidth: 170 }}
          value={activeRule}
          onChange={(event) => setRule(event.target.value)}
        >
          <MenuItem value="all">All rules</MenuItem>
          {ruleOptions.map((name) => (
            <MenuItem key={name} value={name}>
              {ruleLabel(name)}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          inputProps={{ "aria-label": "Action" }}
          sx={{ ...FILTER_SELECT, minWidth: 130 }}
          value={action}
          onChange={(event) => setAction(event.target.value)}
        >
          <MenuItem value="all">All actions</MenuItem>
          {Object.entries(ACTIONS).map(([value, { label }]) => (
            <MenuItem key={value} value={value}>
              {label}
            </MenuItem>
          ))}
        </TextField>
      </GatewayFilterBar>
      <GatewayGrid
        rowData={shown}
        loading={logs.isLoading}
        columnDefs={COLUMNS}
        getRowId={(params) => params.data.id}
        stateKey="gateway-guardrail-matched-requests"
        onRowClicked={(event) =>
          navigate(
            `${paths.dashboard.gateway.logs}?request_id=${encodeURIComponent(event.data.requestId)}`,
          )
        }
        rowStyle={{ cursor: "pointer" }}
        emptyState={{
          variant: "no-results",
          title: "No matched request fits these filters",
          onAction: clear,
        }}
      />
    </>
  );
}

TriggeredRequestsTab.propTypes = {
  gatewayId: PropTypes.string,
};
