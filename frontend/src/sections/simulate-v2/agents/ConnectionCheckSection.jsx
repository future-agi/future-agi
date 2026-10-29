import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CodeBlock from "src/components/code-block";
import SettingsSection from "./SettingsSection";
import { foundValuesOf } from "./foundFacts";
import { TESTED_VERSIONS } from "../_mock/testedVersions";
import {
  checksFor,
  connectionSpecOf,
  connectionStateOf,
  runConnectionCheck,
  startsFromCode,
} from "../_mock/connectionCheck";

const MONO = "ui-monospace, Menlo, monospace";
const STEP_MS = 450;

const STATE_ICON = {
  ok: { icon: "solar:check-circle-linear", color: "status.success.fg" },
  failed: { icon: "solar:close-circle-linear", color: "status.danger.fg" },
  unavailable: { icon: "solar:info-circle-linear", color: "text.tertiary" },
};

/* The fields a person confirms, in the order they read them. */
const FIELDS = [
  { id: "startCommand", label: "Start command", code: true },
  { id: "workdir", label: "Working directory", code: true },
  { id: "port", label: "Port", code: true },
  { id: "health", label: "Health check", code: true },
  { id: "path", label: "Endpoint", always: true, code: true },
  {
    id: "messageField",
    label: "Message field",
    always: true,
    code: true,
    http: true,
  },
  {
    id: "sessionField",
    label: "Session ID field",
    always: true,
    code: true,
    http: true,
  },
  {
    id: "replyField",
    label: "Reply field",
    always: true,
    code: true,
    http: true,
  },
  {
    id: "taskIdField",
    label: "Job ID field",
    always: true,
    code: true,
    async: true,
  },
  {
    id: "progressPath",
    label: "Progress events",
    always: true,
    code: true,
    async: true,
  },
  {
    id: "statusField",
    label: "Status field",
    always: true,
    code: true,
    async: true,
  },
  {
    id: "artifactField",
    label: "Artifact field",
    always: true,
    code: true,
    async: true,
  },
  {
    id: "cancelPath",
    label: "Cancel endpoint",
    always: true,
    code: true,
    async: true,
  },
];
/* How we talk to it — a conversation turn over HTTP, or a task that answers
   with a job ID and finishes later. */
const METHODS = [
  { id: "http", label: "Conversation over HTTP" },
  { id: "async", label: "Async task (job ID, A2A)" },
];
const STATUS_TONE = {
  Supported: "text.primary",
  Partial: "text.secondary",
  Untested: "text.disabled",
};
/* Which field a failed check's repair opens. */
const REPAIR_FIELD = {
  start: "startCommand",
  reply: "replyField",
  session: "sessionField",
  taskId: "taskIdField",
  status: "statusField",
  artifact: "artifactField",
};

/**
 * How we start and talk to a code or endpoint agent, and the connection
 * check that makes it ready (roadmap connect.md, Week 1): one result per
 * capability, each failure with its repair. Shown on the agent's Connections
 * tab. Settings save as a draft; only a passing check makes it ready.
 */
export default function ConnectionCheckSection({ agent, onSaveValues }) {
  const found = foundValuesOf(agent);
  const spec = connectionSpecOf(agent, found);
  const fromCode = startsFromCode(agent);
  const [editing, setEditing] = useState(null);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [draft, setDraft] = useState(spec);
  /* The fields for the method being shown — the draft's while editing. */
  const method = (editing ? draft : spec).method;
  const fields = FIELDS.filter(
    (f) =>
      (f.always || fromCode) &&
      !(f.http && method === "async") &&
      !(f.async && method !== "async"),
  );
  const CHECKS = checksFor(spec);
  const focusRef = useRef(null);
  const [testing, setTesting] = useState(-1);
  const timers = useRef([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  const status = connectionStateOf(agent);
  const test = agent.values?.connectionTest || null;
  const shown =
    testing >= 0
      ? CHECKS.map((c, i) => ({
          ...c,
          state: i < testing ? "done" : i === testing ? "running" : "waiting",
        }))
      : test?.results || null;

  const edit = (fieldId = null) => {
    setDraft(spec);
    setEditing(fieldId || "all");
  };
  useEffect(() => {
    if (editing && editing !== "all") focusRef.current?.focus();
  }, [editing]);

  const save = () => {
    /* A changed connection is tested again before it's ready. */
    onSaveValues(
      { connection: draft, connectionTest: null },
      "Saved as a draft — test the connection to make it ready.",
    );
    setEditing(null);
  };

  const runTest = () => {
    timers.current.forEach(clearTimeout);
    setTesting(0);
    CHECKS.forEach((_, i) => {
      timers.current.push(
        setTimeout(() => setTesting(i + 1), STEP_MS * (i + 1)),
      );
    });
    timers.current.push(
      setTimeout(
        () => {
          const out = runConnectionCheck(agent, spec, found.tools);
          setTesting(-1);
          onSaveValues(
            {
              connectionTest: {
                at: new Date().toISOString(),
                ...out,
              },
            },
            out.passed
              ? "Connection ready — every check passed."
              : `${out.results.filter((r) => r.state === "failed").length} check${out.results.filter((r) => r.state === "failed").length === 1 ? "" : "s"} failed — the fix is next to each.`,
          );
        },
        STEP_MS * (CHECKS.length + 1),
      ),
    );
  };

  const repair = (r) => {
    if (REPAIR_FIELD[r.repair.id]) return edit(REPAIR_FIELD[r.repair.id]);
    if (r.repair.id === "tools") {
      onSaveValues(
        {
          connection: { ...spec, toolsRedirected: true },
          connectionTest: null,
        },
        "Its tools now read their address from the environment — test again.",
      );
    }
  };
  const observeOnly = (tool) =>
    onSaveValues(
      {
        connection: { ...spec, observeOnly: [...spec.observeOnly, tool] },
        connectionTest: null,
      },
      `${tool} will be observed only — scenarios that need it to write stay out of the run.`,
    );

  const asyncTask = spec.method === "async";
  const request = JSON.stringify(
    asyncTask
      ? { task: "Summarise the refund for order ORD-1042" }
      : {
          [spec.messageField || "message"]: "Where is my order?",
          ...(spec.sessionField && { [spec.sessionField]: "test-1" }),
        },
    null,
    2,
  );
  const reply = JSON.stringify(
    asyncTask
      ? {
          [spec.taskIdField || "job_id"]: "job_7f3a",
          [spec.statusField || "status"]: "done",
          ...(spec.artifactField && {
            [spec.artifactField]: "…/refund-summary.pdf",
          }),
        }
      : { [spec.replyField || "reply"]: "What is your order number?" },
    null,
    2,
  );

  return (
    <>
      <SettingsSection
        title="How we run and talk to it"
        sub="Confirmed by you — a detected value is a suggestion, never a hidden assumption"
      >
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
            overflow: "hidden",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            spacing={2}
            sx={{ px: 2, py: 1 }}
          >
            <Typography
              sx={{
                typography: "s2",
                color: "text.subtitle",
                width: 150,
                flexShrink: 0,
              }}
            >
              How we talk to it
            </Typography>
            {editing ? (
              <TextField
                select
                size="small"
                fullWidth
                value={draft.method}
                onChange={(e) => setDraft({ ...draft, method: e.target.value })}
                InputProps={{ sx: { typography: "s2" } }}
              >
                {METHODS.map((m) => (
                  <MenuItem key={m.id} value={m.id} sx={{ typography: "s2" }}>
                    {m.label}
                  </MenuItem>
                ))}
              </TextField>
            ) : (
              <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
                {METHODS.find((m) => m.id === spec.method)?.label}
              </Typography>
            )}
          </Stack>
          {fields.map((f) => (
            <Stack
              key={f.id}
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{
                px: 2,
                py: 1,
                borderTop: "1px solid",
                borderColor: "divider",
                "&:first-of-type": { borderTop: "none" },
              }}
            >
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.subtitle",
                  width: 150,
                  flexShrink: 0,
                }}
              >
                {f.label}
              </Typography>
              {editing ? (
                <TextField
                  size="small"
                  fullWidth
                  inputRef={editing === f.id ? focusRef : undefined}
                  value={draft[f.id] || ""}
                  onChange={(e) =>
                    setDraft({ ...draft, [f.id]: e.target.value })
                  }
                  placeholder={
                    f.id === "sessionField"
                      ? "session_id — sent with every turn"
                      : f.id === "startCommand"
                        ? "python server.py --port 8080"
                        : ""
                  }
                  InputProps={{ sx: { typography: "s2", fontFamily: MONO } }}
                />
              ) : (
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    fontFamily: f.code ? MONO : undefined,
                    color: spec[f.id] ? "text.primary" : "text.disabled",
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {spec[f.id] || "Not set"}
                </Typography>
              )}
            </Stack>
          ))}
        </Box>
        <Stack direction="row" spacing={1} justifyContent="flex-end">
          <Button
            size="small"
            onClick={() => setVersionsOpen(true)}
            sx={{ color: "text.secondary", mr: "auto" }}
          >
            Tested versions
          </Button>
          {editing ? (
            <>
              <Button
                size="small"
                onClick={() => setEditing(null)}
                sx={{ color: "text.secondary" }}
              >
                Cancel
              </Button>
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={save}
              >
                Save draft
              </Button>
            </>
          ) : (
            <Button
              size="small"
              variant="outlined"
              onClick={() => edit()}
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              Edit
            </Button>
          )}
        </Stack>
        {/* One turn, as we'll send it and read it back. */}
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
            gap: 1.5,
          }}
        >
          <Box>
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mb: 0.5 }}
            >
              {`${asyncTask ? "Task" : "Request"} · POST ${spec.path || "/chat"}`}
            </Typography>
            <CodeBlock code={request} />
          </Box>
          <Box>
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mb: 0.5 }}
            >
              {asyncTask
                ? "When it's done · the artifact is what a trial grades"
                : `Reply · shown as the agent's turn from ${spec.replyField || "—"}`}
            </Typography>
            <CodeBlock code={reply} />
          </Box>
        </Box>
      </SettingsSection>

      <SettingsSection
        title="Connection check"
        sub="Each capability on its own — it awards no score, it shows the run can trust the connection"
      >
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {status.label}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {status.line}
            </Typography>
          </Box>
          <Button
            size="small"
            variant={status.state === "ready" ? "outlined" : "contained"}
            color={status.state === "ready" ? "inherit" : "primary"}
            disabled={testing >= 0 || !!editing}
            onClick={runTest}
            startIcon={<Iconify icon="solar:play-bold" width={14} />}
            sx={
              status.state === "ready"
                ? { borderColor: "divider", flexShrink: 0 }
                : { flexShrink: 0 }
            }
          >
            {testing >= 0
              ? "Testing…"
              : test
                ? "Test again"
                : "Test connection"}
          </Button>
        </Stack>
        {shown && (
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
              overflow: "hidden",
              "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
            }}
          >
            {shown.map((r) => {
              const icon =
                r.state === "running"
                  ? {
                      icon: "svg-spinners:ring-resize",
                      color: "text.secondary",
                    }
                  : r.state === "done"
                    ? {
                        icon: "solar:check-circle-linear",
                        color: "text.tertiary",
                      }
                    : r.state === "waiting"
                      ? {
                          icon: "solar:clock-circle-linear",
                          color: "text.disabled",
                        }
                      : STATE_ICON[r.state];
              return (
                <Stack
                  key={r.id}
                  direction="row"
                  spacing={1.25}
                  sx={{ px: 2, py: 1.125 }}
                >
                  <Iconify
                    icon={icon.icon}
                    width={16}
                    sx={{ color: icon.color, flexShrink: 0, mt: "2px" }}
                  />
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography sx={{ typography: "s2", fontWeight: 500 }}>
                      {r.label}
                    </Typography>
                    {r.detail && (
                      <Typography
                        sx={{ typography: "s3", color: "text.secondary" }}
                      >
                        {r.detail}
                      </Typography>
                    )}
                  </Box>
                  {r.repair && testing < 0 && (
                    <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
                      {r.repair.id === "tools" &&
                        test?.productionTools?.[0] && (
                          <Button
                            size="small"
                            onClick={() => observeOnly(test.productionTools[0])}
                            sx={{ color: "text.secondary" }}
                          >
                            Observe it only
                          </Button>
                        )}
                      <Button
                        size="small"
                        variant="outlined"
                        onClick={() => repair(r)}
                        sx={{ color: "text.primary", borderColor: "divider" }}
                      >
                        {r.repair.label}
                      </Button>
                    </Stack>
                  )}
                </Stack>
              );
            })}
          </Box>
        )}
      </SettingsSection>

      {/* What each agent family was tested against — untested is said. */}
      <Dialog
        open={versionsOpen}
        onClose={() => setVersionsOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle
          sx={{
            typography: "s1",
            fontWeight: 600,
            display: "flex",
            alignItems: "center",
          }}
        >
          <Box sx={{ flex: 1 }}>Tested versions</Box>
          <IconButton
            size="small"
            aria-label="Close"
            onClick={() => setVersionsOpen(false)}
          >
            <Iconify icon="solar:close-circle-linear" width={18} />
          </IconButton>
        </DialogTitle>
        <DialogContent>
          <Typography
            sx={{ typography: "s3", color: "text.secondary", mb: 1.5 }}
          >
            The agent families our connection paths were tested with, the
            evidence, and what isn&apos;t covered. A family not listed, or
            Untested, may still connect — its results just aren&apos;t
            certified.
          </Typography>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
            }}
          >
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "1.3fr 1.3fr 0.7fr 1.5fr 1.5fr",
                columnGap: 1.5,
                px: 1.5,
                py: 0.75,
              }}
            >
              {[
                "Family",
                "Tested versions",
                "Status",
                "Evidence",
                "Not covered",
              ].map((h) => (
                <Typography
                  key={h}
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.subtitle",
                  }}
                >
                  {h}
                </Typography>
              ))}
            </Box>
            {TESTED_VERSIONS.map((r) => (
              <Box
                key={r.family}
                sx={{
                  display: "grid",
                  gridTemplateColumns: "1.3fr 1.3fr 0.7fr 1.5fr 1.5fr",
                  columnGap: 1.5,
                  px: 1.5,
                  py: 0.875,
                }}
              >
                <Box>
                  <Typography sx={{ typography: "s2", fontWeight: 500 }}>
                    {r.family}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {r.how}
                  </Typography>
                </Box>
                <Typography sx={{ typography: "s3", fontFamily: MONO }}>
                  {r.versions}
                </Typography>
                <Typography
                  sx={{ typography: "s3", color: STATUS_TONE[r.status] }}
                >
                  {r.status}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {r.evidence}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {r.limits}
                </Typography>
              </Box>
            ))}
          </Box>
        </DialogContent>
      </Dialog>
    </>
  );
}

ConnectionCheckSection.propTypes = {
  agent: PropTypes.object.isRequired,
  /* (patch, message) — saves to the agent's values. */
  onSaveValues: PropTypes.func.isRequired,
};
