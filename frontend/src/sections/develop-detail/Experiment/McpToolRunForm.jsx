// MCP server tool run (options-truth L1 G5 C3): connect a server (stdio
// command or Streamable HTTP URL, plus an env secret), list its tools, pick
// one, map each argument of its JSON schema from a column, a constant or
// nothing, test one row with `tools/call`, then run: one call per row and try.
import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  CircularProgress,
  FormControlLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import axios from "src/utils/axios";
import RunCardShell from "./RunCardShell";
import { useExperimentForm } from "./ExperimentFormContext";
import { useCardValue } from "./cardWatch-g2";
import { isAutoMcpName, mcpRunName } from "./endpointCards-g2";
import { McpEnvField } from "src/sections/common/RunSources/endpoint/httpCardParts";
import StandInTag from "src/sections/common/RunSources/endpoint/StandInTag";
import { mcpEnvSecrets } from "src/sections/common/RunSources/endpoint/requestModel";

const LIST_TOOLS = "/model-hub/r4/mcp/list-tools/";
const CALL = "/model-hub/r4/mcp/call/";

const resultOf = (res) => res?.data?.result ?? null;
// The app's axios rejects with the response body spread (`e.result`).
const errorOf = (e) =>
  e?.result ?? e?.response?.data?.result ?? e?.message ?? "The request failed";

/**
 * Default argument mappings for a tool (pure): a property whose name is a
 * column name maps from that column; the rest are left empty.
 */
export function defaultArguments(toolDef, columns = []) {
  const props = Object.keys(toolDef?.inputSchema?.properties ?? {});
  return Object.fromEntries(
    props.map((p) => {
      const col = columns.find((c) => c.name === p);
      return [p, col ? { kind: "column", column: col.id } : { kind: "empty" }];
    }),
  );
}

const NUMERIC_COLUMN = /^(integer|int|float|number|numeric|decimal)$/i;
const isNumberType = (t) => t === "number" || t === "integer";

/**
 * `order_id is required` for every required argument left empty, and
 * L7c g2 (knob:mcp:arguments) `amount is a number: …` for a number argument
 * given text (a constant that is not a number, or a column that holds
 * text). Pure.
 * @param {object} toolDef
 * @param {object} args  `{name: {kind, value?, column?}}`
 * @param {{id, name, data_type?}[]} [columns]
 */
export function argumentIssues(toolDef, args = {}, columns = []) {
  const props = toolDef?.inputSchema?.properties ?? {};
  const missing = (toolDef?.inputSchema?.required ?? [])
    .filter((k) => {
      const m = args[k];
      if (!m || m.kind === "empty") return true;
      if (m.kind === "constant") return !String(m.value ?? "").trim();
      return !m.column;
    })
    .map((k) => `${k} is required`);
  const typed = Object.entries(args ?? {})
    .filter(([k]) => isNumberType(props[k]?.type))
    .map(([k, m]) => {
      if (m?.kind === "constant") {
        const v = String(m.value ?? "").trim();
        return v && !Number.isFinite(Number(v))
          ? `${k} is a number: "${v.slice(0, 30)}" is text`
          : null;
      }
      if (m?.kind === "column") {
        const col = (columns ?? []).find((c) => c.id === m.column);
        return col?.data_type && !NUMERIC_COLUMN.test(col.data_type)
          ? `${k} is a number: column ${col.name} holds ${col.data_type}`
          : null;
      }
      return null;
    })
    .filter(Boolean);
  return [...missing, ...typed];
}

const SIDE_EFFECTS = [
  { id: "real", label: "Real" },
  { id: "stubs", label: "Stubs" },
  { id: "dry_run", label: "Dry run" },
];
const sel = { bgcolor: "background.paper", minWidth: 0 };

export default function McpToolRunForm({
  control,
  setValue,
  fieldPrefix,
  letters = [],
  onRemove,
  showErrors = false,
}) {
  const { datasetId, mappingColumns, sampleRow } = useExperimentForm();
  const name = `${fieldPrefix}.mcp_tool`;
  const m = useCardValue(control, fieldPrefix, "mcp_tool") ?? {};
  const columns = useMemo(() => mappingColumns ?? [], [mappingColumns]);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const [test, setTest] = useState(null);
  const set = (patch) =>
    setValue(name, { ...m, ...patch }, { shouldDirty: true });
  const runName = useCardValue(control, fieldPrefix, "name");
  const envSecrets = mcpEnvSecrets(m.server ?? {});
  const wantName = mcpRunName(m.server?.name, m.tool, envSecrets);
  useEffect(() => {
    if (runName !== wantName && isAutoMcpName(runName))
      setValue(`${fieldPrefix}.name`, wantName, { shouldDirty: true });
  }, [runName, wantName, fieldPrefix, setValue]);
  const server = m.server ?? {
    transport: "stdio",
    command: "",
    url: "",
    env_secret: "",
  };
  const tools = m.tools ?? [];
  const toolDef = tools.find((t) => t.name === m.tool) ?? null;
  const issues = argumentIssues(toolDef, m.arguments, columns);
  // L7c g2 (knob:mcp:command-url): Connect with nothing to start says so
  // under the field, without a request.
  const [fieldErr, setFieldErr] = useState(null);
  const sideEffect = toolDef?.annotations?.destructiveHint === true;

  const connect = async ({ keepArgs = false } = {}) => {
    const http = server.transport === "http";
    if (!String((http ? server.url : server.command) ?? "").trim()) {
      setFieldErr(
        http
          ? "Enter the server URL, e.g. https://mcp.example.com/mcp"
          : "Enter the command that starts the server, e.g. acme-sandbox mcp --stdio",
      );
      return;
    }
    setFieldErr(null);
    setBusy("connect");
    setErr(null);
    setTest(null);
    try {
      const r = resultOf(await axios.post(LIST_TOOLS, server));
      const first = r?.tools?.[0] ?? null;
      const kept = r?.tools?.find((t) => t.name === m.tool) ?? null;
      const keep = kept ?? first;
      // L7b G2: a card rebuilt from its run keeps the tool and arguments it
      // ran with when the server still lists that tool.
      const keptArgs =
        kept && (keepArgs || Object.keys(m.arguments ?? {}).length)
          ? m.arguments ?? {}
          : null;
      set({
        server: {
          ...server,
          name: r?.server?.name,
          known: r?.server?.known ?? true,
        },
        tools: r?.tools ?? [],
        tool: keep?.name ?? null,
        arguments: keptArgs ?? (keep ? defaultArguments(keep, columns) : {}),
      });
    } catch (e) {
      setErr(errorOf(e));
    } finally {
      setBusy(null);
    }
  };

  // L7b G2 (Edit / Duplicate & edit): a card rebuilt from a run knows its
  // server and tool but not the server's tool list: list it once.
  const listed = useRef(false);
  useEffect(() => {
    if (listed.current || tools.length || !m.server?.name || !m.tool) return;
    listed.current = true;
    connect({ keepArgs: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [m.server?.name, m.tool, tools.length]);

  // L7c g2 (knob:mcp:transport): a different transport, command or URL is
  // another server: the card disconnects (no tools, no tool) until Connect.
  const changeServer = (patch) => {
    setTest(null);
    setFieldErr(null);
    const next = { ...server, ...patch };
    if (m.server?.name || tools.length)
      set({
        server: { ...next, name: undefined, known: undefined },
        tools: [],
        tool: null,
        arguments: {},
      });
    else set({ server: next });
  };

  const pickTool = (t) =>
    set({ tool: t.name, arguments: defaultArguments(t, columns) });
  const setArg = (k, v) =>
    set({ arguments: { ...(m.arguments ?? {}), [k]: v } });

  const runTest = async () => {
    setBusy("test");
    setErr(null);
    try {
      const r = resultOf(
        await axios.post(CALL, {
          server: { ...server, name: m.server?.name },
          tool: m.tool,
          arguments: m.arguments ?? {},
          side_effects: m.side_effects ?? "stubs",
          dataset_id: datasetId,
          row_id: sampleRow?.row_id ?? sampleRow?.id ?? null,
        }),
      );
      setTest(r);
    } catch (e) {
      setErr(errorOf(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <RunCardShell
      icon={
        <Iconify icon="mdi:tools" width={16} sx={{ color: "text.secondary" }} />
      }
      letters={letters}
      title={mcpRunName(m.server?.name, m.tool, envSecrets)}
      typeLabel="One tools/call per row"
      onRemove={onRemove}
      error={
        showErrors && !toolDef ? "Connect the server and pick a tool" : null
      }
    >
      <Stack spacing={1.25} data-testid="mcp-form">
        <Stack direction="row" gap={1} alignItems="center">
          <ToggleButtonGroup
            size="small"
            exclusive
            value={server.transport ?? "stdio"}
            onChange={(_, v) =>
              v && v !== server.transport && changeServer({ transport: v })
            }
          >
            <ToggleButton value="stdio" sx={{ typography: "s2", px: 1 }}>
              stdio command
            </ToggleButton>
            <ToggleButton value="http" sx={{ typography: "s2", px: 1 }}>
              Streamable HTTP
            </ToggleButton>
          </ToggleButtonGroup>
        </Stack>
        <Stack direction="row" gap={1} alignItems="flex-start">
          <TextField
            size="small"
            fullWidth
            label={server.transport === "http" ? "Server URL" : "Command"}
            placeholder={
              server.transport === "http"
                ? "https://mcp.example.com/mcp"
                : "acme-sandbox mcp --stdio"
            }
            value={
              (server.transport === "http" ? server.url : server.command) ?? ""
            }
            onChange={(e) =>
              changeServer({
                [server.transport === "http" ? "url" : "command"]:
                  e.target.value,
              })
            }
            error={Boolean(fieldErr)}
            helperText={fieldErr}
            inputProps={{ "data-testid": "mcp-command" }}
            sx={sel}
          />
          <Button
            variant="outlined"
            size="small"
            onClick={() => connect()}
            disabled={busy === "connect"}
            data-testid="mcp-connect"
            sx={{ height: 40, flexShrink: 0 }}
          >
            {busy === "connect" ? <CircularProgress size={14} /> : "Connect"}
          </Button>
        </Stack>
        <McpEnvField
          server={server}
          onChange={(env) =>
            set({ server: { ...server, env, env_secret: undefined } })
          }
        />
        {tools.length ? (
          <Box data-testid="mcp-tools">
            <Typography typography="s2" fontWeight="fontWeightMedium">
              {m.server?.known === false || tools.some((t) => t.example)
                ? `Example tools (server not recognised) · ${m.server?.name} · ${tools.length}`
                : `${m.server?.name} · ${tools.length} tools`}
            </Typography>
            {m.server?.known === false || tools.some((t) => t.example) ? (
              <Typography
                typography="s3"
                color="text.secondary"
                data-testid="mcp-example-note"
              >
                {`Recognised servers: acme-support-sandbox and github. ${m.server?.name} is not one, so these are example tools named after it; the run calls them as listed.`}
              </Typography>
            ) : null}
            <Box
              sx={{
                mt: 0.5,
                display: "grid",
                gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                gap: 0.5,
              }}
            >
              {tools.map((t) => (
                <Stack
                  key={t.name}
                  direction="row"
                  gap={0.5}
                  alignItems="center"
                  role="button"
                  tabIndex={0}
                  onClick={() => pickTool(t)}
                  data-testid={`mcp-tool-${t.name}`}
                  sx={{
                    cursor: "pointer",
                    px: 0.5,
                    borderRadius: 0.5,
                    bgcolor: m.tool === t.name ? "purple.o5" : "transparent",
                    minWidth: 0,
                  }}
                >
                  <Radio
                    size="small"
                    checked={m.tool === t.name}
                    sx={{ p: 0.25 }}
                  />
                  <Typography typography="s2" noWrap>
                    {`${t.name}(${Object.keys(t.inputSchema?.properties ?? {}).join(", ")})`}
                  </Typography>
                </Stack>
              ))}
            </Box>
          </Box>
        ) : null}
        {toolDef ? (
          <Stack spacing={0.75} data-testid="mcp-args">
            <Typography typography="s2" fontWeight="fontWeightMedium">
              {`Arguments of ${toolDef.name}`}
            </Typography>
            {Object.entries(toolDef.inputSchema?.properties ?? {}).map(
              ([k, p]) => {
                const a = m.arguments?.[k] ?? { kind: "empty" };
                const req = (toolDef.inputSchema?.required ?? []).includes(k);
                return (
                  <Stack key={k} direction="row" gap={1} alignItems="center">
                    <Typography
                      typography="s2"
                      sx={{ width: 130, flexShrink: 0 }}
                      noWrap
                    >
                      {`${k}${req ? " *" : ""}`}
                      <Typography
                        component="span"
                        typography="s3"
                        color="text.secondary"
                      >
                        {` ${p.type}`}
                      </Typography>
                    </Typography>
                    <TextField
                      select
                      size="small"
                      value={a.kind === "column" ? `col:${a.column}` : a.kind}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === "empty") setArg(k, { kind: "empty" });
                        else if (v === "constant")
                          setArg(k, { kind: "constant", value: "" });
                        else setArg(k, { kind: "column", column: v.slice(4) });
                      }}
                      inputProps={{ "data-testid": `mcp-arg-${k}` }}
                      sx={{ ...sel, width: 220 }}
                    >
                      {columns.map((c) => (
                        <MenuItem key={c.id} value={`col:${c.id}`}>
                          {`Column · ${c.name}`}
                        </MenuItem>
                      ))}
                      <MenuItem value="constant">Constant</MenuItem>
                      <MenuItem value="empty">Leave empty</MenuItem>
                    </TextField>
                    {a.kind === "constant" ? (
                      <TextField
                        size="small"
                        placeholder={p.description}
                        value={a.value ?? ""}
                        onChange={(e) =>
                          setArg(k, { kind: "constant", value: e.target.value })
                        }
                        sx={{ ...sel, flex: 1 }}
                      />
                    ) : (
                      <Typography typography="s3" color="text.secondary" noWrap>
                        {p.description}
                      </Typography>
                    )}
                  </Stack>
                );
              },
            )}
            {issues.length ? (
              <Typography
                typography="s2"
                color="warning.dark"
                data-testid="mcp-arg-issues"
              >
                {`${issues.join(" · ")}: each row returns isError until it is fixed.`}
              </Typography>
            ) : null}
            {sideEffect ? (
              <Stack
                direction="row"
                gap={1}
                alignItems="center"
                data-testid="mcp-side-effects"
              >
                <Typography typography="s2">{`${toolDef.name} changes data:`}</Typography>
                <RadioGroup
                  row
                  value={m.side_effects ?? "stubs"}
                  onChange={(e) => set({ side_effects: e.target.value })}
                >
                  {SIDE_EFFECTS.map((s) => (
                    <FormControlLabel
                      key={s.id}
                      value={s.id}
                      control={<Radio size="small" sx={{ p: 0.5 }} />}
                      label={s.label}
                      sx={{
                        "& .MuiFormControlLabel-label": { typography: "s2" },
                      }}
                    />
                  ))}
                </RadioGroup>
              </Stack>
            ) : null}
            <Stack direction="row" gap={1} alignItems="center">
              <Button
                size="small"
                variant="outlined"
                onClick={runTest}
                disabled={busy === "test"}
                data-testid="mcp-test"
                startIcon={<Iconify icon="mdi:play-outline" width={14} />}
              >
                Test on one row
              </Button>
              <Typography typography="s3" color="text.secondary">
                Sends tools/call for the sample row.
              </Typography>
            </Stack>
            {test ? (
              <Box
                data-testid="mcp-test-result"
                sx={{ bgcolor: "background.neutral", borderRadius: 1, p: 1 }}
              >
                <Stack
                  direction="row"
                  gap={1}
                  alignItems="center"
                  flexWrap="wrap"
                >
                  <Typography typography="s3" color="text.secondary">
                    {`Row ${test.row_no ?? 1} · tools/call ${test.request.name}(${JSON.stringify(test.request.arguments)}) · ${test.result.latency_ms} ms${test.result.isError ? " · isError" : ""}`}
                  </Typography>
                  <StandInTag
                    who={m.server?.name ?? server.url ?? server.command}
                    testId="mcp-test-simulated"
                  />
                </Stack>
                <Typography
                  typography="s2"
                  color={test.result.isError ? "error.main" : "text.primary"}
                  sx={{
                    mt: 0.5,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                  }}
                >
                  {test.result.content?.map((c) => c.text).join("\n")}
                </Typography>
              </Box>
            ) : null}
          </Stack>
        ) : null}
        {err ? (
          <Typography typography="s2" color="error.main">
            {err}
          </Typography>
        ) : null}
      </Stack>
    </RunCardShell>
  );
}

McpToolRunForm.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
  fieldPrefix: PropTypes.string.isRequired,
  letters: PropTypes.arrayOf(PropTypes.string),
  onRemove: PropTypes.func,
  showErrors: PropTypes.bool,
};
