import { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";

const TREE_SCHEMA = "omega-run-task-tree/v1";

function record(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function text(value, fallback = "Unknown") {
  return typeof value === "string" && value.trim().length > 0
    ? value
    : fallback;
}

function number(value, fallback = null) {
  return Number.isSafeInteger(value) && value >= 0 ? value : fallback;
}

function normalizeTrees(result, runId) {
  if (result?.available !== true) {
    return {
      state: "unavailable",
      reason: result?.reason || "The task tree is unavailable.",
    };
  }
  if (
    result.schemaVersion !== TREE_SCHEMA ||
    (typeof runId === "string" && result.runId !== runId)
  ) {
    return {
      state: "foreign",
      reason: "The server returned a task tree for a different run.",
    };
  }
  const source = Array.isArray(result.trees)
    ? result.trees
    : Array.isArray(result.tree)
      ? result.tree.some((entry) => Array.isArray(entry?.nodes))
        ? result.tree
        : [{ treeId: "tree-1", nodes: result.tree }]
      : Array.isArray(result.tasks)
        ? [{ treeId: "tree-1", nodes: result.tasks }]
        : Array.isArray(result.nodes)
          ? [{ treeId: "tree-1", nodes: result.nodes }]
          : [];
  const trees = source
    .map((tree, index) => {
      if (!record(tree)) return null;
      const nodes = Array.isArray(tree.nodes)
        ? tree.nodes.filter(
            (node) =>
              record(node) &&
              (typeof node.id === "string" || typeof node.taskId === "string"),
          )
        : [];
      return {
        id: text(tree.treeId || tree.id, `tree-${index + 1}`),
        rootTaskId: text(tree.rootTaskId, ""),
        nodes,
      };
    })
    .filter(Boolean);
  const integrity = result.integrity;
  const incomplete =
    integrity === "degraded" ||
    integrity === "broken" ||
    result.completeness?.complete === false;
  return {
    state: "ready",
    trees,
    incomplete,
    integrity: text(integrity, "verified"),
    unresolvedCount: number(
      result.unresolvedTaskIds?.length,
      number(result.unresolvedTaskCount, 0),
    ),
    quarantinedCount: number(result.quarantinedTaskCount, 0),
  };
}

export default function TaskTreeInspector({ treeResult, runId }) {
  const normalized = useMemo(
    () => normalizeTrees(treeResult, runId),
    [treeResult, runId],
  );
  const [treeId, setTreeId] = useState("");
  const [status, setStatus] = useState("all");
  const [maxDepth, setMaxDepth] = useState("all");
  const [selectedId, setSelectedId] = useState("");

  useEffect(() => {
    setTreeId(
      normalized.state === "ready" ? normalized.trees?.[0]?.id || "" : "",
    );
    setStatus("all");
    setMaxDepth("all");
    setSelectedId("");
  }, [normalized]);

  const selectedTree =
    normalized.state === "ready"
      ? normalized.trees.find((tree) => tree.id === treeId)
      : null;
  const statuses = useMemo(() => {
    if (!selectedTree) return [];
    return [
      ...new Set(
        selectedTree.nodes.map((node) => text(node.status, "unknown")),
      ),
    ].sort();
  }, [selectedTree]);
  const maxAvailableDepth = useMemo(() => {
    if (!selectedTree) return 0;
    return Math.max(
      0,
      ...selectedTree.nodes.map((node) => number(node.lineage?.depth, 0)),
    );
  }, [selectedTree]);
  const rows =
    selectedTree?.nodes.filter((node) => {
      const nodeStatus = text(node.status, "unknown");
      const depth = number(node.lineage?.depth, 0);
      return (
        (status === "all" || nodeStatus === status) &&
        (maxDepth === "all" || depth <= Number(maxDepth))
      );
    }) || [];
  const selected =
    selectedTree?.nodes.find(
      (node) => (node.id || node.taskId) === selectedId,
    ) || null;

  if (normalized.state === "unavailable" || normalized.state === "foreign") {
    return (
      <Card
        variant="outlined"
        component="section"
        aria-label="Task tree inspector"
      >
        <CardContent>
          <Typography variant="h6">Task tree</Typography>
          <Alert severity="info" sx={{ mt: 1.5 }}>
            {normalized.reason}
          </Alert>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card
      variant="outlined"
      component="section"
      aria-label="Task tree inspector"
    >
      <CardContent>
        <Stack spacing={1.5}>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            justifyContent="space-between"
            alignItems={{ sm: "center" }}
            gap={1}
          >
            <Box>
              <Typography variant="h6">Task tree</Typography>
              <Typography variant="body2" color="text.secondary">
                Select a server-reported task to focus its lineage. These
                controls only change the local view; task execution remains
                remote and authoritative.
              </Typography>
            </Box>
            <Stack direction="row" spacing={0.75} flexWrap="wrap">
              <Chip
                size="small"
                variant="outlined"
                label={`${selectedTree?.nodes.length || 0} tasks`}
              />
              <Chip
                size="small"
                color={normalized.incomplete ? "warning" : "success"}
                variant="outlined"
                label={
                  normalized.incomplete ? "Incomplete" : "Integrity reported"
                }
              />
            </Stack>
          </Stack>
          {normalized.incomplete ? (
            <Alert severity="warning">
              The server marked this forest incomplete ({normalized.integrity}).
              Missing or quarantined records are withheld; do not infer a
              complete subagent history.
            </Alert>
          ) : null}
          {normalized.unresolvedCount > 0 || normalized.quarantinedCount > 0 ? (
            <Alert severity="info">
              {normalized.unresolvedCount} unresolved reference
              {normalized.unresolvedCount === 1 ? "" : "s"} and{" "}
              {normalized.quarantinedCount} quarantined record
              {normalized.quarantinedCount === 1 ? "" : "s"}. Their identifiers
              are intentionally not rendered.
            </Alert>
          ) : null}
          {normalized.trees.length === 0 ? (
            <Typography color="text.secondary">
              No server-reported task trees are available.
            </Typography>
          ) : (
            <>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
                {normalized.trees.length > 1 ? (
                  <FormControl size="small" sx={{ minWidth: 180 }}>
                    <InputLabel id="task-tree-select-label">Forest</InputLabel>
                    <Select
                      labelId="task-tree-select-label"
                      label="Forest"
                      value={treeId}
                      onChange={(event) => {
                        setTreeId(event.target.value);
                        setSelectedId("");
                      }}
                    >
                      {normalized.trees.map((tree) => (
                        <MenuItem key={tree.id} value={tree.id}>
                          {tree.id}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                ) : null}
                <FormControl size="small" sx={{ minWidth: 170 }}>
                  <InputLabel id="task-status-filter-label">Status</InputLabel>
                  <Select
                    labelId="task-status-filter-label"
                    label="Status"
                    value={status}
                    onChange={(event) => {
                      setStatus(event.target.value);
                      setSelectedId("");
                    }}
                  >
                    <MenuItem value="all">All statuses</MenuItem>
                    {statuses.map((value) => (
                      <MenuItem key={value} value={value}>
                        {value}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <FormControl size="small" sx={{ minWidth: 170 }}>
                  <InputLabel id="task-depth-filter-label">
                    Maximum depth
                  </InputLabel>
                  <Select
                    labelId="task-depth-filter-label"
                    label="Maximum depth"
                    value={maxDepth}
                    onChange={(event) => {
                      setMaxDepth(event.target.value);
                      setSelectedId("");
                    }}
                  >
                    <MenuItem value="all">All depths</MenuItem>
                    {Array.from(
                      { length: maxAvailableDepth + 1 },
                      (_, depth) => (
                        <MenuItem key={depth} value={String(depth)}>
                          {depth === 0 ? "Root only" : `Through depth ${depth}`}
                        </MenuItem>
                      ),
                    )}
                  </Select>
                </FormControl>
                <Button
                  size="small"
                  onClick={() => {
                    setStatus("all");
                    setMaxDepth("all");
                    setSelectedId("");
                  }}
                >
                  Reset view
                </Button>
              </Stack>
              <Stack spacing={0.5} aria-label="Task tree nodes">
                {rows.length === 0 ? (
                  <Typography color="text.secondary">
                    No tasks match this view.
                  </Typography>
                ) : (
                  rows.map((node, index) => {
                    const id = node.id || node.taskId || `task-${index}`;
                    const depth = number(node.lineage?.depth, 0);
                    return (
                      <Button
                        key={id}
                        variant={selectedId === id ? "contained" : "text"}
                        color={selectedId === id ? "primary" : "inherit"}
                        onClick={() => setSelectedId(id)}
                        sx={{
                          justifyContent: "flex-start",
                          textAlign: "left",
                          pl: 1 + Math.min(depth, 6) * 2,
                        }}
                      >
                        <Stack
                          direction="row"
                          spacing={1}
                          alignItems="center"
                          sx={{ width: "100%" }}
                        >
                          <Typography
                            component="span"
                            variant="body2"
                            sx={{ flex: 1 }}
                          >
                            {text(node.title || node.name, id)}
                          </Typography>
                          <Chip
                            size="small"
                            variant="outlined"
                            label={text(node.status, "unknown")}
                          />
                        </Stack>
                      </Button>
                    );
                  })
                )}
              </Stack>
              {selected ? (
                <Card variant="outlined">
                  <CardContent sx={{ "&:last-child": { pb: 1.5 } }}>
                    <Typography variant="subtitle2">Focused task</Typography>
                    <Typography variant="body2" sx={{ mt: 0.5 }}>
                      {text(
                        selected.title || selected.name,
                        selected.id || selected.taskId,
                      )}
                    </Typography>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ display: "block", mt: 0.75 }}
                    >
                      {text(selected.status, "unknown")} · depth{" "}
                      {number(selected.lineage?.depth, 0)} ·{" "}
                      {selected.lineage?.kind === "root" ? "root" : "subtask"}
                      {selected.agentId ? ` · agent ${selected.agentId}` : ""}
                    </Typography>
                    {Array.isArray(selected.dependencies) &&
                    selected.dependencies.length > 0 ? (
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ display: "block", mt: 0.5 }}
                      >
                        Dependencies: {selected.dependencies.join(", ")}
                      </Typography>
                    ) : null}
                    {selected.receipt?.hash ? (
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ display: "block", mt: 0.5 }}
                      >
                        Receipt: {String(selected.receipt.hash).slice(0, 16)}…
                      </Typography>
                    ) : null}
                  </CardContent>
                </Card>
              ) : (
                <Typography variant="caption" color="text.secondary">
                  Choose a task to focus its server-reported lineage.
                </Typography>
              )}
              <Divider />
              <Typography variant="caption" color="text.secondary">
                The inspector exposes no child-task mutation or operator Society
                route.
              </Typography>
            </>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}

TaskTreeInspector.propTypes = {
  treeResult: PropTypes.shape({ available: PropTypes.bool }),
  runId: PropTypes.string,
};
