import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  CircularProgress,
  Collapse,
  ButtonBase,
  Drawer,
  IconButton,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useAuthContext } from "src/auth/hooks";
import { ROLES } from "src/utils/rolePermissionMapping";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import { useChangeSet } from "../DataTab/Versions/changeSetStore";
import { environmentChangeText } from "../DataTab/Versions/versionText";
import { useDatasetEnvironment } from "../DataTab/DatapointDrawerV2/itemHooks";

const MONO = { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" };
const MONO_INPUT = { ...MONO, fontSize: 13 };
/** What runs do with the environment (the run engine's real behaviour). */
const ENVIRONMENT_LINE =
  "Each try starts a fresh sandbox seeded from its task's start_state: read tools answer from it and edit tools change it.";

/** A section title; `hint` is its info tooltip (keeps the drawer calm). */
const Section = ({ title, hint, children, right }) => (
  <Box>
    <Stack direction="row" alignItems="center" gap={0.75} sx={{ mb: 0.75 }}>
      <Typography typography="s1" fontWeight={600}>
        {title}
      </Typography>
      {hint && (
        <CustomTooltip show size="small" placement="top" arrow title={hint}>
          <Iconify
            icon="mdi:information-outline"
            width={14}
            sx={{ color: "text.disabled" }}
          />
        </CustomTooltip>
      )}
      <Box sx={{ flex: 1 }} />
      {right}
    </Stack>
    {children}
  </Box>
);
Section.propTypes = {
  title: PropTypes.node,
  hint: PropTypes.node,
  children: PropTypes.node,
  right: PropTypes.node,
};

/**
 * v2.1 g4: the tools a Test connection lists for the typed address: the
 * saved server's when the address (URL or command) is the one it serves,
 * none otherwise. Pure.
 */
export function connectionToolsOf(mcp, mode, address) {
  const want = String(address ?? "").trim();
  if (!want) return [];
  const served = String(
    (mode === "url" ? mcp?.url : mcp?.command) ?? "",
  ).trim();
  return served && served === want ? mcp?.tool_names ?? [] : [];
}

/**
 * v2.1 g4: the MCP fields of an address no server answers at: no tools
 * (Test connection finds none there). Pure.
 */
export const environmentMcpWithout = () => ({ tool_names: [], tools: 0 });

const blank = (env) => ({
  image: env?.image ?? "",
  mcpMode: env?.mcp?.url ? "url" : "command",
  url: env?.mcp?.url ?? "",
  command: env?.mcp?.command ?? "",
  reset: env?.reset_command ?? "",
});

/**
 * The task suite's environment (R2-SPEC §5.3.8 b): image and resources, the
 * sandbox's MCP server (URL or command) with `Test connection`, the
 * start-state schema and reset command, secret names. A change is a
 * pending change (`Environment acme-support-sandbox:1.4 → 1.5`) saved
 * with the next version. 560 px.
 *
 * Props (frozen, R2 manifest row 28): open, onClose, datasetId, rowId.
 */
const EnvironmentDrawer = ({ open, onClose, datasetId }) => {
  const { saved, environment, pendingOp } = useDatasetEnvironment(datasetId);
  const changeSet = useChangeSet(datasetId);
  const list = useDevelopDatasetList();
  const { role } = useAuthContext();
  const readOnly = role === ROLES.VIEWER || role === ROLES.WORKSPACE_VIEWER;
  const [form, setForm] = useState(blank(environment));
  const [test, setTest] = useState({ state: "idle", tools: [] });
  const [schemaOpen, setSchemaOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    setTest({ state: "idle", tools: [] });
    // A saved version may have changed the environment: read it again.
    list.refetch?.();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const envKey = JSON.stringify(environment ?? null);
  useEffect(() => {
    if (open) setForm(blank(environment));
  }, [open, envKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const base = blank(environment);
  const dirty = JSON.stringify(form) !== JSON.stringify(base);
  const imageError = !/^[\w.-]+(\/[\w.-]+)*:[\w.-]+$/.test(form.image.trim())
    ? "Use registry/name:tag"
    : null;
  const schema = environment?.start_state_schema ?? null;
  const tables = useMemo(
    () =>
      Object.entries(schema?.properties ?? {}).map(([k, v]) => ({
        key: k,
        type: v?.type ?? "any",
        required: (schema?.required ?? []).includes(k),
      })),
    [schema],
  );
  const r = environment?.resources ?? {};

  // v2.1 g4 (task:environment, create:task-suite): Test connection checks
  // the address typed here. The environment's saved MCP server answers
  // with its tools; any other address has no server behind it.
  const typedAddress = (
    form.mcpMode === "url" ? form.url : form.command
  ).trim();
  const testConnection = async () => {
    setTest({ state: "testing", tools: [], address: typedAddress });
    const started = Date.now();
    const res = await list.refetch?.().catch(() => null);
    const fresh = (res?.data ?? []).find((d) => d.datasetId === datasetId);
    const mcp = fresh?.environment?.mcp ?? environment?.mcp ?? {};
    const tools = connectionToolsOf(mcp, form.mcpMode, typedAddress);
    await new Promise((ok) =>
      setTimeout(ok, Math.max(0, 700 - (Date.now() - started))),
    );
    setTest(
      tools.length
        ? { state: "ok", tools, address: typedAddress, server: mcp.name }
        : { state: "error", tools: [], address: typedAddress },
    );
  };

  // v2.1 g4 (task:environment): Apply agrees with Test connection: an
  // address no MCP server answers at is saved with no tools, so runs on it
  // call none (never the old server's tools under a new address).
  const servedTools = connectionToolsOf(
    environment?.mcp ?? {},
    form.mcpMode,
    typedAddress,
  );
  const addressChanged =
    typedAddress !==
    String(
      (form.mcpMode === "url"
        ? environment?.mcp?.url
        : environment?.mcp?.command) ?? "",
    ).trim();
  const apply = () => {
    const patch = {
      image: form.image.trim(),
      mcp: {
        ...(environment?.mcp ?? {}),
        ...(form.mcpMode === "url"
          ? { url: form.url.trim(), command: undefined }
          : { command: form.command.trim(), url: undefined }),
        ...(addressChanged ? environmentMcpWithout() : {}),
      },
      reset_command: form.reset.trim(),
    };
    changeSet.addOp({ op: "set_environment", ...patch, before: saved });
    enqueueSnackbar(environmentChangeText(saved?.image, patch.image), {
      variant: "success",
    });
  };

  return (
    <Drawer
      anchor="right"
      open={Boolean(open)}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: 560,
          maxWidth: "100vw",
          display: "flex",
          flexDirection: "column",
        },
      }}
      data-testid="environment-drawer"
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 2.5,
          py: 1.75,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon="mdi:docker"
          width={20}
          sx={{ color: "text.secondary" }}
        />
        <Box sx={{ minWidth: 0 }}>
          <Typography typography="m3" fontWeight={600}>
            Environment
          </Typography>
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="environment-line"
          >
            {ENVIRONMENT_LINE}
          </Typography>
        </Box>
        <Box sx={{ flex: 1 }} />
        <IconButton size="small" aria-label="Close" onClick={onClose}>
          <Iconify icon="mdi:close" width={18} />
        </IconButton>
      </Stack>

      {!environment ? (
        <Stack sx={{ p: 3 }} gap={1}>
          <Typography typography="s1">No environment yet.</Typography>
          <Typography typography="s2" color="text.secondary">
            Add the sandbox image your tasks run in.
          </Typography>
        </Stack>
      ) : (
        <Stack gap={2.5} sx={{ px: 2.5, py: 2, flex: 1, overflowY: "auto" }}>
          <Section
            title="Image"
            hint="Every attempt starts a fresh container from this image."
          >
            <TextField
              size="small"
              fullWidth
              value={form.image}
              disabled={readOnly}
              onChange={(e) =>
                setForm((f) => ({ ...f, image: e.target.value }))
              }
              error={Boolean(imageError)}
              helperText={
                imageError ??
                `Resources ${r.cpu ?? "—"} CPU · ${r.memory_gb ?? "—"} GB${r.timeout_s ? ` · ${r.timeout_s} s per attempt` : ""}`
              }
              inputProps={{ "aria-label": "Image", style: MONO_INPUT }}
            />
          </Section>

          <Section
            title="Tools (MCP)"
            hint={`The sandbox's MCP server${environment?.mcp?.name ? ` (${environment.mcp.name})` : ""}: a URL or a command.`}
            right={
              <ToggleButtonGroup
                size="small"
                exclusive
                value={form.mcpMode}
                disabled={readOnly}
                onChange={(_, v) => v && setForm((f) => ({ ...f, mcpMode: v }))}
              >
                <ToggleButton
                  value="url"
                  sx={{ textTransform: "none", py: 0.25, typography: "s3" }}
                >
                  Server URL
                </ToggleButton>
                <ToggleButton
                  value="command"
                  sx={{ textTransform: "none", py: 0.25, typography: "s3" }}
                >
                  Command
                </ToggleButton>
              </ToggleButtonGroup>
            }
          >
            <Stack direction="row" gap={1} alignItems="flex-start">
              <TextField
                size="small"
                fullWidth
                disabled={readOnly}
                value={form.mcpMode === "url" ? form.url : form.command}
                placeholder={
                  form.mcpMode === "url"
                    ? "https://sandbox.acme.ai/mcp"
                    : "python -m acme_sandbox.mcp"
                }
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    [f.mcpMode === "url" ? "url" : "command"]: e.target.value,
                  }))
                }
                inputProps={{
                  "aria-label":
                    form.mcpMode === "url" ? "Server URL" : "Command",
                  style: MONO_INPUT,
                }}
              />
              <Button
                size="small"
                variant="outlined"
                onClick={testConnection}
                disabled={test.state === "testing"}
                data-testid="env-test-connection"
                startIcon={
                  test.state === "testing" ? (
                    <CircularProgress size={12} />
                  ) : (
                    <Iconify icon="mdi:connection" width={16} />
                  )
                }
                sx={{ whiteSpace: "nowrap", height: 40, flexShrink: 0 }}
              >
                Test connection
              </Button>
            </Stack>
            {test.state === "ok" && (
              <Box sx={{ mt: 1 }} data-testid="env-tools">
                <Stack
                  direction="row"
                  alignItems="center"
                  gap={0.5}
                  sx={{ mb: 0.75 }}
                >
                  <Iconify
                    icon="mdi:check-circle"
                    width={16}
                    sx={{ color: "green.600" }}
                  />
                  <Typography typography="s2" fontWeight={600}>
                    {test.tools.length} tools found
                  </Typography>
                  <Typography typography="s3" color="text.secondary">
                    {`· ${test.server ?? "MCP server"} at ${test.address}`}
                  </Typography>
                </Stack>
                <Stack direction="row" flexWrap="wrap" gap={0.5}>
                  {test.tools.map((t) => (
                    <Box
                      key={t}
                      component="span"
                      sx={{
                        ...MONO,
                        typography: "s3",
                        px: 0.75,
                        height: 22,
                        display: "inline-flex",
                        alignItems: "center",
                        borderRadius: 0.5,
                        border: "1px solid",
                        borderColor: "divider",
                      }}
                    >
                      {t}
                    </Box>
                  ))}
                </Stack>
              </Box>
            )}
            {test.state === "error" && (
              <Typography typography="s2" color="red.600" sx={{ mt: 1 }}>
                {test.address
                  ? `No MCP server answered at ${test.address}.`
                  : "Enter the server's URL or command first."}
              </Typography>
            )}
            {addressChanged && typedAddress && !servedTools.length ? (
              <Typography
                typography="s3"
                color="text.secondary"
                sx={{ mt: 0.5, display: "block" }}
                data-testid="env-apply-no-tools"
              >
                {`Apply saves ${typedAddress} with no MCP tools: no server answers there, so runs on this environment call none of ${environment?.mcp?.name ?? "the current server"}'s tools.`}
              </Typography>
            ) : null}
          </Section>

          <Section
            title="Start state"
            hint="Every row's start_state must match this schema"
          >
            <Stack
              direction="row"
              flexWrap="wrap"
              gap={0.5}
              data-testid="env-schema"
            >
              {tables.map((t) => (
                <Box
                  key={t.key}
                  component="span"
                  sx={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 0.5,
                    height: 24,
                    px: 0.875,
                    borderRadius: 0.75,
                    border: "1px solid",
                    borderColor: "divider",
                    typography: "s2",
                  }}
                >
                  <Box component="span" sx={MONO}>
                    {t.key}
                  </Box>
                  <Box
                    component="span"
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {t.type}
                    {t.required ? " · required" : ""}
                  </Box>
                </Box>
              ))}
            </Stack>
            <ButtonBase
              onClick={() => setSchemaOpen((o) => !o)}
              sx={{
                mt: 0.75,
                typography: "s3",
                color: "primary.main",
                gap: 0.25,
              }}
            >
              <Iconify
                icon={schemaOpen ? "mdi:chevron-down" : "mdi:chevron-right"}
                width={14}
              />
              JSON schema
            </ButtonBase>
            <Collapse in={schemaOpen} unmountOnExit>
              <Box
                sx={{
                  mt: 0.5,
                  p: 1,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                }}
              >
                <ValueViewer value={schema} dataType="json" />
              </Box>
            </Collapse>
            <TextField
              size="small"
              fullWidth
              label="Reset command"
              disabled={readOnly}
              value={form.reset}
              placeholder="python -m acme_sandbox.reset --state $START_STATE"
              onChange={(e) =>
                setForm((f) => ({ ...f, reset: e.target.value }))
              }
              inputProps={{ style: MONO_INPUT }}
              sx={{ mt: 2.25 }}
            />
          </Section>

          <Section
            title="Secrets"
            hint="Names only; values never leave the vault"
          >
            <Stack
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              {(environment.secrets ?? []).map((s, i) => (
                <Stack
                  key={s.name}
                  direction="row"
                  alignItems="center"
                  gap={1}
                  sx={{
                    px: 1.25,
                    py: 0.75,
                    borderTop: i ? "1px solid" : "none",
                    borderColor: "divider",
                  }}
                >
                  <Iconify
                    icon="mdi:key-variant"
                    width={14}
                    sx={{ color: "text.secondary" }}
                  />
                  <Typography typography="s2" sx={MONO}>
                    {s.name}
                  </Typography>
                  <Typography
                    typography="s2"
                    color="text.disabled"
                    sx={{ ...MONO, flex: 1 }}
                  >
                    ••••••••
                  </Typography>
                  <Typography
                    typography="s3"
                    color={s.set ? "green.600" : "amber.700"}
                  >
                    {s.set ? "Set" : "Not set"}
                  </Typography>
                </Stack>
              ))}
              {!(environment.secrets ?? []).length && (
                <Typography
                  typography="s2"
                  color="text.secondary"
                  sx={{ px: 1.25, py: 1 }}
                >
                  No secrets.
                </Typography>
              )}
            </Stack>
          </Section>
        </Stack>
      )}

      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 2.5,
          py: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {pendingOp ? (
          <Box
            data-testid="env-pending"
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.75,
              px: 1,
              height: 28,
              borderRadius: 0.75,
              minWidth: 0,
              bgcolor: (t) => alpha(t.palette.amber[500], 0.16),
            }}
          >
            <Typography
              typography="s2"
              fontWeight={500}
              noWrap
              sx={{
                color: (t) =>
                  t.palette.mode === "dark"
                    ? t.palette.amber[500]
                    : t.palette.amber[700],
              }}
            >
              {environmentChangeText(pendingOp.before?.image, pendingOp.image)}
            </Typography>
            <CustomTooltip
              show
              size="small"
              placement="top"
              arrow
              title="Revert this change"
            >
              <IconButton
                size="small"
                aria-label="Revert environment change"
                onClick={() => changeSet.revert(pendingOp.id)}
                sx={{ p: 0.25 }}
              >
                <Iconify icon="mdi:undo" width={14} />
              </IconButton>
            </CustomTooltip>
          </Box>
        ) : null}
        <Box sx={{ flex: 1 }} />
        <Button size="small" variant="text" onClick={onClose}>
          Close
        </Button>
        {!readOnly && (
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title={
              imageError
                ? "Fix the image name first"
                : dirty
                  ? "Adds an unsaved change; it is saved with the next version"
                  : "Change something to apply"
            }
          >
            <span>
              <Button
                size="small"
                variant="contained"
                disabled={!dirty || Boolean(imageError)}
                onClick={apply}
                data-testid="env-apply"
              >
                Apply
              </Button>
            </span>
          </CustomTooltip>
        )}
      </Stack>
    </Drawer>
  );
};

EnvironmentDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  datasetId: PropTypes.string,
  rowId: PropTypes.string,
};

export default EnvironmentDrawer;
