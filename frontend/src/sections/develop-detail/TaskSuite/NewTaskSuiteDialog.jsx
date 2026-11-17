import React, { useEffect, useMemo, useState } from "react";
import { flushSync } from "react-dom";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  CircularProgress,
  Dialog,
  IconButton,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import V1PreviewLine from "src/sections/develop/AddDatasetDrawer/V1PreviewLine";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { openAddData } from "src/sections/common/AddData/addDataStore";

const STANDARD_COLUMNS = [
  "title",
  "instruction",
  "customer_goal",
  "order_id",
  "start_state",
  "checks",
  "category",
  "difficulty",
  "allowed_tools",
];
const MONO = { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" };
const MONO_INPUT = { ...MONO, fontSize: 13 };

const START_FROM = [
  {
    id: "empty",
    label: "Empty",
    help: "Add tasks by hand",
    icon: "mdi:file-outline",
  },
  {
    id: "sessions",
    label: "Production sessions (as tasks)",
    help: "Pick sessions in Observe; each becomes a task with drafted checks",
    icon: "mdi:forum-outline",
  },
  {
    id: "file",
    label: "A file",
    help: "CSV or JSONL with the standard columns",
    icon: "mdi:file-delimited-outline",
  },
];

/**
 * New dataset › Task suite (R2-SPEC §5.3.8 a), 600 px: name, environment
 * (`Pick ▾` a saved one or `New environment…`), start from `Empty` ·
 * `Production sessions (as tasks)` (creates the suite, then opens the
 * Add-data sheet with it as the target, shape `task`) · `A file`. Creates a
 * dataset of kind `task_suite` with the standard columns.
 *
 * Props (frozen, R2 manifest row 28): open, onClose, datasetId, rowId.
 */
const NewTaskSuiteDialog = ({ open, onClose }) => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: datasets = [] } = useDevelopDatasetList();
  const environments = useMemo(() => {
    const seen = new Map();
    datasets
      .filter((d) => d.kind === "task_suite" && d.environment?.image)
      .forEach((d) => {
        if (!seen.has(d.environment.image))
          seen.set(d.environment.image, { env: d.environment, from: d.name });
      });
    return [...seen.entries()].map(([image, v]) => ({ id: image, ...v }));
  }, [datasets]);

  const [name, setName] = useState("");
  const [touched, setTouched] = useState(false);
  const [envId, setEnvId] = useState("");
  const [newImage, setNewImage] = useState("");
  const [newMcp, setNewMcp] = useState("");
  const [startFrom, setStartFrom] = useState("empty");
  // Closed by itself after a create (the host may keep `open` until it
  // unmounts on navigation).
  const [done, setDone] = useState(false);
  useEffect(() => {
    if (!open) return;
    setDone(false);
    setName("");
    setTouched(false);
    setStartFrom("empty");
    setNewImage("");
    setNewMcp("");
    setEnvId(environments[0]?.id ?? "__new");
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const trimmed = name.trim();
  const taken = datasets.some((d) => d.name === trimmed);
  const imageOk = /^[\w.-]+(\/[\w.-]+)*:[\w.-]+$/.test(newImage.trim());
  const reason = !trimmed
    ? "Name the task suite"
    : taken
      ? "A dataset with this name already exists"
      : envId === "__new" && !imageOk
        ? "Enter the environment image as registry/name:tag"
        : null;

  const environment =
    envId === "__new"
      ? {
          image: newImage.trim(),
          resources: { cpu: 2, memory_gb: 4, timeout_s: 600 },
          mcp: newMcp.trim()
            ? /^https?:/.test(newMcp.trim())
              ? { url: newMcp.trim(), tools: 0, tool_names: [] }
              : { command: newMcp.trim(), tools: 0, tool_names: [] }
            : null,
          start_state_schema: null,
          reset_command: "",
          secrets: [],
        }
      : environments.find((e) => e.id === envId)?.env ?? null;

  const create = useMutation({
    mutationFn: () =>
      axios.post(endpoints.develop.createDatasetManually, {
        dataset_name: trimmed,
        kind: "task_suite",
        // The contract needs ≥ 1; task suites start empty (the server ignores it).
        number_of_rows: 1,
        number_of_columns: STANDARD_COLUMNS.length,
        environment,
      }),
    meta: { errorHandled: true },
    onSuccess: (res) => {
      const id = res.data?.result?.dataset_id ?? res.data?.dataset_id;
      queryClient.invalidateQueries({ queryKey: ["develop", "dataset-list"] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-name-list"],
      });
      enqueueSnackbar(`${trimmed} created`, { variant: "success" });
      // Close before navigating: the route change runs in a transition that
      // would hold this update until the dataset page has loaded.
      flushSync(() => {
        setDone(true);
        onClose?.();
      });
      if (!id) return;
      navigate(`/dashboard/develop/${id}?tab=data`);
      if (startFrom === "sessions")
        openAddData({
          source: { kind: "sessions" },
          target: { dataset_id: id },
          shape: "task",
        });
      if (startFrom === "file")
        openAddData({
          source: { kind: "file" },
          target: { dataset_id: id },
          shape: "task",
        });
    },
    onError: (e) =>
      enqueueSnackbar(
        typeof e?.result === "string"
          ? e.result
          : e?.message ?? "Could not create the task suite",
        { variant: "error" },
      ),
  });

  return (
    <Dialog
      open={Boolean(open)}
      onClose={onClose}
      PaperProps={{
        sx: { width: 600, maxWidth: "calc(100vw - 32px)", borderRadius: 1.5 },
      }}
      data-testid="new-task-suite"
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        gap={1}
        sx={{ px: 3, pt: 2.5, pb: 1 }}
      >
        <Box sx={{ flex: 1 }}>
          <Typography typography="m3" fontWeight={600}>
            New task suite
          </Typography>
          <Typography typography="s2" color="text.secondary" sx={{ mt: 0.25 }}>
            Agent tasks with a start state and end-state checks, run in your
            sandbox.
          </Typography>
        </Box>
        <IconButton size="small" aria-label="Close" onClick={onClose}>
          <Iconify icon="mdi:close" width={18} />
        </IconButton>
      </Stack>

      <Stack gap={2.25} sx={{ px: 3, py: 2 }}>
        <TextField
          size="small"
          label="Name"
          fullWidth
          autoFocus
          value={name}
          placeholder="Support ops tasks · staging"
          onChange={(e) => setName(e.target.value)}
          onBlur={() => setTouched(true)}
          error={
            touched &&
            Boolean(reason) &&
            reason !== "Enter the environment image as registry/name:tag"
          }
          helperText={
            touched &&
            reason &&
            reason !== "Enter the environment image as registry/name:tag"
              ? reason
              : " "
          }
          inputProps={{ maxLength: 80, "data-testid": "task-suite-name" }}
        />

        <Box>
          <Typography typography="s2" fontWeight={600} sx={{ mb: 0.5 }}>
            Environment
          </Typography>
          <Select
            size="small"
            fullWidth
            value={envId}
            onChange={(e) => setEnvId(e.target.value)}
            inputProps={{ "aria-label": "Environment" }}
            renderValue={(v) =>
              v === "__new" ? (
                "New environment…"
              ) : (
                <Box component="span" sx={MONO}>
                  {v}
                </Box>
              )
            }
          >
            {environments.map((e) => (
              <MenuItem
                key={e.id}
                value={e.id}
                dense
                sx={{ flexDirection: "column", alignItems: "flex-start" }}
              >
                <Typography typography="s2" sx={MONO}>
                  {e.id}
                </Typography>
                <Typography typography="s3" color="text.secondary">
                  {`${e.env.mcp?.tools ?? 0} tools · used by ${e.from}`}
                </Typography>
              </MenuItem>
            ))}
            <MenuItem value="__new" dense>
              <Iconify icon="mdi:plus" width={14} sx={{ mr: 0.75 }} />
              New environment…
            </MenuItem>
          </Select>
          {envId === "__new" && (
            <Stack direction="row" gap={1} sx={{ mt: 1 }}>
              <TextField
                size="small"
                fullWidth
                label="Image"
                value={newImage}
                placeholder="ghcr.io/acme/acme-support-sandbox:1.5"
                onChange={(e) => setNewImage(e.target.value)}
                error={Boolean(newImage) && !imageOk}
                helperText={
                  newImage && !imageOk ? "Use registry/name:tag" : " "
                }
                inputProps={{
                  style: MONO_INPUT,
                  "data-testid": "task-suite-image",
                }}
              />
              <TextField
                size="small"
                fullWidth
                label="MCP server URL or command"
                value={newMcp}
                placeholder="https://sandbox.acme.ai/mcp"
                onChange={(e) => setNewMcp(e.target.value)}
                helperText={
                  newMcp.trim()
                    ? "No MCP server answers at a new address yet, so this environment starts with no MCP tools; runs on it call none until Test connection in Environment lists them."
                    : "Optional; set it later in Environment"
                }
                inputProps={{ style: MONO_INPUT }}
              />
            </Stack>
          )}
        </Box>

        <Box>
          <Typography typography="s2" fontWeight={600} sx={{ mb: 0.5 }}>
            Start from
          </Typography>
          <Stack gap={0.75} role="radiogroup" aria-label="Start from">
            {START_FROM.map((o) => {
              const on = startFrom === o.id;
              return (
                <CustomTooltip
                  key={o.id}
                  show
                  size="small"
                  placement="right"
                  arrow
                  title={o.help}
                >
                  <ButtonBase
                    role="radio"
                    aria-checked={on}
                    data-testid={`task-suite-start-${o.id}`}
                    onClick={() => setStartFrom(o.id)}
                    sx={{
                      justifyContent: "flex-start",
                      gap: 1.25,
                      px: 1.5,
                      py: 1,
                      borderRadius: 1,
                      textAlign: "left",
                      border: "1px solid",
                      borderColor: on ? "primary.main" : "divider",
                      bgcolor: (t) =>
                        on
                          ? alpha(t.palette.primary.main, 0.08)
                          : "transparent",
                    }}
                  >
                    <Iconify
                      icon={on ? "mdi:radiobox-marked" : "mdi:radiobox-blank"}
                      width={18}
                      sx={{ color: on ? "primary.main" : "text.disabled" }}
                    />
                    <Iconify
                      icon={o.icon}
                      width={18}
                      sx={{ color: "text.secondary" }}
                    />
                    <Typography typography="s2" fontWeight={600}>
                      {o.label}
                    </Typography>
                  </ButtonBase>
                </CustomTooltip>
              );
            })}
          </Stack>
        </Box>

        <CustomTooltip
          show
          size="small"
          placement="top-start"
          arrow
          title={`${STANDARD_COLUMNS.join(", ")} · start_state is JSON; checks is a JSON list of Check evals.`}
        >
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{ alignSelf: "flex-start", color: "text.secondary" }}
          >
            <Iconify icon="mdi:table-column" width={14} />
            <Typography typography="s3">
              {STANDARD_COLUMNS.length} standard columns
            </Typography>
          </Stack>
        </CustomTooltip>
        <V1PreviewLine
          name={trimmed}
          enabled={!reason}
          testId="task-suite-v1-preview"
          spec={{
            kind: "task_suite",
            number_of_columns: STANDARD_COLUMNS.length,
          }}
        />
        {startFrom !== "empty" && !reason ? (
          <Typography typography="s3" color="text.secondary">
            {startFrom === "file"
              ? "Then the file's tasks are added as pending changes, saved as v2."
              : "Then the sessions you pick are added as pending changes, saved as v2."}
          </Typography>
        ) : null}
      </Stack>

      <Stack
        direction="row"
        justifyContent="flex-end"
        gap={1}
        sx={{ px: 3, pb: 2.5 }}
      >
        <Button size="small" variant="text" onClick={onClose}>
          Cancel
        </Button>
        <CustomTooltip
          show={Boolean(reason)}
          size="small"
          placement="top"
          arrow
          title={reason ?? ""}
        >
          <span>
            <Button
              size="small"
              variant="contained"
              disabled={Boolean(reason) || create.isPending}
              onClick={() => create.mutate()}
              data-testid="task-suite-create"
              startIcon={
                create.isPending ? (
                  <CircularProgress size={12} color="inherit" />
                ) : null
              }
            >
              {startFrom === "empty" ? "Create" : "Create and add tasks"}
            </Button>
          </span>
        </CustomTooltip>
      </Stack>
    </Dialog>
  );
};

NewTaskSuiteDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  datasetId: PropTypes.string,
  rowId: PropTypes.string,
};

export default NewTaskSuiteDialog;
