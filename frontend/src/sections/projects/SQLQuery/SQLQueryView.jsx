import React, { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import Editor from "@monaco-editor/react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  LinearProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Tab,
  Tabs,
  List,
  ListItemButton,
  ListItemText,
  MenuItem,
  Stack,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { useDashboardList } from "src/hooks/useDashboards";
import { useAuthContext } from "src/auth/hooks";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import { errorMessage, sqlApi } from "./api";
import useSQLExecution from "./useSQLExecution";
import QueryResults from "./QueryResults";
import QueryEstimate from "./QueryEstimate";
import QueryExamples from "./QueryExamples";
import QueryCatalog from "./QueryCatalog";
import { getSQLCompletions } from "./sqlCompletions";
import {
  describeQueryWindow,
  getEditorHeight,
  getQueryPresentation,
  isPreviousQuery,
  SQL_EDITOR_LINE_HEIGHT,
  SQL_EDITOR_PADDING,
} from "./queryPresentation";

const INITIAL_SQL =
  "SELECT model, count() AS spans, sum(total_tokens) AS tokens\nFROM spans\nGROUP BY model\nORDER BY spans DESC";

export default function SQLQueryView() {
  const { observeId } = useParams();
  const { user } = useAuthContext();
  const [search] = useSearchParams();
  const theme = useTheme();
  const editor = useRef(null);
  const completion = useRef(null);
  const catalogRef = useRef(null);
  const runAction = useRef(null);
  const runRef = useRef(null);
  const [catalog, setCatalog] = useState(null);
  const [libraryTab, setLibraryTab] = useState("catalog");
  const [librarySearch, setLibrarySearch] = useState("");
  const [libraryOpen, setLibraryOpen] = useState(false);
  const shortcut = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl";
  catalogRef.current = catalog;
  useEffect(
    () => () => {
      completion.current?.dispose();
      runAction.current?.dispose();
      editor.current = null;
    },
    [],
  );
  const [sql, setSQL] = useState(INITIAL_SQL);
  const [hours, setHours] = useState(24);
  const [submitted, setSubmitted] = useState(null);
  const [saved, setSaved] = useState([]);
  const [offset, setOffset] = useState(0);
  const [more, setMore] = useState(false);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");
  const [dialog, setDialog] = useState("");
  const [name, setName] = useState("");
  const [visibility, setVisibility] = useState("private");
  const [dashboardId, setDashboardId] = useState("");
  const [saving, setSaving] = useState(false);
  const [chart, setChart] = useState({ type: "table" });
  const [history, setHistory] = useState([]);
  const { data: dashboards = [] } = useDashboardList();
  const {
    job,
    busy,
    error: queryError,
    run,
    cancel,
  } = useSQLExecution(observeId);
  const loadSaved = useCallback(
    async (page = 0) => {
      const result = await sqlApi.saved(observeId, page);
      setSaved(result.items);
      setMore(result.has_more);
      setOffset(page);
    },
    [observeId],
  );
  useEffect(() => {
    let current = true;
    setSaved([]);
    setCatalog(null);
    setSelected(null);
    setSQL(INITIAL_SQL);
    setError("");
    setHistory([]);
    setLibrarySearch("");
    setLibraryTab("catalog");
    Promise.all([sqlApi.catalog(observeId), sqlApi.saved(observeId)])
      .then(([schema, queries]) => {
        if (current) {
          setCatalog(schema);
          setSaved(queries.items);
          setMore(queries.has_more);
          setOffset(0);
        }
      })
      .catch((e) => current && setError(errorMessage(e)));
    const id = search.get("saved");
    if (id)
      sqlApi
        .getSaved(observeId, id)
        .then((value) => {
          if (current) {
            setSelected(value);
            setSQL(value.sql);
            setHours(value.window_hours);
            setChart(value.chart);
          }
        })
        .catch((e) => current && setError(errorMessage(e)));
    return () => {
      current = false;
    };
  }, [observeId, search]);
  const execute = () => {
    // run clears the previous job synchronously and ignores superseded replies.
    // Capture the project/window here; the returned job supplies the SQL that ran.
    setSubmitted({ sql, projectId: observeId, hours });
    const end = new Date();
    const window = {
      start: new Date(end.getTime() - hours * 3600000).toISOString(),
      end: end.toISOString(),
    };
    if (selected && selected.sql === sql) {
      // Reauthorize the stored definition and bypass ad-hoc result reuse.
      run({ saved_query_id: selected.id, ...window }, true);
    } else {
      // Edits are an ad-hoc query, never silently execute the stored SQL.
      run({ sql, ...window });
    }
  };
  runRef.current = () => {
    if (!busy && catalog && sql.trim()) execute();
  };
  // An example replaces the editor text and starts a new, unsaved query, so
  // saving it never overwrites the saved query that was open. Undo restores.
  const applyExample = (example) => {
    const previous = { sql, selected, chart };
    setSQL(example.sql);
    setSelected(null);
    setChart({ type: "table" });
    enqueueSnackbar(`Inserted example: ${example.title}`, {
      action: (key) => (
        <Button
          size="small"
          color="inherit"
          onClick={() => {
            setSQL(previous.sql);
            setSelected(previous.selected);
            setChart(previous.chart);
            closeSnackbar(key);
          }}
        >
          Undo
        </Button>
      ),
    });
  };
  const insert = (text) => {
    if (!editor.current) return;
    editor.current.pushUndoStop();
    editor.current.executeEdits("catalog", [
      { range: editor.current.getSelection(), text },
    ]);
    editor.current.pushUndoStop();
    editor.current.focus();
    setLibraryOpen(false);
  };
  const mountEditor = (instance, monaco) => {
    editor.current = instance;
    completion.current?.dispose();
    runAction.current?.dispose();
    runAction.current = instance.addAction({
      id: "sql-workbench-run",
      label: "Run query",
      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter],
      run: () => runRef.current?.(),
    });
    completion.current = monaco.languages.registerCompletionItemProvider(
      "sql",
      {
        triggerCharacters: [".", "`"],
        provideCompletionItems: (model, position) => {
          // Language providers are global; do not leak this project's catalog
          // into another SQL editor mounted on the same page.
          if (model !== instance.getModel()) return { suggestions: [] };
          const result = getSQLCompletions(
            model.getValue(),
            model.getOffsetAt(position),
            catalogRef.current?.datasets,
          );
          const start = model.getPositionAt(result.from);
          const end = model.getPositionAt(result.to);
          return {
            suggestions: result.suggestions.map((item) => ({
              ...item,
              kind: monaco.languages.CompletionItemKind[item.kind],
              range: {
                startLineNumber: start.lineNumber,
                endLineNumber: end.lineNumber,
                startColumn: start.column,
                endColumn: end.column,
              },
            })),
          };
        },
      },
    );
  };
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const persist =
        selected && selected.created_by_id === user?.id
          ? (data) => sqlApi.update(selected.id, data)
          : sqlApi.save;
      const value = await persist({
        project_id: observeId,
        name,
        sql,
        visibility,
        chart,
        window_hours: hours,
      });
      setSelected(value);
      await loadSaved();
      setDialog("");
      enqueueSnackbar("Query saved", { variant: "success" });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };
  const download = async (format) => {
    try {
      const blob = await sqlApi.download(submitted.projectId, job.id, format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `query-results.${format}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(errorMessage(e));
    }
  };
  const addWidget = async () => {
    setSaving(true);
    try {
      await sqlApi.addWidget(observeId, selected.id, dashboardId);
      setDialog("");
      enqueueSnackbar("Query added to dashboard", { variant: "success" });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };
  return (
    <Box sx={{ p: { xs: 2, md: 2.5 }, minWidth: 0, maxWidth: "100%" }}>
      <Stack spacing={1.5}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
          gap={1}
        >
          <Stack
            direction="row"
            alignItems="baseline"
            gap={1.5}
            flexWrap="wrap"
            useFlexGap
          >
            <Typography variant="h6" component="h1">
              Query
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Read-only ClickHouse SQL · Project scoped
            </Typography>
          </Stack>
          <Typography variant="caption" color="text.secondary">
            30 s · 10,000 rows · 8 MB
          </Typography>
        </Stack>
        {catalog?.identity_semantics && (
          <Box
            sx={{
              px: 1.5,
              py: 0.75,
              borderLeft: 2,
              borderColor: "divider",
              bgcolor: "background.neutral",
            }}
          >
            <Typography variant="caption" color="text.secondary">
              {catalog.identity_semantics}
            </Typography>
          </Box>
        )}
        {(error || queryError) && (
          <Alert severity="error" onClose={() => setError("")}>
            {error || queryError}
          </Alert>
        )}
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "minmax(0, 1fr)",
              md: "300px minmax(0, 1fr)",
              xl: "320px minmax(0, 1fr)",
            },
            bgcolor: "background.paper",
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
          }}
        >
          <Box
            component="aside"
            aria-label="Query library"
            sx={{
              minWidth: 0,
              borderRight: { md: 1 },
              borderBottom: { xs: 1, md: 0 },
              borderColor: "divider",
              bgcolor: "background.neutral",
            }}
          >
            <Tabs
              value={libraryTab}
              onChange={(_, value) => {
                setLibraryTab(value);
                setLibrarySearch("");
                setLibraryOpen(true);
              }}
              variant="fullWidth"
              aria-label="Query library views"
              sx={{
                px: 1,
                minHeight: 48,
                borderBottom: 1,
                borderColor: "divider",
                "& .MuiTabs-flexContainer": { gap: 0 },
                "& .MuiTab-root": { minWidth: 0, px: 1, fontSize: 13 },
                "& .MuiTab-root:not(:last-of-type)": { mr: 0 },
                "& .MuiTabs-indicator": {
                  bgcolor: (t) =>
                    t.palette.accent?.brand || t.palette.primary.main,
                },
              }}
            >
              {[
                ["catalog", "Catalog"],
                ["examples", "Examples"],
                ["saved", "Saved"],
              ].map(([value, label]) => (
                <Tab
                  key={value}
                  value={value}
                  label={label}
                  id={`query-library-${value}`}
                  aria-controls={`query-panel-${value}`}
                  sx={{
                    // Production disables ripples. Inset focus stays visible
                    // inside the scroller, independently of tab selection.
                    "&.Mui-focusVisible": {
                      outline: "2px solid",
                      outlineColor: "text.primary",
                      outlineOffset: "-3px",
                    },
                  }}
                />
              ))}
            </Tabs>
            <Button
              size="small"
              onClick={() => setLibraryOpen((value) => !value)}
              aria-expanded={libraryOpen}
              aria-controls="query-library-content"
              sx={{ display: { xs: "flex", md: "none" }, mx: 1, my: 0.5 }}
            >
              {libraryOpen
                ? "Hide library"
                : "Browse datasets, examples & saved queries"}
            </Button>
            <Box
              id="query-library-content"
              sx={{
                display: { xs: libraryOpen ? "block" : "none", md: "block" },
              }}
            >
              <Box sx={{ p: 1.5 }}>
                <TextField
                  fullWidth
                  size="small"
                  type="search"
                  label={
                    libraryTab === "catalog"
                      ? "Search catalog"
                      : libraryTab === "examples"
                        ? "Search examples"
                        : "Search saved queries"
                  }
                  placeholder={
                    libraryTab === "catalog"
                      ? "Dataset, column or type…"
                      : libraryTab === "saved"
                        ? "Name or SQL on this page…"
                        : "Question, dataset or SQL…"
                  }
                  value={librarySearch}
                  onChange={(event) => setLibrarySearch(event.target.value)}
                  inputProps={{ "aria-controls": `query-panel-${libraryTab}` }}
                />
              </Box>
              <Box
                sx={{
                  maxHeight: { xs: 280, md: "min(65vh, 680px)" },
                  overflowY: "auto",
                  overflowX: "hidden",
                  overscrollBehavior: "contain",
                }}
              >
                <Box
                  role="tabpanel"
                  id="query-panel-catalog"
                  aria-labelledby="query-library-catalog"
                  hidden={libraryTab !== "catalog"}
                >
                  {!catalog && !error && (
                    <CircularProgress
                      size={20}
                      aria-label="Loading catalog"
                      sx={{ m: 2 }}
                    />
                  )}
                  {!catalog && error && (
                    <Typography variant="body2" sx={{ p: 2 }}>
                      Catalog unavailable. Reload this page to try again.
                    </Typography>
                  )}
                  {catalog && (
                    <QueryCatalog
                      datasets={catalog.datasets}
                      search={librarySearch}
                      onInsert={insert}
                    />
                  )}
                  {catalog && (
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ display: "block", p: 2 }}
                    >
                      Project catalog · v{catalog.version || 1}
                    </Typography>
                  )}
                </Box>
                <Box
                  role="tabpanel"
                  id="query-panel-examples"
                  aria-labelledby="query-library-examples"
                  hidden={libraryTab !== "examples"}
                >
                  <QueryExamples
                    examples={catalog?.examples}
                    datasets={catalog?.datasets}
                    onUse={(example) => {
                      applyExample(example);
                      editor.current?.focus();
                      setLibraryOpen(false);
                    }}
                    search={librarySearch}
                  />
                  {!catalog?.examples?.length && (
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ p: 2 }}
                    >
                      No examples available for this catalog. Start with a
                      dataset in Catalog.
                    </Typography>
                  )}
                </Box>
                <Box
                  role="tabpanel"
                  id="query-panel-saved"
                  aria-labelledby="query-library-saved"
                  hidden={libraryTab !== "saved"}
                >
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block", px: 2, pb: 1 }}
                  >
                    Saved queries · Search this page
                  </Typography>
                  <List dense disablePadding aria-label="Saved queries">
                    {saved
                      .filter((query) =>
                        `${query.name} ${query.sql}`
                          .toLowerCase()
                          .includes(librarySearch.trim().toLowerCase()),
                      )
                      .map((query) => (
                        <ListItemButton
                          key={query.id}
                          selected={selected?.id === query.id}
                          onClick={() => {
                            setSelected(query);
                            setSQL(query.sql);
                            setHours(query.window_hours);
                            setChart(query.chart);
                            editor.current?.focus();
                            setLibraryOpen(false);
                          }}
                          sx={{
                            px: 2,
                            py: 1,
                            borderTop: 1,
                            borderColor: "divider",
                          }}
                        >
                          <ListItemText
                            primary={query.name}
                            secondary={
                              query.visibility === "project"
                                ? "Shared with project"
                                : "Private"
                            }
                            primaryTypographyProps={{
                              fontSize: 13,
                              fontWeight: 600,
                              sx: { overflowWrap: "anywhere" },
                            }}
                            secondaryTypographyProps={{ fontSize: 12 }}
                          />
                        </ListItemButton>
                      ))}
                  </List>
                  {!saved.length && (
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ display: "block", p: 2 }}
                    >
                      Save a query to revisit it or share it with project
                      members.
                    </Typography>
                  )}
                  {saved.length > 0 &&
                    !saved.some((query) =>
                      `${query.name} ${query.sql}`
                        .toLowerCase()
                        .includes(librarySearch.trim().toLowerCase()),
                    ) && (
                      <Typography role="status" variant="body2" sx={{ p: 2 }}>
                        No matching saved queries on this page.
                      </Typography>
                    )}
                  <Stack
                    direction="row"
                    alignItems="center"
                    justifyContent="space-between"
                    sx={{ p: 1 }}
                  >
                    <Button
                      size="small"
                      disabled={!offset}
                      onClick={() =>
                        loadSaved(Math.max(0, offset - 20)).catch((e) =>
                          setError(errorMessage(e)),
                        )
                      }
                    >
                      Previous
                    </Button>
                    <Typography variant="caption" color="text.secondary">
                      Page {offset / 20 + 1}
                    </Typography>
                    <Button
                      size="small"
                      disabled={!more}
                      onClick={() =>
                        loadSaved(offset + 20).catch((e) =>
                          setError(errorMessage(e)),
                        )
                      }
                    >
                      Next
                    </Button>
                  </Stack>
                </Box>
              </Box>
            </Box>
          </Box>
          <Stack sx={{ minWidth: 0 }}>
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              gap={1}
              sx={{
                px: 1.5,
                py: 0.75,
                bgcolor: "background.neutral",
                borderBottom: 1,
                borderColor: "divider",
              }}
            >
              <Typography
                variant="caption"
                sx={{ fontWeight: 600, overflowWrap: "anywhere" }}
              >
                {selected?.name || "Unsaved query"}
                {selected && selected.sql !== sql ? " · Edited" : ""}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ flexShrink: 0 }}
              >
                SQL
              </Typography>
            </Stack>
            <Stack
              direction="row"
              gap={1}
              flexWrap="wrap"
              useFlexGap
              alignItems="center"
              sx={{ p: 1.5, borderBottom: 1, borderColor: "divider" }}
            >
              <TextField
                select
                size="small"
                label="Time window"
                value={hours}
                onChange={(e) => setHours(Number(e.target.value))}
                sx={{ width: { xs: "100%", sm: 150 }, flexShrink: 0 }}
              >
                {[1, 24, 168, 744].map((value) => (
                  <MenuItem key={value} value={value}>
                    {value === 1
                      ? "Last hour"
                      : describeQueryWindow(value).replace(/^last/, "Last")}
                  </MenuItem>
                ))}
              </TextField>
              <Button
                variant="contained"
                onClick={execute}
                title={`Run query (${shortcut}+Enter)`}
                aria-keyshortcuts={
                  shortcut === "⌘" ? "Meta+Enter" : "Control+Enter"
                }
                disabled={busy || !catalog || !sql.trim()}
              >
                Run query
              </Button>
              <Button onClick={cancel} disabled={!busy || !job}>
                Cancel query
              </Button>
              <Box sx={{ flex: 1 }} />
              <Button
                disabled={!catalog?.can_save || !sql.trim()}
                onClick={() => {
                  setName(selected?.name || "");
                  setVisibility(selected?.visibility || "private");
                  setDialog("save");
                }}
              >
                Save query
              </Button>
            </Stack>
            <Editor
              key={observeId}
              height={getEditorHeight(sql.split("\n").length)}
              language="sql"
              value={sql}
              onChange={(value) => setSQL(value || "")}
              theme={theme.palette.mode === "dark" ? "vs-dark" : "light"}
              onMount={mountEditor}
              options={{
                minimap: { enabled: false },
                fontSize: 14,
                lineHeight: SQL_EDITOR_LINE_HEIGHT,
                padding: {
                  top: SQL_EDITOR_PADDING,
                  bottom: SQL_EDITOR_PADDING,
                },
                lineNumbersMinChars: 3,
                fontFamily: "monospace",
                // The existing loader uses Monaco 0.43's boolean option.
                wordBasedSuggestions: false,
                quickSuggestions: {
                  other: true,
                  comments: false,
                  strings: false,
                },
                scrollBeyondLastLine: false,
                wordWrap: "on",
                ariaLabel: "SQL query editor",
                automaticLayout: true,
                tabSize: 2,
              }}
            />
            <Stack
              role="group"
              aria-label="Query editor hints"
              direction="row"
              alignItems="baseline"
              flexWrap="wrap"
              useFlexGap
              columnGap={1.5}
              sx={{ px: 1.5, py: 0.25, borderTop: 1, borderColor: "divider" }}
            >
              <Stack direction="row" flexWrap="wrap" useFlexGap gap={1.5}>
                <Typography variant="caption" color="text.secondary">
                  {shortcut}+Enter to run
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Ctrl+Space for suggestions
                </Typography>
              </Stack>
              <QueryEstimate
                projectId={observeId}
                sql={sql}
                hours={hours}
                enabled={Boolean(catalog)}
              />
            </Stack>
            <Stack
              role="group"
              aria-label="Results toolbar"
              direction="row"
              gap={1}
              flexWrap="nowrap"
              alignItems="center"
              sx={{
                px: 1.5,
                py: 0.125,
                borderTop: 1,
                borderBottom: 1,
                borderColor: "divider",
                bgcolor: "background.neutral",
              }}
            >
              <Stack
                role="group"
                aria-label="Result summary"
                justifyContent="center"
                // Reserve the 22px title + 18px provenance in both states.
                sx={{ flex: 1, minHeight: 40 }}
              >
                <Stack direction="row" gap={1} alignItems="baseline">
                  <Typography variant="subtitle2" component="h2">
                    Results
                  </Typography>
                  <Typography
                    variant="caption"
                    role="status"
                    color="text.secondary"
                  >
                    {busy
                      ? `Query ${job?.state || "submitting"}…`
                      : job?.state || "Ready"}
                  </Typography>
                </Stack>
                {job &&
                  isPreviousQuery(
                    submitted && {
                      ...submitted,
                      // Saved definitions may change after this editor opens.
                      sql: job.sql ?? submitted.sql,
                    },
                    { sql, projectId: observeId, hours },
                  ) && (
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      aria-label={`Previous query results · ${describeQueryWindow(submitted.hours)} — Run to update`}
                      title={`Previous query results · ${describeQueryWindow(submitted.hours)} — Run to update`}
                      sx={{ whiteSpace: "nowrap" }}
                    >
                      Previous · {describeQueryWindow(submitted.hours)} — Run to
                      update
                    </Typography>
                  )}
              </Stack>
              <Stack
                role="group"
                aria-label="Result actions"
                direction="row"
                gap={1}
                flexWrap="nowrap"
                sx={{
                  flexShrink: 0,
                  "& .MuiButton-root": { whiteSpace: "nowrap" },
                }}
              >
                <Button
                  size="small"
                  disabled={job?.state !== "succeeded" || job.expired}
                  onClick={() => download("csv")}
                >
                  CSV
                </Button>
                <Button
                  size="small"
                  disabled={job?.state !== "succeeded" || job.expired}
                  onClick={() => download("parquet")}
                >
                  Parquet
                </Button>
                <Button
                  size="small"
                  disabled={
                    !catalog?.can_save ||
                    selected?.visibility !== "project" ||
                    selected?.sql !== sql
                  }
                  onClick={() => setDialog("dashboard")}
                >
                  Add to dashboard
                </Button>
                <Button
                  size="small"
                  onClick={async () => {
                    try {
                      const value = await sqlApi.history(observeId);
                      setHistory(value.items);
                      setDialog("history");
                    } catch (e) {
                      setError(errorMessage(e));
                    }
                  }}
                >
                  History
                </Button>
              </Stack>
            </Stack>
            {busy && <LinearProgress aria-label="Query in progress" />}
            <Box
              sx={{ p: { xs: 1.5, md: 2 }, minHeight: 180, minWidth: 0 }}
              aria-busy={busy}
            >
              {job?.error ? (
                <Alert severity="error">{job.error}</Alert>
              ) : job?.expired ? (
                <Alert severity="info">
                  These results expired. Run the query again.
                </Alert>
              ) : (
                <QueryResults
                  key={job?.id || "empty"}
                  result={job?.result}
                  presentation={getQueryPresentation({
                    state: job?.state,
                    busy,
                  })}
                  showEditorGuidance={!job && !queryError}
                  chart={chart}
                  onChartChange={setChart}
                />
              )}
            </Box>
            {selected &&
              selected.created_by_id === user?.id &&
              catalog?.can_save && (
                <Button
                  color="error"
                  size="small"
                  onClick={() => setDialog("delete")}
                  sx={{ alignSelf: "flex-start", ml: 2 }}
                >
                  Delete saved query
                </Button>
              )}
            {selected && (
              <Stack direction="row" gap={1} sx={{ px: 2, pb: 2 }}>
                <Typography variant="caption" color="text.secondary">
                  {selected.name} ·{" "}
                  {selected.visibility === "project"
                    ? "Project members only. Each person runs with their current access."
                    : "Only you can open this saved query."}
                </Typography>
                {selected.visibility === "project" && (
                  <Button
                    size="small"
                    onClick={() =>
                      navigator.clipboard
                        .writeText(
                          `${window.location.origin}${window.location.pathname}?saved=${selected.id}`,
                        )
                        .then(() => enqueueSnackbar("Query link copied"))
                        .catch(() =>
                          setError(
                            "Clipboard unavailable. Copy the saved-query URL from your browser.",
                          ),
                        )
                    }
                  >
                    Copy query link
                  </Button>
                )}
              </Stack>
            )}
          </Stack>
        </Box>
      </Stack>
      <Dialog
        open={dialog === "save"}
        onClose={() => !saving && setDialog("")}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>Save query</DialogTitle>
        <DialogContent>
          {error && <Alert severity="error">{error}</Alert>}
          <Stack spacing={2} sx={{ pt: 1 }}>
            <TextField
              autoFocus
              label="Query name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              inputProps={{ maxLength: 160 }}
            />
            <TextField
              select
              label="Visibility"
              value={visibility}
              onChange={(e) => setVisibility(e.target.value)}
            >
              <MenuItem value="private">Private</MenuItem>
              <MenuItem value="project">Project members</MenuItem>
            </TextField>
            <Typography variant="caption">
              A shared query is not a public link. Results stay private to the
              person who runs it.
            </Typography>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button disabled={saving} onClick={() => setDialog("")}>
            Back
          </Button>
          <Button disabled={!name.trim() || saving} onClick={save}>
            Save
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog
        open={dialog === "dashboard"}
        onClose={() => !saving && setDialog("")}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>Add query to dashboard</DialogTitle>
        <DialogContent>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            select
            fullWidth
            label="Dashboard"
            value={dashboardId}
            onChange={(e) => setDashboardId(e.target.value)}
            sx={{ mt: 1 }}
          >
            {dashboards.map((d) => (
              <MenuItem key={d.id} value={d.id}>
                {d.name}
              </MenuItem>
            ))}
          </TextField>
          <Typography variant="caption">
            The widget reruns the saved query with each viewer&apos;s project
            permissions.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog("")}>Back</Button>
          <Button disabled={!dashboardId || saving} onClick={addWidget}>
            Add query
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog
        open={dialog === "delete"}
        onClose={() => !saving && setDialog("")}
      >
        <DialogTitle>Delete saved query?</DialogTitle>
        <DialogContent>
          {error && <Alert severity="error">{error}</Alert>}
          Dashboard widgets using this query will stop running. Existing
          personal results keep their normal expiry.
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog("")}>Keep query</Button>
          <Button
            color="error"
            disabled={saving}
            onClick={async () => {
              setSaving(true);
              setError("");
              try {
                await sqlApi.remove(observeId, selected.id);
                setSelected(null);
                await loadSaved();
                setDialog("");
              } catch (e) {
                setError(errorMessage(e));
              } finally {
                setSaving(false);
              }
            }}
          >
            Delete query
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog
        open={dialog === "history"}
        onClose={() => setDialog("")}
        fullWidth
      >
        <DialogTitle>Your recent queries</DialogTitle>
        <DialogContent>
          <List>
            {history.map((item) => (
              <ListItemButton
                key={item.id}
                onClick={() => {
                  setSQL(item.sql);
                  setDialog("");
                }}
              >
                <ListItemText
                  primary={item.sql.slice(0, 150)}
                  secondary={`${item.state} · ${new Date(item.created_at).toLocaleString()}`}
                />
              </ListItemButton>
            ))}
          </List>
          {!history.length && <Typography>No queries yet.</Typography>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog("")}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
