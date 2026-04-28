/* eslint-disable react/prop-types */
import {
  Autocomplete,
  Box,
  Chip,
  CircularProgress,
  IconButton,
  InputAdornment,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
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
import useErrorLocalizerPoll from "../hooks/useErrorLocalizerPoll";

const DATASET_PAGE_SIZE = 25;

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
    : Object.entries(data);

  return (
    <Box sx={{ display: "flex", flexDirection: "column" }}>
      {entries.map(([key, val]) => {
        const isObj = val !== null && typeof val === "object";
        return (
          <JsonEntryRow
            key={key}
            entryKey={key}
            entryValue={val}
            isObject={isObj}
            depth={depth}
          />
        );
      })}
    </Box>
  );
}

function JsonEntryRow({ entryKey, entryValue, isObject, depth }) {
  const [open, setOpen] = useState(false);

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
        onClick={() => isObject && setOpen(!open)}
      >
        {isObject && (
          <Iconify
            icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
            width={12}
            sx={{ color: "text.disabled", mt: 0.25, flexShrink: 0 }}
          />
        )}
        {!isObject && <Box sx={{ width: 12, flexShrink: 0 }} />}
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
            sx={{
              fontSize: "11px",
              color: "primary.main",
              wordBreak: "break-all",
              overflow: "hidden",
              textOverflow: "ellipsis",
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
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
          const value = cell?.cell_value ?? cell ?? "";
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

    // Column names for variable mapping dropdown
    // When sourceColumns is provided (e.g. workbench mode), use those instead
    // of dataset columns. sourceColumns may be objects with headerName/field or strings.
    const columnNames = useMemo(() => {
      if (isWorkbenchMode) {
        return sourceColumns.map((col) =>
          typeof col === "string"
            ? col
            : col.headerName || col.field || col.name || col.label || "",
        );
      }
      const base = columns
        .filter((c) => c.id && c.name && !["id", "orgId"].includes(c.name))
        .map((c) => c.name);

      // Expand JSON columns with nested sub-key paths (e.g. "EXPECTED OUTPUT.where_conditions")
      // by introspecting the first row's cell values — same recursive walk as TaskLivePreview.
      const ARRAY_PEEK = 500;
      const DICT_LIMIT = 5000;
      const walk = (node, prefix, keys) => {
        if (Array.isArray(node)) {
          node.slice(0, ARRAY_PEEK).forEach((item, idx) => {
            const path = prefix ? `${prefix}.${idx}` : String(idx);
            keys.push(path);
            if (item && typeof item === "object") {
              walk(item, path, keys);
            }
          });
          return;
        }
        for (const [k, v] of canonicalEntries(node)) {
          if (k.startsWith("_")) continue;
          const path = prefix ? `${prefix}.${k}` : k;
          keys.push(path);
          if (v && typeof v === "object") {
            if (Array.isArray(v) || Object.keys(v).length < DICT_LIMIT) {
              walk(v, path, keys);
            }
          }
        }
      };

      const expanded = [];
      base.forEach((colName) => {
        expanded.push(colName);
        const cell = rowCells.find((rc) => rc.name === colName);
        if (!cell) return;
        let parsed = cell.value;
        if (typeof parsed === "string") {
          const trimmed = parsed.trim();
          if (
            (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
            (trimmed.startsWith("[") && trimmed.endsWith("]"))
          ) {
            try {
              parsed = JSON.parse(trimmed);
            } catch {
              return;
            }
          } else {
            return;
          }
        }
        if (parsed && typeof parsed === "object") {
          const subKeys = [];
          walk(parsed, colName, subKeys);
          expanded.push(...subKeys);
        }
      });

      if (!extraColumns?.length) return expanded;
      // Virtual columns ("Output", "Prompt Chain" for experiments) render
      // first so they're prominent in the dropdown AND win auto-mapping
      // ties — auto-map iterates columnNames in order and takes the first
      // match, so real dataset columns that happen to collide with eval
      // variable names shouldn't shadow the experiment virtuals.
      const extras = (extraColumns || [])
        .map((col) =>
          typeof col === "string"
            ? col
            : col.headerName || col.field || col.name || col.label || "",
        )
        .filter(Boolean);
      const extraSet = new Set(extras);
      const baseWithoutExtras = expanded.filter((n) => !extraSet.has(n));
      return [...extras, ...baseWithoutExtras];
    }, [columns, sourceColumns, extraColumns, isWorkbenchMode, rowCells]);

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

    // Resolve UUID-based mapping values to column names (edit mode).
    // The saved mapping uses column UUIDs but the dropdown shows names.
    // Experiment virtual columns are also saved by field ("output",
    // "prompt_chain"); resolve those to their display names too.
    const uuidResolutionDone = React.useRef(false);
    useEffect(() => {
      if (!columns.length && !Object.keys(extraFieldToName).length) return;
      if (uuidResolutionDone.current) return;
      const idToName = {};
      columns.forEach((c) => {
        if (c.id && c.name) idToName[c.id] = c.name;
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
    }, [columns, extraFieldToName]); // eslint-disable-line react-hooks/exhaustive-deps

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
          // Build a valueMap of all dotted paths from row cells — same
          // walkValues + resolveMapping pattern as TaskLivePreview.
          const ARRAY_PEEK_R = 500;
          const DICT_LIMIT_R = 5000;
          const valueMap = {};
          const walkValues = (node, prefix) => {
            if (Array.isArray(node)) {
              node.slice(0, ARRAY_PEEK_R).forEach((item, idx) => {
                const path = prefix ? `${prefix}.${idx}` : String(idx);
                valueMap[path] = item;
                if (item && typeof item === "object") {
                  walkValues(item, path);
                }
              });
              return;
            }
            for (const [k, v] of canonicalEntries(node)) {
              if (k.startsWith("_")) continue;
              const path = prefix ? `${prefix}.${k}` : k;
              valueMap[path] = v;
              if (v && typeof v === "object") {
                if (Array.isArray(v) || Object.keys(v).length < DICT_LIMIT_R) {
                  walkValues(v, path);
                }
              }
            }
          };

          // Walk each column's cell value into the valueMap
          const colDataTypes = {};
          columns
            .filter(
              (c) => c.id && c.name && !["id", "orgId"].includes(c.name),
            )
            .forEach((col) => {
              const cell = currentRow[col.id];
              let cellValue = cell?.cell_value ?? cell ?? "";
              valueMap[col.name] = cellValue;
              colDataTypes[col.name] = col.data_type || "text";

              // Parse JSON strings and walk nested keys
              if (typeof cellValue === "string") {
                const trimmed = cellValue.trim();
                if (
                  (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
                  (trimmed.startsWith("[") && trimmed.endsWith("]"))
                ) {
                  try {
                    const parsed = JSON.parse(trimmed);
                    if (parsed && typeof parsed === "object") {
                      walkValues(parsed, col.name);
                    }
                  } catch {
                    // not valid JSON
                  }
                }
              } else if (cellValue && typeof cellValue === "object") {
                walkValues(cellValue, col.name);
              }
            });

          // Resolve mapping using the valueMap — same as TaskLivePreview
          for (const variable of variables) {
            const field = mapping[variable];
            if (!field) continue;
            const val = valueMap[field];
            if (val !== undefined && val !== null) {
              evalMapping[variable] =
                typeof val === "object" ? JSON.stringify(val) : String(val);

              // Find the root column for data type detection
              const rootCol = field.includes(".")
                ? field.slice(0, field.indexOf("."))
                : field;
              const dt = colDataTypes[rootCol] || "text";
              if (["image", "images"].includes(dt)) {
                inputDataTypes[variable] = "image";
              } else if (dt === "audio") {
                inputDataTypes[variable] = "audio";
              } else {
                inputDataTypes[variable] = "text";
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
                const val = cell?.cell_value ?? cell ?? "";
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
          ? await axios.post(endpoints.develop.eval.executeCompositeEval(tid), {
              mapping: evalMapping,
              error_localizer: errorLocalizerEnabled,
              input_data_types: inputDataTypes,
              row_context: rowContext,
            })
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
              },
              input_data_types: inputDataTypes,
              row_context: rowContext,
            });

        if (data?.status) {
          setResult(data.result);
          onTestResult?.(true, data.result);
          if (errorLocalizerEnabled && data.result?.log_id) {
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

    // Build a name→ID lookup so the save payload uses column UUIDs (which the
    // backend's eval_runner expects) while the test playground uses names.
    const nameToId = useMemo(() => {
      const map = {};
      columns.forEach((c) => {
        if (c.id && c.name) map[c.name] = c.id;
      });
      return map;
    }, [columns]);

    // Mapping with column IDs for the save payload
    const idMapping = useMemo(() => {
      const m = {};
      if (isWorkbenchMode) {
        Object.entries(mapping).forEach(([variable, colName]) => {
          m[variable] = sourceNameToField[colName] || colName;
        });
      } else {
        Object.entries(mapping).forEach(([variable, colName]) => {
          // Extras resolve to their own field (e.g. "Output" → "output") and
          // bypass the dataset UUID lookup since they don't exist in columns.
          m[variable] =
            extraNameToField[colName] || nameToId[colName] || colName;
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
                <Box
                  component="span"
                  sx={{ color: "error.main", ml: 0.25 }}
                >
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
                  <Autocomplete
                    size="small"
                    options={
                      mapping[variable] &&
                      !columnNames.includes(mapping[variable])
                        ? [mapping[variable], ...columnNames]
                        : columnNames
                    }
                    value={mapping[variable] || null}
                    onChange={(_, val) =>
                      setMapping((prev) => ({
                        ...prev,
                        [variable]: val || "",
                      }))
                    }
                    openOnFocus
                    autoHighlight
                    selectOnFocus
                    handleHomeEndKeys
                    isOptionEqualToValue={(opt, val) => opt === val}
                    sx={{
                      flex: 1,
                      ...(isUnmapped && {
                        "& .MuiOutlinedInput-notchedOutline": {
                          borderColor: "warning.main",
                        },
                      }),
                    }}
                    renderInput={(params) => (
                      <TextField
                        {...params}
                        placeholder="Select column (required)"
                        InputProps={{
                          ...params.InputProps,
                          sx: {
                            ...params.InputProps.sx,
                            fontSize: "12px",
                            fontFamily: "monospace",
                            height: 30,
                            py: 0,
                          },
                        }}
                      />
                    )}
                    renderOption={(props, col) => {
                      const { key, ...rest } = props;
                      return (
                        <Box
                          component="li"
                          key={key}
                          {...rest}
                          sx={{
                            ...rest.sx,
                            fontSize: "12px",
                            fontFamily: "monospace",
                          }}
                        >
                          {col}
                        </Box>
                      );
                    }}
                    ListboxProps={{ style: { maxHeight: 260 } }}
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
};

export default DatasetTestMode;
