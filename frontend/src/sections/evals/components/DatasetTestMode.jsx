/* eslint-disable react/prop-types */
import {
  Autocomplete,
  Box,
  Chip,
  ClickAwayListener,
  CircularProgress,
  IconButton,
  InputAdornment,
  Paper,
  Popper,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import { TreeView, TreeItem } from "@mui/lab";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { canonicalEntries } from "src/utils/utils";
import { useDebounce } from "src/hooks/use-debounce";
import CellMarkdown from "src/sections/common/CellMarkdown";
import EvalResultDisplay from "./EvalResultDisplay";
import { buildCompositeRuntimeConfig } from "../Helpers/compositeRuntimeConfig";
import useErrorLocalizerPoll from "../hooks/useErrorLocalizerPoll";
import { useExecuteCompositeEvalAdhoc } from "../hooks/useCompositeEval";

const DATASET_PAGE_SIZE = 25;

// Dataset cells come back wrapped as { cell_id, cell_value, metadata, status, ... }.
// When cell_value is null, the old `?? cell ?? ""` pattern leaked the wrapper
// into the UI as an "Object (10 keys)" tree (TH-4979). Treat null cell_value
// as blank; only fall back to `cell` when it's not a wrapper (raw legacy values).
const unwrapCellValue = (cell) => {
  if (cell && typeof cell === "object" && "cell_id" in cell) {
    return cell.cell_value ?? "";
  }
  return cell ?? "";
};

// ---------------------------------------------------------------------------
// Nested JSON value renderer — expandable key-value tree
// ---------------------------------------------------------------------------
export function JsonValueTree({ value, expanded, onToggle }) {
  let parsed;
  try {
    parsed = typeof value === "string" ? JSON.parse(value) : value;
  } catch {
    return (
      <Typography
        variant="caption"
        component="pre"
        sx={{
          fontFamily: "monospace",
          fontSize: "11px",
          color: "primary.main",
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
          m: 0,
        }}
      >
        {value}
      </Typography>
    );
  }

  if (parsed === null || parsed === undefined) {
    return (
      <Typography variant="caption" color="text.disabled">
        null
      </Typography>
    );
  }

  if (typeof parsed !== "object") {
    return (
      <Typography
        variant="caption"
        color="primary.main"
        sx={{ fontSize: "12px" }}
      >
        {String(parsed)}
      </Typography>
    );
  }

  return (
    <Box>
      {/* Toggle */}
      <Box
        onClick={onToggle}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          cursor: "pointer",
          "&:hover": { opacity: 0.7 },
        }}
      >
        <Iconify
          icon={expanded ? "mdi:chevron-down" : "mdi:chevron-right"}
          width={14}
          sx={{ color: "text.disabled" }}
        />
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontSize: "11px" }}
        >
          {Array.isArray(parsed)
            ? `Array (${parsed.length} items)`
            : `Object (${Object.keys(parsed).length} keys)`}
        </Typography>
      </Box>

      {/* Expanded content */}
      {expanded && (
        <Box
          sx={{
            ml: 1.5,
            mt: 0.5,
            borderLeft: "1px solid",
            borderColor: "divider",
            pl: 1,
          }}
        >
          <JsonEntries data={parsed} />
        </Box>
      )}
    </Box>
  );
}

function JsonEntries({ data, depth = 0 }) {
  if (depth > 500) {
    return (
      <Typography
        variant="caption"
        color="text.disabled"
        sx={{ fontSize: "10px" }}
      >
        ...
      </Typography>
    );
  }

  const entries = Array.isArray(data)
    ? data.map((v, i) => [String(i), v])
    : canonicalEntries(data);

  return (
    <Box sx={{ display: "flex", flexDirection: "column" }}>
      {entries.map(([key, val], idx) => {
        const isObj = val !== null && typeof val === "object";
        return (
          <JsonEntryRow
            key={key}
            entryKey={key}
            entryValue={val}
            isObject={isObj}
            depth={depth}
            isLast={idx === entries.length - 1}
          />
        );
      })}
    </Box>
  );
}

function JsonEntryRow({ entryKey, entryValue, isObject, depth, isLast }) {
  const [open, setOpen] = useState(false);
  const [valueExpanded, setValueExpanded] = useState(false);

  return (
    <Box sx={{ py: 0.25 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 0.5,
          cursor: isObject ? "pointer" : "default",
          "&:hover": isObject
            ? { backgroundColor: "action.hover", borderRadius: "4px" }
            : {},
          px: 0.5,
          py: 0.15,
        }}
        onClick={(e) => {
          if (!isObject) return;
          e.stopPropagation();
          setOpen(!open);
        }}
      >
        {isObject && (
          <Iconify
            icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
            width={12}
            sx={{ color: "text.disabled", mt: 0.25, flexShrink: 0 }}
          />
        )}
        {!isObject && <Box sx={{ width: 12, flexShrink: 0 }} />}
        <Box
          sx={{
            display: "flex",
            alignItems: "flex-start",
            gap: 0.5,
            flex: 1,
            minWidth: 0,
            ...(isLast || (isObject && open)
              ? {}
              : { borderBottom: "1px solid", borderColor: "divider" }),
          }}
        >
          <Typography
            variant="caption"
            fontWeight={600}
            sx={{
              fontSize: "11px",
              minWidth: 60,
              flexShrink: 0,
              color: "text.secondary",
            }}
          >
            {entryKey}
          </Typography>
          {!isObject && (
            <Typography
              variant="caption"
              onClick={(e) => {
                e.stopPropagation();
                setValueExpanded((v) => !v);
              }}
              sx={{
                fontSize: "11px",
                color: "primary.main",
                wordBreak: "break-all",
                overflow: "hidden",
                textOverflow: "ellipsis",
                display: "-webkit-box",
                WebkitLineClamp: valueExpanded ? 9999 : 2,
                WebkitBoxOrient: "vertical",
                cursor: "pointer",
                "&:hover": { opacity: 0.85 },
              }}
            >
              {entryValue === null
                ? "null"
                : entryValue === true
                  ? "true"
                  : entryValue === false
                    ? "false"
                    : String(entryValue)}
            </Typography>
          )}
          {isObject && !open && (
            <Typography
              variant="caption"
              color="text.disabled"
              sx={{ fontSize: "10px" }}
            >
              {Array.isArray(entryValue)
                ? `[${entryValue.length}]`
                : `{${Object.keys(entryValue).length}}`}
            </Typography>
          )}
        </Box>
      </Box>
      {isObject && open && (
        <Box
          sx={{
            ml: 2,
            borderLeft: "1px solid",
            borderColor: "divider",
            pl: 0.75,
          }}
        >
          <JsonEntries data={entryValue} depth={depth + 1} />
        </Box>
      )}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// ColumnTreeSelect — dropdown with tree view for column + nested path selection
// ---------------------------------------------------------------------------
function buildTree(columnNames) {
  const roots = [];
  const nodeMap = {}; // path → node

  const getOrCreate = (path, label, parentList) => {
    if (nodeMap[path]) return nodeMap[path];
    const node = { id: path, label, path, children: [] };
    nodeMap[path] = node;
    parentList.push(node);
    return node;
  };

  columnNames.forEach((fullPath) => {
    // Split into segments: "col.a.b" → ["col","a","b"], "col[0].x" → ["col","[0]","x"]
    const segments = [];
    let current = "";
    for (let i = 0; i < fullPath.length; i++) {
      const ch = fullPath[i];
      if (ch === ".") {
        if (current) segments.push(current);
        current = "";
      } else if (ch === "[") {
        if (current) segments.push(current);
        current = "[";
      } else if (ch === "]") {
        current += "]";
        segments.push(current);
        current = "";
      } else {
        current += ch;
      }
    }
    if (current) segments.push(current);

    if (segments.length === 1) {
      getOrCreate(fullPath, segments[0], roots);
    } else {
      // Walk segments, creating intermediate nodes
      let parentList = roots;
      let builtPath = "";
      for (let i = 0; i < segments.length; i++) {
        const sep = i === 0 ? "" : segments[i].startsWith("[") ? "" : ".";
        builtPath += sep + segments[i];
        const node = getOrCreate(builtPath, segments[i], parentList);
        parentList = node.children;
      }
    }
  });
  return roots;
}

function renderTreeNode(node, onSelect) {
  const hasKids = node.children.length > 0;
  return (
    <TreeItem
      key={node.id}
      nodeId={node.id}
      label={
        <Typography
          sx={{
            fontSize: "12px",
            fontFamily: "monospace",
            fontWeight: hasKids ? 600 : 400,
            color: hasKids ? "text.primary" : "text.secondary",
            py: 0.15,
          }}
          onClick={(e) => {
            e.stopPropagation();
            onSelect(node.path);
          }}
        >
          {node.label}
        </Typography>
      }
    >
      {hasKids && node.children.map((child) => renderTreeNode(child, onSelect))}
    </TreeItem>
  );
}

function ColumnTreeSelect({ columnNames, value, onChange, isUnmapped }) {
  const [open, setOpen] = useState(false);
  const [typing, setTyping] = useState(false);
  const anchorRef = useRef(null);
  const tree = useMemo(() => buildTree(columnNames), [columnNames]);

  // Only filter when user is actively typing, not when re-opening with a selected value
  const filtered = useMemo(() => {
    if (!typing || !value) return tree;
    const q = value.toLowerCase();
    const filterNodes = (nodes) =>
      nodes
        .map((node) => {
          if (node.path.toLowerCase().startsWith(q)) return node;
          const kids = filterNodes(node.children);
          if (kids.length) return { ...node, children: kids };
          return null;
        })
        .filter(Boolean);
    return filterNodes(tree);
  }, [tree, value, typing]);

  // Collect all node IDs for default expansion
  const allIds = useMemo(() => {
    const ids = [];
    const walk = (nodes) =>
      nodes.forEach((n) => {
        ids.push(n.id);
        walk(n.children);
      });
    walk(filtered);
    return ids;
  }, [filtered]);

  const handleSelect = (path) => {
    onChange(path);
    setOpen(false);
    setTyping(false);
  };

  return (
    <Box sx={{ flex: 1 }}>
      <TextField
        ref={anchorRef}
        size="small"
        fullWidth
        value={value}
        placeholder="Select column"
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setTyping(true);
          onChange(e.target.value);
          if (!open) setOpen(true);
        }}
        autoComplete="off"
        inputProps={{
          autoComplete: "off",
          autoCorrect: "off",
          spellCheck: false,
        }}
        InputProps={{
          sx: { fontSize: "12px", fontFamily: "monospace", height: 30, py: 0 },
          endAdornment: (
            <InputAdornment position="end">
              <Iconify
                icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
                width={16}
                sx={{ color: "text.disabled", cursor: "pointer" }}
                onClick={() => {
                  setOpen((p) => !p);
                  setTyping(false);
                }}
              />
            </InputAdornment>
          ),
        }}
        sx={{
          ...(isUnmapped && {
            "& .MuiOutlinedInput-notchedOutline": {
              borderColor: "warning.main",
            },
          }),
        }}
      />
      {open && filtered.length > 0 && (
        <Popper
          open
          anchorEl={anchorRef.current}
          placement="bottom-start"
          style={{ zIndex: 1301, width: anchorRef.current?.offsetWidth || 240 }}
        >
          <ClickAwayListener
            onClickAway={(e) => {
              // Don't close if clicking the input field itself
              if (anchorRef.current?.contains(e.target)) return;
              setOpen(false);
              setTyping(false);
            }}
          >
            <Paper
              elevation={8}
              sx={{
                mt: 0.5,
                borderRadius: "8px",
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              <Box sx={{ maxHeight: 260, overflow: "auto", py: 0.5 }}>
                <TreeView
                  defaultExpanded={allIds}
                  defaultCollapseIcon={
                    <Iconify
                      icon="mdi:chevron-down"
                      width={14}
                      sx={{ color: "text.disabled" }}
                    />
                  }
                  defaultExpandIcon={
                    <Iconify
                      icon="mdi:chevron-right"
                      width={14}
                      sx={{ color: "text.disabled" }}
                    />
                  }
                  sx={{
                    "& .MuiTreeItem-content": { py: 0.1, borderRadius: "4px" },
                    "& .MuiTreeItem-content:hover": { bgcolor: "action.hover" },
                  }}
                >
                  {filtered.map((node) => renderTreeNode(node, handleSelect))}
                </TreeView>
              </Box>
            </Paper>
          </ClickAwayListener>
        </Popper>
      )}
    </Box>
  );
}

// Walk a JSON value and extract dot-notation keys (max 3 levels deep).
function extractKeysFromValue(raw) {
  let parsed = null;
  if (raw && typeof raw === "object") parsed = raw;
  else if (typeof raw === "string" && raw.trim()) {
    try {
      const p = JSON.parse(raw);
      if (p && typeof p === "object") parsed = p;
    } catch {
      /* not JSON */
    }
  }
  if (!parsed) return [];
  const keys = [];
  const walk = (obj, prefix, depth) => {
    if (depth > 3 || !obj || typeof obj !== "object") return;
    if (Array.isArray(obj)) {
      // Show first 3 indices (or less if array is shorter)
      const count = Math.min(obj.length, 3);
      for (let i = 0; i < count; i++) {
        keys.push(`${prefix}[${i}]`);
        // Recurse into each element to discover nested object keys
        if (obj[i] && typeof obj[i] === "object") {
          walk(obj[i], `${prefix}[${i}]`, depth + 1);
        }
      }
      return;
    }
    for (const k of Object.keys(obj)) {
      const path = prefix ? `${prefix}.${k}` : k;
      keys.push(path);
      if (obj[k] && typeof obj[k] === "object") walk(obj[k], path, depth + 1);
    }
  };
  walk(parsed, "", 0);
  return keys;
}

// Resolve a dot/bracket path inside a parsed JSON value.
function resolveNestedValue(raw, jsonPath) {
  let parsed = null;
  if (raw && typeof raw === "object") parsed = raw;
  else if (typeof raw === "string") {
    try {
      parsed = JSON.parse(raw);
    } catch {
      return raw;
    }
  }
  if (!parsed) return raw;
  const parts = jsonPath.split(/[.[\]]/).filter(Boolean);
  let cur = parsed;
  for (const p of parts) {
    if (cur == null) return undefined;
    cur = /^\d+$/.test(p)
      ? Array.isArray(cur)
        ? cur[parseInt(p, 10)]
        : undefined
      : typeof cur === "object"
        ? cur[p]
        : undefined;
  }
  return cur;
}

const DatasetTestMode = React.forwardRef(
  (
    {
      templateId,
      model = "turing_large",
      variables = [],
      codeParams = {},
      onTestResult,
      onColumnsLoaded,
      initialDatasetId = "",
      onReadyChange,
      onClearResult,
      contextOptions = ["variables_only"],
      errorLocalizerEnabled = false,
      initialMapping = null,
      isComposite = false,
      compositeAdhocConfig = null,
      sourceColumns,
      extraColumns,
    },
    ref,
  ) => {
    // When sourceColumns is provided (workbench mode), skip dataset fetching
    // and use the provided columns for variable mapping instead.
    const isWorkbenchMode = !!sourceColumns?.length;

    // Keep ref to templateId for imperative calls
    const templateIdRef = useRef(templateId);
    useEffect(() => {
      templateIdRef.current = templateId;
    }, [templateId]);

    // Dataset list (searchable, paginated, infinite scroll)
    const [datasetOptions, setDatasetOptions] = useState([]);
    const [selectedDataset, setSelectedDataset] = useState(null);
    const [selectedDatasetId, setSelectedDatasetId] =
      useState(initialDatasetId);
    const [datasetSearch, setDatasetSearch] = useState("");
    const debouncedDatasetSearch = useDebounce(datasetSearch.trim(), 400);
    const [datasetPage, setDatasetPage] = useState(0);
    const [datasetHasMore, setDatasetHasMore] = useState(true);
    const [loadingDatasets, setLoadingDatasets] = useState(false);
    const [datasetOpen, setDatasetOpen] = useState(false);

    // Dataset data
    const [columns, setColumns] = useState([]);
    const [jsonSchemas, setJsonSchemas] = useState({});
    const [rows, setRows] = useState([]);
    const [totalRows, setTotalRows] = useState(0);
    const [currentRowIndex, setCurrentRowIndex] = useState(0);
    const [loadingData, setLoadingData] = useState(false);

    // Variable mapping — seeded with saved values when editing an existing eval
    const [mapping, setMapping] = useState(
      initialMapping && typeof initialMapping === "object"
        ? { ...initialMapping }
        : {},
    );

    // Search + expand
    const [tableSearch, setTableSearch] = useState("");
    const [expandedCols, setExpandedCols] = useState({});

    // Eval result
    const [isRunning, setIsRunning] = useState(false);
    const [result, setResult] = useState(null);
    const [error, setError] = useState(null);
    const [resultFormat, setResultFormat] = useState("markdown");
    // Async error localization — playground returns before the localizer
    // task finishes, so we poll `/get-eval-logs?log_id=...` and merge the
    // resulting error_details into `result` for EvalResultDisplay.
    const { state: errorLocalizerState, start: startErrorLocalizerPoll } =
      useErrorLocalizerPoll();
    const executeCompositeAdhoc = useExecuteCompositeEvalAdhoc();

    // 1. Fetch dataset list — paginated + searchable
    const fetchDatasets = useCallback(async (page, search, append) => {
      setLoadingDatasets(true);
      try {
        const { data } = await axios.get(endpoints.develop.getDatasets(), {
          params: {
            search_text: search || null,
            page,
            page_size: DATASET_PAGE_SIZE,
          },
        });
        if (data?.status) {
          const items = data?.result?.datasets || data?.result?.rowData || [];
          const total = data?.result?.total_count;
          setDatasetOptions((prev) => {
            const next = append ? [...prev, ...items] : items;
            if (typeof total === "number") {
              setDatasetHasMore(next.length < total);
            } else {
              setDatasetHasMore(items.length === DATASET_PAGE_SIZE);
            }
            return next;
          });
        }
      } catch {
        // silent
      } finally {
        setLoadingDatasets(false);
      }
    }, []);

    // Reset + fetch page 0 whenever the debounced search changes (and on mount)
    // Skip in workbench mode — no dataset picker needed.
    useEffect(() => {
      if (isWorkbenchMode) return;
      setDatasetPage(0);
      fetchDatasets(0, debouncedDatasetSearch, false);
    }, [debouncedDatasetSearch, fetchDatasets, sourceColumns]);

    const handleDatasetListboxScroll = useCallback(
      (event) => {
        const listbox = event.currentTarget;
        if (
          listbox.scrollTop + listbox.clientHeight >=
            listbox.scrollHeight - 8 &&
          datasetHasMore &&
          !loadingDatasets
        ) {
          const nextPage = datasetPage + 1;
          setDatasetPage(nextPage);
          fetchDatasets(nextPage, debouncedDatasetSearch, true);
        }
      },
      [
        datasetHasMore,
        loadingDatasets,
        datasetPage,
        debouncedDatasetSearch,
        fetchDatasets,
      ],
    );

    // 2. Fetch dataset columns + rows + JSON schema when dataset selected
    // Skip in workbench mode — columns come from sourceColumns prop.
    useEffect(() => {
      if (isWorkbenchMode) return;
      if (!selectedDatasetId) {
        setColumns([]);
        setRows([]);
        setTotalRows(0);
        setCurrentRowIndex(0);
        onColumnsLoaded?.([], {});
        return;
      }

      const controller = new AbortController();

      const fetchData = async () => {
        setLoadingData(true);
        try {
          // Fetch dataset detail and JSON schema in parallel
          const [detailRes, schemaRes] = await Promise.all([
            axios.get(endpoints.develop.getDatasetDetail(selectedDatasetId), {
              params: { current_page_index: 0, page_size: 50 },
              signal: controller.signal,
            }),
            axios
              .get(endpoints.develop.getJsonColumnSchema(selectedDatasetId), {
                signal: controller.signal,
              })
              .catch((e) => {
                if (e?.name === "CanceledError" || e?.name === "AbortError")
                  throw e;
                return { data: { result: {} } };
              }),
          ]);

          const res = detailRes.data?.result || {};
          const cols = res.column_config || [];
          const tableRows = res.table || res.row_data || [];
          const total = res.metadata?.total_rows || tableRows.length || 0;
          const jsonSchemas = schemaRes.data?.result || {};

          setColumns(cols);
          setJsonSchemas(jsonSchemas);
          setRows(tableRows);
          setTotalRows(total);
          setCurrentRowIndex(0);
          onColumnsLoaded?.(cols, jsonSchemas);
        } catch (e) {
          if (e?.name === "CanceledError" || e?.name === "AbortError") return;
          setColumns([]);
          setRows([]);
          onColumnsLoaded?.([], {});
        } finally {
          if (!controller.signal.aborted) setLoadingData(false);
        }
      };

      fetchData();
      return () => controller.abort();
    }, [selectedDatasetId, onColumnsLoaded, sourceColumns]);

    // Current row data
    const currentRow = rows[currentRowIndex] || null;

    // Extract column name → value pairs for current row
    const rowCells = useMemo(() => {
      if (!currentRow || !columns.length) return [];
      return columns
        .filter(
          (col) => col.id && col.name && !["id", "orgId"].includes(col.name),
        )
        .map((col) => {
          const cell = currentRow[col.id];
          const value = unwrapCellValue(cell);
          // Don't pre-stringify objects/arrays — coercing them via
          // String(value) produces the literal text "[object Object]"
          // which then fails the downstream JSON.parse check, falls
          // through to the plain Typography branch, and renders that
          // literal text in the row detail table. Keep the original
          // type so the rendering can detect objects via `typeof` and
          // route them to JsonValueTree.
          let cellValue;
          if (value == null) {
            cellValue = "";
          } else if (typeof value === "object") {
            cellValue = value;
          } else {
            cellValue = String(value);
          }
          return {
            id: col.id,
            name: col.name,
            value: cellValue,
            raw: cell,
          };
        });
    }, [currentRow, columns]);

    // extraColumns: virtual columns appended to the mapping dropdown on top of
    // fetched dataset columns (e.g. "output" / "prompt_chain" for experiment
    // evals that reference computed values, not real dataset cells). Does NOT
    // gate dataset fetching — unlike sourceColumns/workbench mode.
    const extraNameToField = useMemo(() => {
      const m = {};
      (extraColumns || []).forEach((col) => {
        if (typeof col === "object") {
          const name =
            col.headerName || col.field || col.name || col.label || "";
          m[name] = col.field || name;
        }
      });
      return m;
    }, [extraColumns]);

    // Reverse map for edit-mode pre-fill: the saved mapping stores the
    // virtual column's `field` ("output", "prompt_chain") but the dropdown
    // renders by display name ("Output", "Prompt Chain"). Without this
    // resolution the Select shows empty for pre-existing mappings.
    const extraFieldToName = useMemo(() => {
      const m = {};
      (extraColumns || []).forEach((col) => {
        if (typeof col === "object") {
          const name =
            col.headerName || col.field || col.name || col.label || "";
          const field = col.field || name;
          if (field) m[field] = name;
        }
      });
      return m;
    }, [extraColumns]);

    // Build expanded column names (with JSON sub-paths) and name→ID
    // lookup in one pass. Discovers nested keys from both the backend
    // JSON schema AND runtime cell-value inspection of the current row.
    const { columnNames, nameToId } = useMemo(() => {
      if (isWorkbenchMode) {
        const names = sourceColumns.map((col) =>
          typeof col === "string"
            ? col
            : col.headerName || col.field || col.name || col.label || "",
        );
        return { columnNames: names, nameToId: {} };
      }
      const names = [];
      const n2id = {};
      const seen = new Set();
      const add = (display, id) => {
        if (seen.has(display)) return;
        seen.add(display);
        names.push(display);
        if (id) n2id[display] = id;
      };
      columns.forEach((c) => {
        if (!c.id || !c.name || ["id", "orgId"].includes(c.name)) return;
        add(c.name, c.id);
        // Collect sub-paths from backend schema + runtime cell values
        const schemaPaths = jsonSchemas?.[c.id]?.keys || [];
        const cell = currentRow?.[c.id];
        const runtimePaths = cell
          ? extractKeysFromValue(unwrapCellValue(cell))
          : [];
        const allPaths = new Set([...schemaPaths, ...runtimePaths]);
        allPaths.forEach((path) => {
          // Bracket paths: "col[0]" not "col.[0]"
          const sep = path.startsWith("[") ? "" : ".";
          add(`${c.name}${sep}${path}`, `${c.id}${sep}${path}`);
        });
      });
      if (!extraColumns?.length) return { columnNames: names, nameToId: n2id };
      const extras = (extraColumns || [])
        .map((col) =>
          typeof col === "string"
            ? col
            : col.headerName || col.field || col.name || col.label || "",
        )
        .filter(Boolean);
      const extraSet = new Set(extras);
      return {
        columnNames: [...extras, ...names.filter((n) => !extraSet.has(n))],
        nameToId: n2id,
      };
    }, [
      columns,
      sourceColumns,
      extraColumns,
      isWorkbenchMode,
      jsonSchemas,
      currentRow,
    ]);

    // Workbench mode: map display name → field identifier (e.g. "model_output" → "output_prompt")
    const sourceNameToField = useMemo(() => {
      if (!isWorkbenchMode) return {};
      const m = {};
      sourceColumns.forEach((col) => {
        if (typeof col === "object") {
          const name =
            col.headerName || col.field || col.name || col.label || "";
          m[name] = col.field || name;
        }
      });
      return m;
    }, [sourceColumns, isWorkbenchMode]);

    // Resolve UUID-based mapping values to display names (edit mode).
    // Handles both plain UUIDs and "uuid.path" nested references.
    const uuidResolutionDone = React.useRef(false);
    useEffect(() => {
      if (!columns.length && !Object.keys(extraFieldToName).length) return;
      if (uuidResolutionDone.current) return;
      const idToName = {};
      columns.forEach((c) => {
        if (!c.id || !c.name) return;
        idToName[c.id] = c.name;
        // Reverse-map nested IDs: "uuid.path" → "col.path"
        const paths = jsonSchemas?.[c.id]?.keys || [];
        paths.forEach((p) => {
          idToName[`${c.id}.${p}`] = `${c.name}.${p}`;
        });
      });
      setMapping((prev) => {
        const next = { ...prev };
        let changed = false;
        Object.keys(next).forEach((variable) => {
          const val = next[variable];
          if (!val) return;
          if (idToName[val]) {
            next[variable] = idToName[val];
            changed = true;
          } else if (extraFieldToName[val]) {
            next[variable] = extraFieldToName[val];
            changed = true;
          }
        });
        if (changed) uuidResolutionDone.current = true;
        return changed ? next : prev;
      });
    }, [columns, extraFieldToName, jsonSchemas]);

    // Prune stale mapping keys when variables list changes (instruction edits).
    useEffect(() => {
      if (!variables.length) return;
      const varSet = new Set(variables);
      setMapping((prev) => {
        const pruned = {};
        let changed = false;
        Object.keys(prev).forEach((k) => {
          if (varSet.has(k)) pruned[k] = prev[k];
          else changed = true;
        });
        return changed ? pruned : prev;
      });
    }, [variables]);

    // Auto-map variables to columns when names match (case-insensitive)
    useEffect(() => {
      if (!columnNames.length || !variables.length) return;
      setMapping((prev) => {
        const next = { ...prev };
        let changed = false;
        variables.forEach((v) => {
          if (next[v]) return; // Already mapped
          const vt = v.trim();
          // Try exact match, then case-insensitive, then trimmed+normalized
          const exact = columnNames.find((c) => c === vt);
          const caseInsensitive =
            !exact &&
            columnNames.find((c) => c.toLowerCase() === vt.toLowerCase());
          const normalized =
            !exact &&
            !caseInsensitive &&
            columnNames.find(
              (c) =>
                c.trim().toLowerCase().replace(/\s+/g, " ") ===
                vt.toLowerCase().replace(/\s+/g, " "),
            );
          const match = exact || caseInsensitive || normalized;
          if (match) {
            next[v] = match;
            changed = true;
          }
        });
        return changed ? next : prev;
      });
    }, [variables, columnNames]);

    // Filter cells by search
    const filteredCells = useMemo(() => {
      if (!tableSearch.trim()) return rowCells;
      const q = tableSearch.toLowerCase();
      return rowCells.filter(
        (c) =>
          c.name.toLowerCase().includes(q) || c.value.toLowerCase().includes(q),
      );
    }, [rowCells, tableSearch]);

    // Run test
    const handleRunTest = useCallback(async () => {
      const tid = templateIdRef.current;
      if (!tid) {
        onTestResult?.(false, "No template ID — save the eval first");
        return;
      }
      if (!selectedDatasetId && !isWorkbenchMode) {
        onTestResult?.(false, "Select a dataset first");
        return;
      }

      setIsRunning(true);
      setResult(null);
      setError(null);

      try {
        // Build mapping from variable → column cell value + detect data types
        const evalMapping = {};
        const inputDataTypes = {};
        const rowContext = {};
        const imageUrls = [];
        const compositeConfig = buildCompositeRuntimeConfig({
          codeParams,
        });

        if (isWorkbenchMode) {
          // Workbench mode: mapping sends variable → field name (e.g. input_prompt)
          // The backend resolves these against the prompt's actual input/output.
          for (const variable of variables) {
            const mappedColName = mapping[variable];
            if (mappedColName) {
              evalMapping[variable] =
                sourceNameToField[mappedColName] || mappedColName;
              inputDataTypes[variable] = "text";
            }
          }
        } else {
          // Dataset mode: mapping sends variable → actual cell values.
          // Supports nested paths (e.g. "col.key" → resolve from cell JSON).
          for (const variable of variables) {
            const mappedColName = mapping[variable];
            if (mappedColName && currentRow) {
              let baseName = mappedColName;
              let jsonPath = null;
              const dot = mappedColName.indexOf(".");
              const bracket = mappedColName.indexOf("[");
              if (dot > 0 && (bracket < 0 || dot < bracket)) {
                baseName = mappedColName.substring(0, dot);
                jsonPath = mappedColName.substring(dot + 1);
              } else if (bracket > 0) {
                baseName = mappedColName.substring(0, bracket);
                jsonPath = mappedColName.substring(bracket);
              }
              const col = columns.find((c) => c.name === baseName);
              if (col) {
                const cell = currentRow[col.id];
                let cellValue = unwrapCellValue(cell);
                if (jsonPath) {
                  const resolved = resolveNestedValue(cellValue, jsonPath);
                  if (resolved !== undefined && resolved !== null) {
                    cellValue =
                      typeof resolved === "object"
                        ? JSON.stringify(resolved)
                        : String(resolved);
                  }
                }
                evalMapping[variable] = cellValue;
                const dt = col.data_type || "text";
                inputDataTypes[variable] = ["image", "images"].includes(dt)
                  ? "image"
                  : dt === "audio"
                    ? "audio"
                    : "text";
              }
            }
          }

          // Build full row context for data injection (all column values)
          if (currentRow && columns.length) {
            columns
              .filter(
                (c) => c.id && c.name && !["id", "orgId"].includes(c.name),
              )
              .forEach((col) => {
                const cell = currentRow[col.id];
                const val = unwrapCellValue(cell);
                const valStr =
                  typeof val === "object" ? JSON.stringify(val) : String(val);
                rowContext[col.name] = valStr;

                // Collect file URLs from image/audio/pdf columns
                const isFileCol = [
                  "image",
                  "images",
                  "audio",
                  "pdf",
                  "file",
                ].includes(col.data_type);
                const isFileUrl =
                  /\.(png|jpg|jpeg|gif|webp|svg|mp3|wav|ogg|m4a|pdf|doc|docx)(\?|$)/i.test(
                    valStr,
                  );
                if ((isFileCol || isFileUrl) && valStr.startsWith("http")) {
                  imageUrls.push(valStr); // imageUrls handles all file types
                }
              });
          }
        }

        // Composite evals use the composite execute endpoint
        const { data } = isComposite
          ? compositeAdhocConfig
            ? {
                data: {
                  status: true,
                  result: await executeCompositeAdhoc.mutateAsync({
                    ...compositeAdhocConfig,
                    mapping: evalMapping,
                    model,
                    config: compositeConfig,
                    error_localizer: errorLocalizerEnabled,
                    input_data_types: inputDataTypes,
                    row_context: rowContext,
                  }),
                },
              }
            : await axios.post(
                endpoints.develop.eval.executeCompositeEval(tid),
                {
                  mapping: evalMapping,
                  model,
                  config: compositeConfig,
                  error_localizer: errorLocalizerEnabled,
                  input_data_types: inputDataTypes,
                  row_context: rowContext,
                },
              )
          : await axios.post(endpoints.develop.eval.evalPlayground, {
              template_id: tid,
              model,
              error_localizer: errorLocalizerEnabled,
              config: {
                mapping: evalMapping,
                ...(Object.keys(codeParams || {}).length > 0
                  ? { params: codeParams }
                  : {}),
                image_urls: imageUrls.length > 0 ? imageUrls : undefined,
                // Send data_injection flags from contextOptions — same pattern
                // as EvalPickerConfigFull (tracing tab) so the BE knows which
                // context toggles are enabled.
                ...(() => {
                  const flags = {};
                  if (contextOptions.includes("dataset_row"))
                    flags.full_row = true;
                  if (contextOptions.includes("full_row"))
                    flags.full_row = true;
                  if (contextOptions.includes("span_context"))
                    flags.span_context = true;
                  if (contextOptions.includes("trace_context"))
                    flags.trace_context = true;
                  if (contextOptions.includes("session_context"))
                    flags.session_context = true;
                  if (contextOptions.includes("call_context"))
                    flags.call_context = true;
                  return Object.keys(flags).length > 0
                    ? { data_injection: flags }
                    : {};
                })(),
              },
              input_data_types: inputDataTypes,
              row_context: rowContext,
            });

        if (data?.status) {
          const nextResult = isComposite
            ? {
                output:
                  data.result?.aggregation_enabled &&
                  data.result?.aggregate_score != null
                    ? data.result.aggregate_score
                    : null,
                reason: data.result?.summary || "",
                compositeResult: data.result,
              }
            : data.result;
          setResult(nextResult);
          onTestResult?.(true, nextResult);
          if (!isComposite && errorLocalizerEnabled && data.result?.log_id) {
            startErrorLocalizerPoll(data.result.log_id);
          }
        } else {
          const errMsg = data?.result || "Evaluation failed";
          setError(errMsg);
          onTestResult?.(false, errMsg);
        }
      } catch (err) {
        const errMsg =
          err?.result ||
          err?.detail ||
          err?.message ||
          "Failed to run evaluation";
        setError(errMsg);
        onTestResult?.(false, errMsg);
      } finally {
        setIsRunning(false);
      }
    }, [
      selectedDatasetId,
      variables,
      mapping,
      currentRow,
      columns,
      onTestResult,
      errorLocalizerEnabled,
      startErrorLocalizerPoll,
      isWorkbenchMode,
      sourceNameToField,
      codeParams,
      isComposite,
      compositeAdhocConfig,
      model,
      executeCompositeAdhoc,
    ]);

    // Readiness: dataset selected + (all variables mapped OR a non-template
    // context option is enabled — e.g. dataset_row, span_context, trace_context)
    const hasNonTemplateContext = (contextOptions || []).some(
      (opt) => opt && opt !== "variables_only",
    );
    const allMapped =
      variables.length === 0 ||
      hasNonTemplateContext ||
      variables.every((v) => mapping[v]);

    // Mapping with column IDs for the save payload.
    // nameToId (from columnMaps memo above) already handles nested paths.
    // For freeSolo typed paths not in the map, resolve base column → uuid.
    const idMapping = useMemo(() => {
      const m = {};
      if (isWorkbenchMode) {
        Object.entries(mapping).forEach(([variable, colName]) => {
          m[variable] = sourceNameToField[colName] || colName;
        });
      } else {
        Object.entries(mapping).forEach(([variable, colName]) => {
          if (!colName) return;
          if (extraNameToField[colName]) {
            m[variable] = extraNameToField[colName];
            return;
          }
          if (nameToId[colName]) {
            m[variable] = nameToId[colName];
            return;
          }
          // freeSolo: "col.typed_path" → "uuid.typed_path"
          const dot = colName.indexOf(".");
          const bracket = colName.indexOf("[");
          const split =
            dot > 0 && (bracket < 0 || dot < bracket)
              ? dot
              : bracket > 0
                ? bracket
                : -1;
          if (split > 0) {
            const base = colName.substring(0, split);
            const baseId = nameToId[base];
            if (baseId) {
              m[variable] = `${baseId}${colName.substring(split)}`;
              return;
            }
          }
          m[variable] = colName;
        });
      }
      return m;
    }, [
      mapping,
      nameToId,
      isWorkbenchMode,
      sourceNameToField,
      extraNameToField,
    ]);
    const isReady = (!!selectedDatasetId || isWorkbenchMode) && allMapped;

    useEffect(() => {
      onReadyChange?.(isReady, idMapping);
    }, [isReady, idMapping]); // eslint-disable-line react-hooks/exhaustive-deps

    // Expose runTest + validation to parent via ref
    useImperativeHandle(
      ref,
      () => ({
        runTest: (overrideTemplateId) => {
          if (overrideTemplateId) templateIdRef.current = overrideTemplateId;
          handleRunTest();
        },
        get isReady() {
          return (!!selectedDatasetId || isWorkbenchMode) && allMapped;
        },
        get mapping() {
          return mapping;
        },
      }),
      [handleRunTest, selectedDatasetId, allMapped, mapping],
    );

    return (
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        {/* Dataset selector — hidden when initialDatasetId or sourceColumns is provided */}
        {!initialDatasetId && !isWorkbenchMode && (
          <Box>
            <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
              Choose Dataset<span style={{ color: "#d32f2f" }}>*</span>
            </Typography>
            <Autocomplete
              fullWidth
              size="small"
              open={datasetOpen}
              onOpen={() => {
                setDatasetOpen(true);
                setDatasetSearch("");
              }}
              onClose={() => setDatasetOpen(false)}
              value={selectedDataset}
              onChange={(_, newValue) => {
                setSelectedDataset(newValue);
                setSelectedDatasetId(newValue?.id || "");
              }}
              onInputChange={(_, newInput, reason) => {
                if (reason === "input") setDatasetSearch(newInput);
                if (reason === "clear") setDatasetSearch("");
              }}
              options={datasetOptions}
              getOptionLabel={(opt) => opt?.name || opt?.id || ""}
              isOptionEqualToValue={(a, b) => a?.id === b?.id}
              filterOptions={(x) => x}
              loading={loadingDatasets}
              noOptionsText={
                loadingDatasets ? "Loading..." : "No datasets found"
              }
              ListboxProps={{
                onScroll: handleDatasetListboxScroll,
                sx: { maxHeight: 320 },
              }}
              renderOption={(props, option) => (
                <Box
                  component="li"
                  {...props}
                  key={option.id}
                  sx={{ fontSize: "13px" }}
                >
                  {option.name || option.id}
                </Box>
              )}
              renderInput={(params) => (
                <TextField
                  {...params}
                  placeholder="Choose from dataset list"
                  InputProps={{
                    ...params.InputProps,
                    endAdornment: loadingDatasets ? (
                      <InputAdornment position="end">
                        <CircularProgress size={14} />
                      </InputAdornment>
                    ) : (
                      params.InputProps.endAdornment
                    ),
                  }}
                  sx={{ "& .MuiInputBase-root": { fontSize: "13px" } }}
                />
              )}
            />
          </Box>
        )}

        {/* Row navigator */}
        {!isWorkbenchMode && selectedDatasetId && totalRows > 0 && (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Typography variant="caption" color="text.secondary">
              Test on row {currentRowIndex + 1} of {totalRows}
            </Typography>
            <IconButton
              size="small"
              disabled={currentRowIndex === 0}
              onClick={() => {
                setCurrentRowIndex((i) => Math.max(0, i - 1));
                setResult(null);
                setError(null);
                onClearResult?.();
              }}
              sx={{ width: 24, height: 24 }}
            >
              <Iconify icon="mdi:chevron-left" width={16} />
            </IconButton>
            <IconButton
              size="small"
              disabled={currentRowIndex >= totalRows - 1}
              onClick={() => {
                setCurrentRowIndex((i) => Math.min(totalRows - 1, i + 1));
                setResult(null);
                setError(null);
                onClearResult?.();
              }}
              sx={{ width: 24, height: 24 }}
            >
              <Iconify icon="mdi:chevron-right" width={16} />
            </IconButton>
          </Box>
        )}

        {/* Loading */}
        {!isWorkbenchMode && loadingData && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
            <CircularProgress size={20} />
          </Box>
        )}

        {/* Empty dataset */}
        {!isWorkbenchMode &&
          selectedDatasetId &&
          !loadingData &&
          totalRows === 0 && (
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 0.75,
                py: 3,
                border: "1px dashed",
                borderColor: "divider",
                borderRadius: "8px",
              }}
            >
              <Iconify
                icon="mdi:table-off"
                width={28}
                sx={{ color: "text.disabled" }}
              />
              <Typography
                variant="body2"
                fontWeight={600}
                color="text.secondary"
              >
                No rows in this dataset
              </Typography>
              <Typography variant="caption" color="text.disabled">
                Add rows to the dataset before running a test
              </Typography>
            </Box>
          )}

        {/* Row data table */}
        {!isWorkbenchMode && rowCells.length > 0 && !loadingData && (
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "6px",
              overflow: "hidden",
            }}
          >
            {/* Search */}
            <Box
              sx={{
                px: 1,
                py: 0.75,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <TextField
                size="small"
                fullWidth
                placeholder="Search columns or values..."
                value={tableSearch}
                onChange={(e) => setTableSearch(e.target.value)}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Iconify
                        icon="mdi:magnify"
                        width={14}
                        sx={{ color: "text.disabled" }}
                      />
                    </InputAdornment>
                  ),
                  sx: { fontSize: "12px", height: 28 },
                }}
              />
            </Box>

            {/* Header */}
            <Box
              sx={{
                display: "flex",
                px: 1.5,
                py: 0.5,
                backgroundColor: (theme) =>
                  theme.palette.mode === "dark"
                    ? "rgba(255,255,255,0.03)"
                    : "#fafafa",
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                variant="caption"
                fontWeight={600}
                sx={{ width: 130, flexShrink: 0 }}
              >
                Columns
              </Typography>
              <Typography variant="caption" fontWeight={600} sx={{ flex: 1 }}>
                Value
              </Typography>
            </Box>

            {/* Rows */}
            <Box sx={{ maxHeight: 400, overflowY: "auto" }}>
              {filteredCells.map((cell) => {
                const col = columns.find((c) => c.id === cell.id);
                const dataType = col?.data_type || "text";
                // Three cases: explicit json column type, raw value is
                // already an object/array (we stopped pre-stringifying
                // them above), or value is a JSON-encoded string we can
                // parse into an object.
                const isJson =
                  dataType === "json" ||
                  (cell.value !== null && typeof cell.value === "object") ||
                  (() => {
                    if (typeof cell.value !== "string") return false;
                    try {
                      const p = JSON.parse(cell.value);
                      return p !== null && typeof p === "object";
                    } catch {
                      return false;
                    }
                  })();
                const isImage =
                  dataType === "image" ||
                  /\.(png|jpg|jpeg|gif|webp|svg)(\?|$)/i.test(cell.value);
                const isAudio =
                  dataType === "audio" ||
                  /\.(mp3|wav|ogg|m4a|webm)(\?|$)/i.test(cell.value);

                return (
                  <Box
                    key={cell.id}
                    sx={{
                      display: "flex",
                      alignItems: "flex-start",
                      px: 1.5,
                      py: 0.6,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      "&:last-child": { borderBottom: "none" },
                      "&:hover": { backgroundColor: "action.hover" },
                    }}
                  >
                    {/* Column name */}
                    <Typography
                      variant="caption"
                      fontWeight={500}
                      noWrap
                      sx={{ width: 130, flexShrink: 0, pt: 0.25 }}
                    >
                      {cell.name}
                    </Typography>

                    {/* Value */}
                    <Box sx={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
                      {isImage ? (
                        <Box
                          component="img"
                          src={cell.value}
                          alt={cell.name}
                          sx={{
                            maxWidth: "100%",
                            maxHeight: 80,
                            borderRadius: "4px",
                            objectFit: "contain",
                          }}
                          onError={(e) => {
                            e.target.style.display = "none";
                          }}
                        />
                      ) : isAudio ? (
                        <Box
                          component="audio"
                          controls
                          src={cell.value}
                          sx={{ width: "100%", height: 28 }}
                        />
                      ) : isJson ? (
                        <JsonValueTree
                          value={cell.value}
                          expanded={expandedCols[cell.id]}
                          onToggle={() =>
                            setExpandedCols((prev) => ({
                              ...prev,
                              [cell.id]: !prev[cell.id],
                            }))
                          }
                        />
                      ) : (
                        <Typography
                          variant="caption"
                          color="primary.main"
                          sx={{
                            fontSize: "12px",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                            wordBreak: "break-all",
                            cursor: "pointer",
                          }}
                          onClick={() =>
                            setExpandedCols((prev) => ({
                              ...prev,
                              [cell.id]: !prev[cell.id],
                            }))
                          }
                          title={cell.value}
                        >
                          {expandedCols[cell.id]
                            ? cell.value
                            : cell.value
                              ? `"${cell.value}"`
                              : "—"}
                        </Typography>
                      )}
                    </Box>
                  </Box>
                );
              })}

              {filteredCells.length === 0 && (
                <Typography
                  variant="caption"
                  color="text.disabled"
                  sx={{ py: 2, textAlign: "center", display: "block" }}
                >
                  No columns match your search
                </Typography>
              )}
            </Box>
          </Box>
        )}

        {/* Variable mapping — always visible when variables exist */}
        {variables.length > 0 && (
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                mb: 0.5,
              }}
            >
              <Typography
                variant="caption"
                color="text.secondary"
                fontWeight={600}
              >
                Variable Mapping
                <Box component="span" sx={{ color: "error.main", ml: 0.25 }}>
                  *
                </Box>
              </Typography>
              {(() => {
                const unmapped = variables.filter((v) => !mapping[v]).length;
                if (unmapped === 0) return null;
                return (
                  <Chip
                    label={`${unmapped} unmapped`}
                    size="small"
                    color="warning"
                    variant="outlined"
                    sx={{ fontSize: "11px", height: 20 }}
                  />
                );
              })()}
            </Box>
            {variables.map((variable) => {
              const isUnmapped = !mapping[variable];
              return (
                <Box
                  key={variable}
                  sx={{ display: "flex", alignItems: "center", gap: 1 }}
                >
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 0.75,
                      px: 1.5,
                      py: 0.5,
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: "6px",
                      minWidth: 120,
                    }}
                  >
                    <Iconify
                      icon="mdi:code-braces"
                      width={14}
                      sx={{ color: "text.secondary" }}
                    />
                    <Typography variant="caption" fontWeight={500}>
                      {variable}
                    </Typography>
                  </Box>
                  <Iconify
                    icon="mdi:arrow-right"
                    width={14}
                    sx={{ color: "text.disabled" }}
                  />
                  <ColumnTreeSelect
                    columnNames={columnNames}
                    value={mapping[variable] || ""}
                    onChange={(val) =>
                      setMapping((prev) => ({
                        ...prev,
                        [variable]: val || "",
                      }))
                    }
                    isUnmapped={isUnmapped}
                  />
                </Box>
              );
            })}
          </Box>
        )}

        {/* Loading indicator during eval */}
        {isRunning && (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, py: 1 }}>
            <Box sx={{ display: "flex", gap: 0.3 }}>
              {[0, 1, 2].map((i) => (
                <Box
                  key={i}
                  sx={{
                    width: 4,
                    height: 4,
                    borderRadius: "50%",
                    backgroundColor: "primary.main",
                    animation: "pulse 1.2s ease-in-out infinite",
                    animationDelay: `${i * 0.2}s`,
                    "@keyframes pulse": {
                      "0%, 100%": { opacity: 0.3 },
                      "50%": { opacity: 1 },
                    },
                  }}
                />
              ))}
            </Box>
            <Typography variant="caption" color="text.secondary">
              Evaluating...
            </Typography>
          </Box>
        )}

        {/* Result */}
        {result && !isRunning && (
          <EvalResultDisplay
            result={{
              ...result,
              ...(errorLocalizerState.status
                ? { error_localizer_status: errorLocalizerState.status }
                : {}),
              ...(errorLocalizerState.details
                ? {
                    error_details:
                      errorLocalizerState.details.error_analysis ||
                      errorLocalizerState.details,
                    selected_input_key:
                      errorLocalizerState.details.selected_input_key,
                    input_data: errorLocalizerState.details.input_data,
                    input_types: errorLocalizerState.details.input_types,
                  }
                : {}),
            }}
          />
        )}

        {/* Error */}
        {error && !isRunning && (
          <Box
            sx={{
              p: 1.5,
              borderRadius: "6px",
              border: "1px solid",
              borderColor: "error.main",
              backgroundColor: "error.lighter",
            }}
          >
            <Typography variant="caption" color="error.main">
              {typeof error === "string" ? error : JSON.stringify(error)}
            </Typography>
          </Box>
        )}
      </Box>
    );
  },
);

DatasetTestMode.displayName = "DatasetTestMode";

DatasetTestMode.propTypes = {
  templateId: PropTypes.string,
  variables: PropTypes.array,
  codeParams: PropTypes.object,
  onTestResult: PropTypes.func,
  onColumnsLoaded: PropTypes.func,
  initialDatasetId: PropTypes.string,
  onReadyChange: PropTypes.func,
  onClearResult: PropTypes.func,
  initialMapping: PropTypes.object,
  sourceColumns: PropTypes.array,
  extraColumns: PropTypes.array,
  isComposite: PropTypes.bool,
  compositeAdhocConfig: PropTypes.object,
};

export default DatasetTestMode;
