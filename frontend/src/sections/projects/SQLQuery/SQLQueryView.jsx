import React, { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import Editor from "@monaco-editor/react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
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
  const [catalog, setCatalog] = useState(null);
  catalogRef.current = catalog;
  useEffect(() => () => completion.current?.dispose(), []);
  const [sql, setSQL] = useState(INITIAL_SQL);
  const [hours, setHours] = useState(24);
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
    const end = new Date();
    run({
      sql,
      start: new Date(end.getTime() - hours * 3600000).toISOString(),
      end: end.toISOString(),
    });
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
    editor.current.executeEdits("catalog", [
      { range: editor.current.getSelection(), text },
    ]);
    editor.current.focus();
  };
  const mountEditor = (instance, monaco) => {
    editor.current = instance;
    completion.current?.dispose();
    completion.current = monaco.languages.registerCompletionItemProvider(
      "sql",
      {
        provideCompletionItems: (model, position) => {
          const word = model.getWordUntilPosition(position);
          const range = {
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: word.startColumn,
            endColumn: word.endColumn,
          };
          return {
            suggestions: (catalogRef.current?.datasets || [])
              .filter((dataset) => dataset.available !== false)
              .flatMap((dataset) => [
                {
                  label: dataset.name,
                  insertText: dataset.name,
                  kind: monaco.languages.CompletionItemKind.Class,
                  detail: dataset.description,
                  range,
                },
                ...dataset.columns.map((column) => ({
                  label: column.name,
                  insertText: `\`${column.name}\``,
                  kind: monaco.languages.CompletionItemKind.Field,
                  detail: `${dataset.name} · ${column.type}`,
                  range,
                })),
              ]),
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
      const blob = await sqlApi.download(observeId, job.id, format);
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
    <Box sx={{ p: { xs: 2, md: 3 }, minWidth: 0 }}>
      <Stack spacing={2}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
          gap={1}
        >
          <Box>
            <Typography variant="h5">Query</Typography>
            <Typography variant="body2" color="text.secondary">
              Explore this project with read-only ClickHouse SQL.
            </Typography>
          </Box>
          <Stack direction="row" gap={1}>
            <Chip size="small" label="Project scoped" variant="outlined" />
            <Chip
              size="small"
              label="30 s · 10,000 rows · 8 MB"
              variant="outlined"
            />
          </Stack>
        </Stack>
        {catalog?.identity_semantics && (
          <Alert severity="info">{catalog.identity_semantics}</Alert>
        )}
        {(error || queryError) && (
          <Alert severity="error" onClose={() => setError("")}>
            {error || queryError}
          </Alert>
        )}
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", md: "240px minmax(0, 1fr)" },
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
          }}
        >
          <Stack
            sx={{
              borderRight: { md: 1 },
              borderColor: "divider",
              maxHeight: { md: 850 },
              overflow: "auto",
              bgcolor: "background.neutral",
            }}
          >
            <Typography variant="overline" sx={{ px: 2, pt: 1 }}>
              Project catalog · v{catalog?.version || 1}
            </Typography>
            {!catalog && !error && <CircularProgress size={20} sx={{ m: 2 }} />}
            {(catalog?.datasets || []).map((dataset) => (
              <Box
                component="details"
                key={dataset.name}
                sx={{ px: 2, py: 0.5 }}
              >
                <Typography
                  component="summary"
                  sx={{
                    cursor: "pointer",
                    fontFamily: "monospace",
                    fontSize: 13,
                  }}
                >
                  {dataset.name}
                  {dataset.available === false ? " (unavailable)" : ""}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {dataset.description}
                  {dataset.unavailable_reason &&
                    ` ${dataset.unavailable_reason}`}
                </Typography>
                <List dense disablePadding>
                  {dataset.columns.map((c) => (
                    <ListItemButton
                      key={c.name}
                      disabled={dataset.available === false}
                      onClick={() => insert(`\`${c.name}\``)}
                      aria-label={`Insert ${dataset.name}.${c.name}`}
                      sx={{ px: 1, py: 0 }}
                    >
                      <ListItemText
                        primary={c.name}
                        secondary={c.type}
                        primaryTypographyProps={{
                          fontFamily: "monospace",
                          fontSize: 12,
                        }}
                        secondaryTypographyProps={{ fontSize: 10 }}
                      />
                    </ListItemButton>
                  ))}
                </List>
              </Box>
            ))}
            <QueryExamples
              examples={catalog?.examples}
              datasets={catalog?.datasets}
              onUse={applyExample}
            />
            <Divider sx={{ my: 1 }} />
            <Typography variant="overline" sx={{ px: 2 }}>
              Saved queries
            </Typography>
            <List dense>
              {saved.map((query) => (
                <ListItemButton
                  key={query.id}
                  selected={selected?.id === query.id}
                  onClick={() => {
                    setSelected(query);
                    setSQL(query.sql);
                    setHours(query.window_hours);
                    setChart(query.chart);
                  }}
                >
                  <ListItemText
                    primary={query.name}
                    secondary={
                      query.visibility === "project"
                        ? "Shared with project"
                        : "Private"
                    }
                  />
                </ListItemButton>
              ))}
            </List>
            {!saved.length && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ px: 2, pb: 2 }}
              >
                Save a query to revisit it or share it with project members.
              </Typography>
            )}
            <Stack direction="row">
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
              <Button
                size="small"
                disabled={!more}
                onClick={() =>
                  loadSaved(offset + 20).catch((e) => setError(errorMessage(e)))
                }
              >
                Next
              </Button>
            </Stack>
          </Stack>
          <Stack sx={{ minWidth: 0 }}>
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
                sx={{ minWidth: 160 }}
              >
                {[1, 24, 168, 744].map((value) => (
                  <MenuItem key={value} value={value}>
                    {value === 1 ? "Last hour" : `Last ${value / 24} days`}
                  </MenuItem>
                ))}
              </TextField>
              <Button
                variant="contained"
                onClick={execute}
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
              height="260px"
              language="sql"
              value={sql}
              onChange={(value) => setSQL(value || "")}
              theme={theme.palette.mode === "dark" ? "vs-dark" : "light"}
              onMount={mountEditor}
              options={{
                minimap: { enabled: false },
                fontSize: 13,
                fontFamily: "monospace",
                scrollBeyondLastLine: false,
                wordWrap: "on",
                ariaLabel: "SQL query editor",
                automaticLayout: true,
                tabSize: 2,
              }}
            />
            <QueryEstimate
              projectId={observeId}
              sql={sql}
              hours={hours}
              enabled={Boolean(catalog)}
            />
            <Stack
              direction="row"
              gap={1}
              flexWrap="wrap"
              useFlexGap
              alignItems="center"
              sx={{
                p: 1.5,
                borderTop: 1,
                borderBottom: 1,
                borderColor: "divider",
              }}
            >
              <Typography variant="subtitle2">Results</Typography>
              {job?.sql && job.sql !== sql && (
                <Typography variant="caption" color="text.secondary">
                  Previous query results — run to update
                </Typography>
              )}
              <Typography
                variant="caption"
                role="status"
                color="text.secondary"
              >
                {busy
                  ? `Query ${job?.state || "submitting"}…`
                  : job?.state || "Ready"}
              </Typography>
              <Box sx={{ flex: 1 }} />
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
            <Box sx={{ p: 2, minHeight: 160 }}>
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
      </Dialog>
    </Box>
  );
}
