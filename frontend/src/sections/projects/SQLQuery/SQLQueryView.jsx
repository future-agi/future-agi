import React, { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import Editor from "@monaco-editor/react";
import { alpha } from "@mui/material/styles";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  LinearProgress,
  Dialog,
  DialogContent,
  FormControl,
  FormControlLabel,
  FormLabel,
  IconButton,
  Radio,
  RadioGroup,
  Tab,
  Tabs,
  List,
  ListItemButton,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { useDashboardList } from "src/hooks/useDashboards";
import { useAuthContext } from "src/auth/hooks";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import { relativeTime } from "src/utils/format-time";
import { errorMessage, sqlApi } from "./api";
import useSQLExecution from "./useSQLExecution";
import QueryResults from "./QueryResults";
import QueryEstimate from "./QueryEstimate";
import QueryExamples from "./QueryExamples";
import QueryCatalog from "./QueryCatalog";
import { getSQLCompletions } from "./sqlCompletions";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import {
  ACTION_BUTTON_PROPS,
  ACTION_BUTTON_SX,
  CONTROL_HEIGHT,
  MetaLine,
  PRIMARY_BUTTON_PROPS,
  PRIMARY_BUTTON_SX,
  QUERY_TABS_SX,
  StatePill,
  TabLabel,
  visibilityIcon,
  visibilityLabel,
  warningText,
} from "./queryUi";
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
  const [downloadAnchor, setDownloadAnchor] = useState(null);
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
      // Land on the saved list so the new or updated query is in view.
      setLibraryTab("saved");
      setLibrarySearch("");
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
  // Saving an opened query you own replaces it, so the dialog says so.
  const updatingSaved = Boolean(
    selected && selected.created_by_id === user?.id,
  );
  const addToDashboardDisabled =
    !catalog?.can_save ||
    selected?.visibility !== "project" ||
    selected?.sql !== sql;
  return (
    <Box sx={{ p: 2, minWidth: 0, maxWidth: "100%" }}>
      <Stack spacing={1.5}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
          gap={1}
        >
          <Box sx={{ minWidth: 0 }}>
            <Typography
              component="h1"
              sx={{ typography: "m3", fontWeight: "fontWeightSemiBold" }}
            >
              Query
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Read-only ClickHouse SQL · Project scoped
            </Typography>
          </Box>
          <MetaLine
            role="group"
            aria-label="Query limits"
            icon="solar:info-circle-linear"
            title="Each run stops after 30 seconds and returns at most 10,000 rows or 8 MB of results."
            parts={["30 s timeout", "10,000-row limit", "8 MB result limit"]}
          />
        </Stack>
        {catalog?.identity_semantics && (
          <Stack
            direction="row"
            spacing={1}
            alignItems="flex-start"
            sx={{
              px: 1.5,
              py: 1,
              border: 1,
              borderColor: "divider",
              borderRadius: 1,
              bgcolor: "background.neutral",
            }}
          >
            <Iconify
              icon="solar:info-circle-linear"
              width={16}
              sx={{ color: "text.secondary", mt: "1px", flexShrink: 0 }}
            />
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {catalog.identity_semantics}
            </Typography>
          </Stack>
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
              bgcolor: "background.paper",
            }}
          >
            <Tabs
              value={libraryTab}
              onChange={(_, value) => {
                setLibraryTab(value);
                setLibrarySearch("");
                setLibraryOpen(true);
              }}
              // The three tabs share the narrow library column evenly.
              variant="fullWidth"
              aria-label="Query library views"
              sx={{
                ...QUERY_TABS_SX,
                px: 1,
                borderBottom: 1,
                borderColor: "divider",
              }}
            >
              {[
                ["catalog", "Catalog", catalog?.datasets?.length],
                ["examples", "Examples", catalog?.examples?.length],
                ["saved", "Saved", saved.length],
              ].map(([value, label, count]) => (
                <Tab
                  key={value}
                  value={value}
                  aria-label={label}
                  label={
                    <TabLabel
                      label={label}
                      count={value === "saved" && more ? `${count}+` : count}
                    />
                  }
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
              {...ACTION_BUTTON_PROPS}
              onClick={() => setLibraryOpen((value) => !value)}
              aria-expanded={libraryOpen}
              aria-controls="query-library-content"
              sx={{
                ...ACTION_BUTTON_SX,
                display: { xs: "flex", md: "none" },
                mx: 1.5,
                my: 1,
              }}
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
                {/* The platform search field (FormSearchField): icon, no
                    floating label; the label stays for assistive tech. */}
                <FormSearchField
                  fullWidth
                  type="search"
                  placeholder={
                    libraryTab === "catalog"
                      ? "Search datasets, columns, types"
                      : libraryTab === "saved"
                        ? "Search saved queries"
                        : "Search examples"
                  }
                  searchQuery={librarySearch}
                  onChange={(event) => setLibrarySearch(event.target.value)}
                  inputProps={{
                    "aria-label":
                      libraryTab === "catalog"
                        ? "Search catalog"
                        : libraryTab === "examples"
                          ? "Search examples"
                          : "Search saved queries",
                    "aria-controls": `query-panel-${libraryTab}`,
                  }}
                  sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
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
                    <Typography sx={{ p: 2, typography: "s2" }}>
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
                      sx={{
                        display: "block",
                        px: 2,
                        py: 1.5,
                        typography: "s3",
                        color: "text.disabled",
                      }}
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
                      sx={{ p: 2, typography: "s2", color: "text.secondary" }}
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
                    sx={{
                      display: "block",
                      px: 2,
                      pb: 1,
                      typography: "s3",
                      color: "text.secondary",
                    }}
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
                              <MetaLine
                                icon={visibilityIcon(query.visibility)}
                                parts={[
                                  visibilityLabel(query.visibility),
                                  query.window_hours &&
                                    describeQueryWindow(query.window_hours),
                                ]}
                              />
                            }
                            primaryTypographyProps={{
                              sx: {
                                typography: "s2",
                                fontWeight: "fontWeightSemiBold",
                                overflowWrap: "anywhere",
                                mb: 0.5,
                              },
                            }}
                            secondaryTypographyProps={{ component: "div" }}
                          />
                        </ListItemButton>
                      ))}
                  </List>
                  {!saved.length && (
                    <Typography
                      sx={{
                        display: "block",
                        p: 2,
                        typography: "s2",
                        color: "text.secondary",
                      }}
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
                      <Typography role="status" sx={{ p: 2, typography: "s2" }}>
                        No matching saved queries on this page.
                      </Typography>
                    )}
                  <Stack
                    direction="row"
                    alignItems="center"
                    justifyContent="space-between"
                    sx={{ px: 2, py: 1.5 }}
                  >
                    <Button
                      {...ACTION_BUTTON_PROPS}
                      sx={ACTION_BUTTON_SX}
                      startIcon={<Iconify icon="solar:alt-arrow-left-linear" />}
                      disabled={!offset}
                      onClick={() =>
                        loadSaved(Math.max(0, offset - 20)).catch((e) =>
                          setError(errorMessage(e)),
                        )
                      }
                    >
                      Previous
                    </Button>
                    <Typography
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      Page {offset / 20 + 1}
                    </Typography>
                    <Button
                      {...ACTION_BUTTON_PROPS}
                      sx={ACTION_BUTTON_SX}
                      endIcon={<Iconify icon="solar:alt-arrow-right-linear" />}
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
                py: 1,
                borderBottom: 1,
                borderColor: "divider",
              }}
            >
              <Stack
                direction="row"
                alignItems="center"
                gap={0.75}
                flexWrap="wrap"
                useFlexGap
                sx={{ minWidth: 0 }}
              >
                <Iconify
                  icon="solar:code-linear"
                  width={16}
                  sx={{ color: "text.secondary", flexShrink: 0 }}
                />
                <Typography
                  sx={{
                    typography: "s1",
                    fontWeight: "fontWeightSemiBold",
                    overflowWrap: "anywhere",
                  }}
                >
                  {selected?.name || "Unsaved query"}
                </Typography>
                {selected && selected.sql !== sql && (
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={0.5}
                    sx={{ color: warningText }}
                  >
                    <Iconify icon="solar:pen-linear" width={12} />
                    <Typography
                      component="span"
                      sx={{
                        typography: "s3",
                        fontWeight: "fontWeightSemiBold",
                        color: "inherit",
                      }}
                    >
                      Edited
                    </Typography>
                  </Stack>
                )}
                {selected && (
                  <MetaLine
                    icon={visibilityIcon(selected.visibility)}
                    parts={[visibilityLabel(selected.visibility)]}
                  />
                )}
              </Stack>
            </Stack>
            <Stack
              direction="row"
              gap={1}
              flexWrap="wrap"
              useFlexGap
              alignItems="center"
              sx={{
                px: 1.5,
                py: 1.25,
                borderBottom: 1,
                borderColor: "divider",
              }}
            >
              <TextField
                select
                size="small"
                value={hours}
                onChange={(e) => setHours(Number(e.target.value))}
                SelectProps={{
                  SelectDisplayProps: {
                    "aria-label": "Time window",
                    title: "Time window",
                  },
                }}
                InputProps={{
                  startAdornment: (
                    <Iconify
                      icon="solar:calendar-linear"
                      width={16}
                      sx={{ color: "text.secondary", mr: 1, flexShrink: 0 }}
                    />
                  ),
                  sx: { typography: "s2", height: CONTROL_HEIGHT },
                }}
                sx={{ width: { xs: "100%", sm: 160 }, flexShrink: 0 }}
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
                {...PRIMARY_BUTTON_PROPS}
                startIcon={<Iconify icon="solar:play-bold" />}
                sx={{ ...PRIMARY_BUTTON_SX, px: 1.75 }}
                onClick={execute}
                title={`Run query (${shortcut}+Enter)`}
                aria-keyshortcuts={
                  shortcut === "⌘" ? "Meta+Enter" : "Control+Enter"
                }
                disabled={busy || !catalog || !sql.trim()}
              >
                Run query
              </Button>
              <Button
                {...ACTION_BUTTON_PROPS}
                sx={ACTION_BUTTON_SX}
                startIcon={<Iconify icon="solar:stop-circle-linear" />}
                onClick={cancel}
                disabled={!busy || !job}
              >
                Cancel query
              </Button>
              <Box sx={{ flex: 1 }} />
              <Button
                {...ACTION_BUTTON_PROPS}
                sx={ACTION_BUTTON_SX}
                startIcon={<Iconify icon="solar:diskette-linear" />}
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
              sx={{ px: 1.5, py: 0.75, borderTop: 1, borderColor: "divider" }}
            >
              <Stack
                direction="row"
                flexWrap="wrap"
                useFlexGap
                gap={1.5}
                alignItems="center"
              >
                <Iconify
                  icon="solar:keyboard-linear"
                  width={14}
                  sx={{ color: "text.disabled" }}
                />
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {shortcut}+Enter to run
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
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
                py: 0.75,
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
                <Stack direction="row" gap={1} alignItems="center">
                  <Typography
                    component="h2"
                    sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}
                  >
                    Results
                  </Typography>
                  <StatePill
                    state={busy ? job?.state || "submitting" : job?.state}
                    role="status"
                  >
                    {busy
                      ? `Query ${job?.state || "submitting"}…`
                      : job?.state || "Ready"}
                  </StatePill>
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
                      aria-label={`Previous query results · ${describeQueryWindow(submitted.hours)} — Run to update`}
                      title={`Previous query results · ${describeQueryWindow(submitted.hours)} — Run to update`}
                      sx={{
                        whiteSpace: "nowrap",
                        typography: "s3",
                        color: warningText,
                        mt: 0.25,
                      }}
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
                  {...ACTION_BUTTON_PROPS}
                  sx={ACTION_BUTTON_SX}
                  startIcon={
                    <Iconify icon="solar:download-minimalistic-linear" />
                  }
                  endIcon={<Iconify icon="solar:alt-arrow-down-linear" />}
                  aria-haspopup="menu"
                  aria-expanded={Boolean(downloadAnchor)}
                  aria-controls={
                    downloadAnchor ? "query-download-menu" : undefined
                  }
                  disabled={job?.state !== "succeeded" || job.expired}
                  onClick={(event) => setDownloadAnchor(event.currentTarget)}
                >
                  Download
                </Button>
                <Menu
                  id="query-download-menu"
                  anchorEl={downloadAnchor}
                  open={Boolean(downloadAnchor)}
                  onClose={() => setDownloadAnchor(null)}
                  anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
                  transformOrigin={{ vertical: "top", horizontal: "right" }}
                  slotProps={{ paper: { sx: { mt: 0.5, minWidth: 220 } } }}
                >
                  {[
                    [
                      "csv",
                      "CSV",
                      "solar:file-text-linear",
                      "Opens in spreadsheets",
                    ],
                    [
                      "parquet",
                      "Parquet",
                      "solar:database-linear",
                      "Typed columns for data tools",
                    ],
                  ].map(([format, label, icon, hint]) => (
                    <MenuItem
                      key={format}
                      aria-label={label}
                      onClick={() => {
                        setDownloadAnchor(null);
                        download(format);
                      }}
                      sx={{ gap: 1.25, py: 1 }}
                    >
                      <Iconify
                        icon={icon}
                        width={16}
                        sx={{ color: "text.secondary", flexShrink: 0 }}
                      />
                      <Box>
                        <Typography
                          sx={{
                            typography: "s2",
                            fontWeight: "fontWeightSemiBold",
                          }}
                        >
                          {label}
                        </Typography>
                        <Typography
                          sx={{ typography: "s3", color: "text.subtitle" }}
                        >
                          {hint}
                        </Typography>
                      </Box>
                    </MenuItem>
                  ))}
                </Menu>
                <Button
                  // Quiet while unavailable so the permanently disabled state
                  // does not read as an outlined call to action.
                  variant={addToDashboardDisabled ? "text" : "outlined"}
                  size="small"
                  sx={ACTION_BUTTON_SX}
                  startIcon={<Iconify icon="solar:widget-add-linear" />}
                  disabled={addToDashboardDisabled}
                  onClick={() => setDialog("dashboard")}
                >
                  Add to dashboard
                </Button>
                <Button
                  size="small"
                  sx={{ ...ACTION_BUTTON_SX, color: "text.secondary" }}
                  startIcon={<Iconify icon="solar:history-linear" />}
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
            {selected && (
              <Stack
                direction="row"
                alignItems="center"
                flexWrap="wrap"
                useFlexGap
                gap={1}
                sx={{ px: 2, py: 1.25, borderTop: 1, borderColor: "divider" }}
              >
                <Typography
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    flex: 1,
                    minWidth: 200,
                  }}
                >
                  {selected.visibility === "project"
                    ? "Project members only. Each person runs with their current access."
                    : "Only you can open this saved query."}
                </Typography>
                {selected.visibility === "project" && (
                  <Button
                    {...ACTION_BUTTON_PROPS}
                    sx={ACTION_BUTTON_SX}
                    startIcon={<Iconify icon="solar:link-linear" />}
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
                {selected.created_by_id === user?.id && catalog?.can_save && (
                  <Button
                    {...ACTION_BUTTON_PROPS}
                    color="error"
                    sx={ACTION_BUTTON_SX}
                    startIcon={<Iconify icon="solar:trash-bin-trash-linear" />}
                    onClick={() => setDialog("delete")}
                  >
                    Delete saved query
                  </Button>
                )}
              </Stack>
            )}
          </Stack>
        </Box>
      </Stack>
      <ModalWrapper
        open={dialog === "save"}
        onClose={() => setDialog("")}
        title={updatingSaved ? "Update saved query" : "Save query"}
        subTitle={
          updatingSaved
            ? `Saving replaces “${selected.name}” for everyone who can open it.`
            : "Reopen it later from Saved, or share it with your project."
        }
        actionBtnTitle={updatingSaved ? "Save changes" : "Save"}
        onSubmit={save}
        isValid={Boolean(name.trim())}
        isLoading={saving}
        modalWidth="480px"
      >
        <Stack spacing={2}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            autoFocus
            fullWidth
            size="small"
            label="Query name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            inputProps={{ maxLength: 160 }}
          />
          <FormControl>
            <FormLabel
              id="query-visibility-label"
              sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", mb: 1 }}
            >
              Visibility
            </FormLabel>
            <RadioGroup
              aria-labelledby="query-visibility-label"
              value={visibility}
              onChange={(e) => setVisibility(e.target.value)}
              sx={{ gap: 1 }}
            >
              {[
                ["private", "Private", "Only you can open it."],
                [
                  "project",
                  "Project members",
                  "Anyone in this project can open it. Each person runs it with their own access; results are never shared.",
                ],
              ].map(([value, label, hint]) => (
                <FormControlLabel
                  key={value}
                  value={value}
                  control={<Radio size="small" sx={{ mt: -0.25 }} />}
                  label={
                    <Box>
                      <Typography
                        sx={{
                          typography: "s2",
                          fontWeight: "fontWeightSemiBold",
                        }}
                      >
                        {label}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.secondary" }}
                      >
                        {hint}
                      </Typography>
                    </Box>
                  }
                  sx={{
                    m: 0,
                    px: 1,
                    py: 1,
                    alignItems: "flex-start",
                    border: 1,
                    borderRadius: 1,
                    borderColor:
                      visibility === value ? "primary.main" : "divider",
                    bgcolor: (t) =>
                      visibility === value
                        ? alpha(t.palette.primary.main, 0.06)
                        : "transparent",
                  }}
                />
              ))}
            </RadioGroup>
          </FormControl>
          <Box
            sx={{
              p: 1.5,
              border: 1,
              borderColor: "divider",
              borderRadius: 1,
              bgcolor: "background.neutral",
            }}
          >
            <MetaLine
              icon="solar:diskette-linear"
              parts={[
                "Saves this SQL",
                describeQueryWindow(hours),
                chart?.type === "bar" ? "chart view" : "table view",
              ]}
              sx={{ mb: 0.75 }}
            />
            <Typography
              sx={{
                typography: "s3",
                fontFamily: "monospace",
                color: "text.secondary",
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
                display: "-webkit-box",
                WebkitLineClamp: 4,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}
            >
              {sql}
            </Typography>
          </Box>
        </Stack>
      </ModalWrapper>
      <ModalWrapper
        open={dialog === "dashboard"}
        onClose={() => setDialog("")}
        title="Add to dashboard"
        subTitle="The widget reruns this saved query with each viewer’s project permissions."
        actionBtnTitle="Add query"
        onSubmit={addWidget}
        isValid={Boolean(dashboardId)}
        isLoading={saving}
        modalWidth="480px"
      >
        <Stack spacing={1.5}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            select
            fullWidth
            size="small"
            label="Dashboard"
            value={dashboardId}
            onChange={(e) => setDashboardId(e.target.value)}
          >
            {dashboards.map((d) => (
              <MenuItem key={d.id} value={d.id}>
                {d.name}
              </MenuItem>
            ))}
          </TextField>
          {!dashboards.length && (
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              No dashboards yet. Create one in Dashboards first.
            </Typography>
          )}
        </Stack>
      </ModalWrapper>
      <ModalWrapper
        open={dialog === "delete"}
        onClose={() => setDialog("")}
        title="Delete saved query?"
        subTitle="Dashboard widgets using this query will stop running. Existing personal results keep their normal expiry."
        actionBtnTitle="Delete query"
        cancelBtnTitle="Keep query"
        actionBtnProps={{ color: "error" }}
        isValid
        isLoading={saving}
        modalWidth="440px"
        onSubmit={async () => {
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
        {error ? <Alert severity="error">{error}</Alert> : null}
      </ModalWrapper>
      <Dialog
        open={dialog === "history"}
        onClose={() => setDialog("")}
        fullWidth
        maxWidth="sm"
        PaperProps={{ sx: { borderRadius: 1, p: 2, gap: 1.5 } }}
      >
        <Stack direction="row" alignItems="flex-start" gap={1}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              component="h2"
              sx={{ typography: "m3", fontWeight: "fontWeightSemiBold" }}
            >
              Recent queries
            </Typography>
            <Typography sx={{ typography: "s1", color: "text.secondary" }}>
              Your last 20 runs in this project. Select one to open it in the
              editor.
            </Typography>
          </Box>
          <IconButton
            aria-label="Close"
            onClick={() => setDialog("")}
            sx={{ color: "text.primary", mt: -0.5, mr: -0.5 }}
          >
            <Iconify icon="akar-icons:cross" />
          </IconButton>
        </Stack>
        <DialogContent sx={{ p: 0 }}>
          {history.length ? (
            <List
              disablePadding
              aria-label="Recent queries"
              sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}
            >
              {history.map((item, index) => (
                <ListItemButton
                  key={item.id}
                  onClick={() => {
                    setSQL(item.sql);
                    setDialog("");
                  }}
                  sx={{
                    display: "block",
                    px: 1.5,
                    py: 1.25,
                    borderTop: index ? 1 : 0,
                    borderColor: "divider",
                    "&:hover .history-open": { opacity: 1 },
                  }}
                >
                  <Stack
                    direction="row"
                    alignItems="center"
                    gap={1}
                    sx={{ mb: 0.75 }}
                  >
                    <StatePill state={item.state}>{item.state}</StatePill>
                    <MetaLine
                      parts={[
                        relativeTime(item.created_at),
                        item.expired && "results expired",
                      ]}
                      title={new Date(item.created_at).toLocaleString()}
                      sx={{ flex: 1 }}
                    />
                    <Typography
                      className="history-open"
                      sx={{
                        typography: "s3",
                        fontWeight: "fontWeightSemiBold",
                        color: "text.secondary",
                        opacity: 0,
                        transition: "opacity 120ms",
                      }}
                    >
                      Open in editor
                    </Typography>
                  </Stack>
                  <Typography
                    title={item.sql}
                    sx={{
                      typography: "s2",
                      fontFamily: "monospace",
                      color: "text.primary",
                      whiteSpace: "pre-wrap",
                      overflowWrap: "anywhere",
                      display: "-webkit-box",
                      WebkitLineClamp: 3,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }}
                  >
                    {item.sql}
                  </Typography>
                  {item.error && (
                    <Typography
                      sx={{ typography: "s3", color: "error.main", mt: 0.5 }}
                    >
                      {item.error}
                    </Typography>
                  )}
                </ListItemButton>
              ))}
            </List>
          ) : (
            <Box sx={{ py: 4, textAlign: "center" }}>
              <Typography
                sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
              >
                No queries yet
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                Queries you run in this project appear here.
              </Typography>
            </Box>
          )}
        </DialogContent>
      </Dialog>
    </Box>
  );
}
