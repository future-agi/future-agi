import PropTypes from "prop-types";
import { Fragment, useEffect, useMemo, useState } from "react";
import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import CopyCommandButton from "../components/CopyCommandButton";
import { StatusDot } from "../components/primitives";
import { segmentedToggleSx } from "../agents/segmentedToggleSx";
import { pendingEnvChanges } from "../_mock/versions";
import {
  PREVIEWS,
  callToolPatch,
  newPreview,
  previewCli,
  previewFilesOf,
  resourcesOf,
  sampleArgsOf,
  sayPatch,
  snapshotPatch,
  snapshotSupportOf,
  startPreview,
  startingPointsOf,
  stopPreview,
} from "../_mock/sandboxPreview";

const MONO = "ui-monospace, Menlo, monospace";
const PANELS = ["State", "Files", "Logs", "Network", "Resources"];
const head = {
  typography: "s3",
  color: "text.tertiary",
  fontWeight: 500,
  py: 0.5,
  px: 1,
  bgcolor: "transparent",
  whiteSpace: "nowrap",
};
const cell = {
  py: 0.5,
  px: 1,
  borderColor: "divider",
  typography: "s3",
  fontFamily: MONO,
  whiteSpace: "nowrap",
};
const clock = (ms) =>
  `${Math.floor(ms / 60000)}:${String(Math.floor((ms % 60000) / 1000)).padStart(2, "0")}`;
const time = (iso) =>
  new Date(iso).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });

/**
 * Sandbox preview (roadmap builder.md): start the world on a starting state,
 * act as the agent — call a tool or say something — and watch its state,
 * files, logs, network and resources. A preview, not a result: it isn't a
 * run, isn't scored and never touches completed runs.
 */
export default function SandboxPreviewDrawer({
  open,
  onClose,
  env,
  envState,
  patch,
  locked,
}) {
  const [p, setP] = useState(
    () => PREVIEWS.get(env?.id) || newPreview(env, envState),
  );
  const [panel, setPanel] = useState("State");
  const [mode, setMode] = useState("tool");
  const tools = env?.tools || [];
  const [toolName, setToolName] = useState(tools[0]?.name || "");
  const [args, setArgs] = useState(() =>
    JSON.stringify(sampleArgsOf(tools[0]), null, 2),
  );
  const [say, setSay] = useState("");
  const [argsError, setArgsError] = useState(null);
  const [resetting, setResetting] = useState(null);
  const [openReq, setOpenReq] = useState(null);
  const [, tick] = useState(0);

  /* Kept for the session, so closing the drawer doesn't lose the preview. */
  useEffect(() => {
    if (env?.id) PREVIEWS.set(env.id, p);
  }, [env?.id, p]);
  useEffect(() => {
    if (!open || p.status !== "running") return undefined;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [open, p.status]);

  const points = useMemo(
    () => startingPointsOf(env, envState),
    [env, envState],
  );
  const snap = snapshotSupportOf(env, envState);
  const files = previewFilesOf(envState);
  const res = resourcesOf(p);
  const unsaved = pendingEnvChanges(envState).length;
  const persona = (envState?.scenarios || []).find((s) => s.persona?.name)
    ?.persona?.name;
  const tool = tools.find((t) => t.name === toolName);
  const dirty = p.changed.length > 0 || p.log.some((l) => l.who !== "Sandbox");

  if (!open) return <SideDrawer open={false} onClose={onClose} />;

  const pickTool = (name) => {
    setToolName(name);
    setArgs(
      JSON.stringify(sampleArgsOf(tools.find((t) => t.name === name)), null, 2),
    );
    setArgsError(null);
  };
  const send = () => {
    let body;
    try {
      body = JSON.parse(args);
    } catch {
      setArgsError("That isn't valid JSON. Nothing was sent.");
      return;
    }
    setArgsError(null);
    setP((cur) => callToolPatch(cur, { env, envState, tool, args: body }));
  };
  const reset = (pointId) => setP(newPreview(env, envState, pointId));
  const askReset = (pointId) =>
    dirty ? setResetting({ pointId }) : reset(pointId);

  return (
    <SideDrawer open onClose={onClose} width={960}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1}
          sx={{
            px: 2,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m3", fontWeight: 600 }}>
              Sandbox preview
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {`${env?.name} · a preview, not a result. It isn't a run, isn't scored, and completed runs aren't touched.`}
            </Typography>
          </Box>
          <IconButton size="small" aria-label="Close" onClick={onClose}>
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>

        {/* Start, stop, reset, snapshot — and where it starts from. */}
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2,
            py: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexWrap: "wrap",
            rowGap: 1,
          }}
        >
          <StatusDot
            status={p.status === "running" ? "passed" : "cancelled"}
            size={8}
            live={p.status === "running"}
          />
          <Typography sx={{ typography: "s2", fontWeight: 600, width: 112 }}>
            {p.status === "running"
              ? `Running · ${clock(res.elapsed)}`
              : "Stopped"}
          </Typography>
          <Select
            size="small"
            value={p.pointId}
            onChange={(e) => askReset(e.target.value)}
            sx={{ minWidth: 280, maxWidth: 360, fontSize: 13 }}
          >
            {points.map((pt) => (
              <MenuItem key={pt.id} value={pt.id} sx={{ fontSize: 13 }}>
                {pt.label}
              </MenuItem>
            ))}
          </Select>
          <Box flex={1} />
          {p.status === "running" ? (
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              onClick={() => setP(stopPreview)}
              startIcon={<Iconify icon="solar:stop-linear" width={14} />}
            >
              Stop
            </Button>
          ) : (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => setP(startPreview)}
              startIcon={<Iconify icon="solar:play-linear" width={14} />}
            >
              {p.log.length ? "Resume" : "Start"}
            </Button>
          )}
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => askReset(p.pointId)}
            startIcon={<Iconify icon="solar:restart-linear" width={14} />}
          >
            Reset
          </Button>
          <Tooltip
            arrow
            title={snap.ok ? "Save this state as a starting point" : snap.why}
          >
            <span>
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                disabled={!snap.ok || locked || !patch || !p.changed.length}
                onClick={() => {
                  const name = `after ${p.changed[p.changed.length - 1]?.key || "preview"}`;
                  patch(snapshotPatch(envState, p, name));
                }}
                startIcon={<Iconify icon="solar:camera-linear" width={14} />}
              >
                Snapshot
              </Button>
            </span>
          </Tooltip>
          <CopyCommandButton command={previewCli(env, p.pointId)} label="CLI" />
        </Stack>
        {unsaved > 0 && (
          <Typography
            sx={{
              typography: "s3",
              color: "text.secondary",
              px: 2,
              py: 0.75,
              borderBottom: "1px solid",
              borderColor: "divider",
              bgcolor: "background.neutral",
            }}
          >
            {`Uses the world as it is now, including ${unsaved} unsaved edit${unsaved === 1 ? "" : "s"}. Runs still use the saved version.`}
          </Typography>
        )}

        <Stack direction="row" sx={{ flex: 1, minHeight: 0 }}>
          {/* The agent's side. */}
          <Stack
            sx={{
              width: 380,
              flexShrink: 0,
              borderRight: "1px solid",
              borderColor: "divider",
              minHeight: 0,
            }}
          >
            <Box
              sx={{ p: 1.5, borderBottom: "1px solid", borderColor: "divider" }}
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ mb: 1 }}
              >
                <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
                  Act as the agent
                </Typography>
                <ToggleButtonGroup
                  exclusive
                  size="small"
                  value={mode}
                  onChange={(_, v) => v && setMode(v)}
                  sx={segmentedToggleSx}
                >
                  <ToggleButton value="tool">Call a tool</ToggleButton>
                  <ToggleButton value="say">Say something</ToggleButton>
                </ToggleButtonGroup>
              </Stack>
              {mode === "tool" ? (
                <Stack spacing={1}>
                  <Select
                    size="small"
                    value={toolName}
                    onChange={(e) => pickTool(e.target.value)}
                    sx={{ fontFamily: MONO, fontSize: 12 }}
                  >
                    {tools.map((t) => (
                      <MenuItem
                        key={t.name}
                        value={t.name}
                        sx={{ fontFamily: MONO, fontSize: 12 }}
                      >
                        {t.name}
                      </MenuItem>
                    ))}
                  </Select>
                  <TextField
                    multiline
                    minRows={3}
                    maxRows={8}
                    value={args}
                    onChange={(e) => setArgs(e.target.value)}
                    error={!!argsError}
                    helperText={argsError}
                    inputProps={{ style: { fontFamily: MONO, fontSize: 12 } }}
                  />
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Button
                      size="small"
                      variant="contained"
                      color="primary"
                      disabled={p.status !== "running" || !tool}
                      onClick={send}
                    >
                      Send request
                    </Button>
                    {p.pending && p.pending.tool === toolName && (
                      <Button
                        size="small"
                        color="inherit"
                        onClick={() =>
                          setArgs(
                            JSON.stringify(
                              {
                                ...p.pending.args,
                                approval_id: `AP-${p.calls}`,
                              },
                              null,
                              2,
                            ),
                          )
                        }
                      >
                        Add the approval
                      </Button>
                    )}
                    {p.status !== "running" && (
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        Start the preview first
                      </Typography>
                    )}
                  </Stack>
                </Stack>
              ) : (
                <Stack spacing={1}>
                  <TextField
                    multiline
                    minRows={2}
                    placeholder={`What the agent says to ${persona || "the caller"}`}
                    value={say}
                    onChange={(e) => setSay(e.target.value)}
                  />
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    disabled={p.status !== "running" || !say.trim()}
                    onClick={() => {
                      setP((cur) =>
                        sayPatch(cur, { text: say.trim(), persona }),
                      );
                      setSay("");
                    }}
                    sx={{ alignSelf: "flex-start" }}
                  >
                    Send
                  </Button>
                </Stack>
              )}
            </Box>
            <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 1.5 }}>
              {!p.log.length ? (
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  Start the preview, then call a tool or say something. What the
                  world does shows here and on the right.
                </Typography>
              ) : (
                <Stack spacing={1}>
                  {p.log.map((l, i) => (
                    <Box key={i}>
                      <Typography
                        sx={{
                          typography: "s3",
                          color:
                            l.tone === "actor"
                              ? "text.primary"
                              : "text.tertiary",
                          fontWeight: l.tone === "actor" ? 600 : 400,
                        }}
                      >
                        {`${l.who} · ${time(l.at)}`}
                      </Typography>
                      <Typography
                        sx={{
                          typography: "s2",
                          color:
                            l.tone === "warn"
                              ? "warning.dark"
                              : "text.secondary",
                        }}
                      >
                        {l.text}
                      </Typography>
                    </Box>
                  ))}
                </Stack>
              )}
            </Box>
          </Stack>

          {/* What the world shows. */}
          <Stack sx={{ flex: 1, minWidth: 0, minHeight: 0 }}>
            <Box sx={{ px: 1.5, pt: 1.25 }}>
              <ToggleButtonGroup
                exclusive
                size="small"
                value={panel}
                onChange={(_, v) => v && setPanel(v)}
                sx={segmentedToggleSx}
              >
                {PANELS.map((x) => (
                  <ToggleButton key={x} value={x}>
                    {x === "Network" && p.network.length
                      ? `Network · ${p.network.length}`
                      : x === "State" && p.changed.length
                        ? `State · ${p.changed.length} changed`
                        : x}
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>
            </Box>
            <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 1.5 }}>
              {panel === "State" &&
                Object.entries(p.tables).map(([name, rows]) => {
                  const cols = [
                    ...new Set(rows.flatMap((r) => Object.keys(r))),
                  ];
                  return (
                    <Box key={name} sx={{ mb: 2 }}>
                      <Typography
                        sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}
                      >{`${name} · ${rows.length}`}</Typography>
                      {rows.length ? (
                        <Table size="small">
                          <TableHead>
                            <TableRow>
                              {cols.map((c) => (
                                <TableCell key={c} sx={head}>
                                  {c}
                                </TableCell>
                              ))}
                              <TableCell sx={head} />
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {rows.map((r, i) => {
                              const ch = p.changed
                                .filter(
                                  (c) =>
                                    c.table === name &&
                                    Object.values(r).includes(c.key),
                                )
                                .pop();
                              return (
                                <TableRow
                                  key={i}
                                  sx={{
                                    bgcolor: ch
                                      ? "action.selected"
                                      : "transparent",
                                  }}
                                >
                                  {cols.map((c) => (
                                    <TableCell key={c} sx={cell}>
                                      {String(r[c] ?? "")}
                                    </TableCell>
                                  ))}
                                  <TableCell
                                    sx={{
                                      ...cell,
                                      fontFamily: "inherit",
                                      color: "text.secondary",
                                    }}
                                  >
                                    {ch ? `${ch.change} in this preview` : ""}
                                  </TableCell>
                                </TableRow>
                              );
                            })}
                          </TableBody>
                        </Table>
                      ) : (
                        <Typography
                          sx={{ typography: "s3", color: "text.tertiary" }}
                        >
                          Empty
                        </Typography>
                      )}
                    </Box>
                  );
                })}
              {panel === "Files" &&
                (files.length ? (
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell sx={head}>File</TableCell>
                        <TableCell sx={head}>Used as</TableCell>
                        <TableCell sx={head}>The agent</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {files.map((f) => (
                        <TableRow key={f.name}>
                          <TableCell sx={cell}>{f.name}</TableCell>
                          <TableCell sx={{ ...cell, fontFamily: "inherit" }}>
                            {f.role}
                          </TableCell>
                          <TableCell
                            sx={{
                              ...cell,
                              fontFamily: "inherit",
                              color: f.agentSees
                                ? "text.primary"
                                : "text.tertiary",
                            }}
                          >
                            {f.agentSees
                              ? "Can read it"
                              : "Can't see it (evaluator only)"}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    No files in this environment. Attach policies or records in
                    the builder chat.
                  </Typography>
                ))}
              {panel === "Logs" &&
                (p.log.length ? (
                  <Box
                    component="pre"
                    sx={{
                      m: 0,
                      fontFamily: MONO,
                      fontSize: 12,
                      lineHeight: "18px",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {p.log
                      .map(
                        (l) => `${time(l.at)}  ${l.who.padEnd(18)} ${l.text}`,
                      )
                      .join("\n")}
                  </Box>
                ) : (
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    Nothing yet.
                  </Typography>
                ))}
              {panel === "Network" &&
                (p.network.length ? (
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell sx={head}>Time</TableCell>
                        <TableCell sx={head}>Request</TableCell>
                        <TableCell sx={head}>Status</TableCell>
                        <TableCell sx={{ ...head, textAlign: "right" }}>
                          ms
                        </TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {p.network.map((r, i) => (
                        <Fragment key={i}>
                          <TableRow
                            hover
                            onClick={() => setOpenReq(openReq === i ? null : i)}
                            sx={{ cursor: "pointer" }}
                          >
                            <TableCell sx={cell}>{time(r.at)}</TableCell>
                            <TableCell
                              sx={cell}
                            >{`${r.method} ${r.path}`}</TableCell>
                            <TableCell
                              sx={{
                                ...cell,
                                color:
                                  typeof r.status === "number" &&
                                  r.status >= 400
                                    ? "error.main"
                                    : "text.primary",
                              }}
                            >
                              {r.status}
                              {r.note && (
                                <Box
                                  component="span"
                                  sx={{
                                    fontFamily: "inherit",
                                    color: "text.tertiary",
                                  }}
                                >{` · ${r.note}`}</Box>
                              )}
                            </TableCell>
                            <TableCell sx={{ ...cell, textAlign: "right" }}>
                              {r.ms}
                            </TableCell>
                          </TableRow>
                          {openReq === i && (
                            <TableRow>
                              <TableCell
                                colSpan={4}
                                sx={{
                                  ...cell,
                                  whiteSpace: "pre-wrap",
                                  color: "text.secondary",
                                }}
                              >
                                {`request  ${JSON.stringify(r.body)}\nresponse ${JSON.stringify(r.response)}`}
                              </TableCell>
                            </TableRow>
                          )}
                        </Fragment>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    No requests yet.
                  </Typography>
                ))}
              {panel === "Resources" && (
                <Box
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "8px",
                  }}
                >
                  {[
                    ["Running for", clock(res.elapsed)],
                    ["Tool calls", res.calls],
                    ["Tokens", res.tokens.toLocaleString()],
                    ["Memory", `${res.memoryMb} MB of 1 GB`],
                    [
                      "Preview cost",
                      `≈ ${res.credits} credits. Previews aren't runs and don't count toward run limits.`,
                    ],
                  ].map(([k, v]) => (
                    <Stack
                      key={k}
                      direction="row"
                      spacing={1.5}
                      sx={{
                        px: 1.5,
                        py: 0.75,
                        "& + &": {
                          borderTop: "1px solid",
                          borderColor: "divider",
                        },
                      }}
                    >
                      <Typography
                        sx={{
                          typography: "s3",
                          color: "text.tertiary",
                          width: 120,
                        }}
                      >
                        {k}
                      </Typography>
                      <Typography
                        sx={{
                          typography: "s2",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {v}
                      </Typography>
                    </Stack>
                  ))}
                </Box>
              )}
            </Box>
          </Stack>
        </Stack>
      </Stack>

      <ConfirmDialog
        open={!!resetting}
        onClose={() => setResetting(null)}
        title="Reset the preview?"
        content={`This discards ${p.changed.length} change${p.changed.length === 1 ? "" : "s"} to the state and the conversation, and restores ${points.find((x) => x.id === resetting?.pointId)?.label?.toLowerCase() || "the starting state"}. Completed runs aren't touched.`}
        action={
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => {
              reset(resetting.pointId);
              setResetting(null);
            }}
          >
            Reset
          </Button>
        }
      />
    </SideDrawer>
  );
}

SandboxPreviewDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  locked: PropTypes.bool,
};
