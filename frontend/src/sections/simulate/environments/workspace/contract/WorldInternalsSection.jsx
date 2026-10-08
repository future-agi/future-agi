import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Collapse, Chip, Tooltip, IconButton } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * "World" internals on the Contract tab: the tables the world holds and the
 * tools the agent has. Everything shown is read from the job's contract and
 * world outputs. Columns appear only when ALK reported the table's fields in
 * `data_schema`; a tool's callable appears only when `tool_entrypoints` names
 * it. Nothing is derived from a name.
 */
export default function WorldInternalsSection({ env }) {
  const schema = schemaRows(env);
  const tools = env?.tools || [];
  const entrypoints = entrypointsByTool(env);
  const totalRows = schema.reduce((a, t) => a + (t.rows || 0), 0);

  return (
    <Stack spacing={2} sx={{ mb: 2 }}>
      {(schema.length > 0 || env?.dataStore) && (
        <SchemaCard schema={schema} totalRows={totalRows} dataStore={env?.dataStore} />
      )}
      {tools.length > 0 && <ToolsCard tools={tools} entrypoints={entrypoints} />}
    </Stack>
  );
}
WorldInternalsSection.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  patch: PropTypes.func,
  locked: PropTypes.bool,
};

// Seed tables from `world.stores`, joined with the field types the contract's
// `data_schema` carries for a collection of the same name.
function schemaRows(env) {
  const tables = env?.seed?.tables || [];
  const dataSchema = env?.dataSchema || {};
  return tables.map((t) => {
    const fields = dataSchema[t.name];
    const cols =
      fields && typeof fields === "object" && !Array.isArray(fields)
        ? Object.entries(fields).map(([name, type]) => ({ name, type: String(type) }))
        : null;
    return { name: t.name, rows: t.rows, note: t.note, cols };
  });
}

function entrypointsByTool(env) {
  const out = {};
  for (const e of env?.toolEntrypoints || []) {
    if (e?.tool) out[e.tool] = e;
  }
  return out;
}

/* ── tables ─────────────────────────────────────────────────────────── */

function SchemaCard({ schema, totalRows, dataStore }) {
  const [openTable, setOpenTable] = useState(null);
  const storeDetails = Object.entries(dataStore || {}).filter(
    ([, value]) => value !== "" && value != null,
  );
  return (
    <SectionCard
      title="World state · database"
      subtitle="The tables this environment's world holds"
      action={
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
          {schema.length} tables · {totalRows.toLocaleString()} rows
        </Typography>
      }
    >
      {storeDetails.length > 0 && (
        <Box sx={{ px: 2.5, py: 1.75, bgcolor: "background.neutral", borderBottom: "1px solid", borderColor: "divider" }}>
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))" }, gap: 1.25 }}>
            {storeDetails.map(([key, value]) => (
              <Box key={key} minWidth={0}>
                <Typography sx={{ typography: "s3", color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.35 }}>
                  {key.replaceAll("_", " ")}
                </Typography>
                <Typography sx={{ typography: "s2", fontFamily: MONO, overflowWrap: "anywhere" }}>
                  {storeValue(key, value)}
                </Typography>
              </Box>
            ))}
          </Box>
        </Box>
      )}
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {schema.map((t) => {
          const expandable = !!t.cols?.length;
          const open = expandable && openTable === t.name;
          return (
            <Box key={t.name}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                onClick={expandable ? () => setOpenTable(open ? null : t.name) : undefined}
                sx={{ px: 2.5, py: 1.375, cursor: expandable ? "pointer" : "default", "&:hover": expandable ? { bgcolor: "action.hover" } : {} }}
              >
                <Iconify
                  icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
                  width={13}
                  sx={{ color: "text.subtitle", flexShrink: 0, visibility: expandable ? "visible" : "hidden" }}
                />
                <Iconify icon="solar:database-linear" width={15} sx={{ color: "#2563EB", flexShrink: 0 }} />
                <Typography sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO, minWidth: 140 }}>{t.name}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }} noWrap>
                  {[expandable ? `${t.cols.length} fields` : null, t.note].filter(Boolean).join(" · ")}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>
                  {(t.rows || 0).toLocaleString()} rows
                </Typography>
              </Stack>
              {expandable && (
                <Collapse in={open}>
                  <Box sx={{ px: 2.5, py: 1.5, bgcolor: "background.neutral", borderTop: "1px solid", borderColor: "divider" }}>
                    <Box sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", rowGap: 0.5, columnGap: 1.5 }}>
                      {t.cols.map((c) => (
                        <Box key={c.name} sx={{ display: "contents" }}>
                          <Typography sx={{ typography: "s2", fontFamily: MONO, fontWeight: 600 }}>{c.name}</Typography>
                          <Typography sx={{ typography: "s3", fontFamily: MONO, color: "text.subtitle" }}>{c.type}</Typography>
                        </Box>
                      ))}
                    </Box>
                  </Box>
                </Collapse>
              )}
            </Box>
          );
        })}
      </Stack>
    </SectionCard>
  );
}
SchemaCard.propTypes = { schema: PropTypes.array, totalRows: PropTypes.number, dataStore: PropTypes.object };

/* ── tools ──────────────────────────────────────────────────────────── */

function ToolsCard({ tools, entrypoints }) {
  const [openTool, setOpenTool] = useState(null);
  return (
    <SectionCard
      title="Tools"
      subtitle="The tools the agent declares, and where each one runs when ALK could read it"
      action={
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
          {tools.length} tools
        </Typography>
      }
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {tools.map((tool) => {
          const entry = entrypoints[tool.name];
          const open = openTool === tool.name;
          const runsAt = entry?.callable
            ? `${entry.module ? `${entry.module}.` : ""}${entry.callable}`
            : entry?.endpoint
              ? `${entry.service ? `${entry.service} ` : ""}${entry.method || "POST"} /${entry.endpoint}`
              : null;
          return (
            <Box key={tool.name}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                onClick={() => setOpenTool(open ? null : tool.name)}
                sx={{ px: 2.5, py: 1.375, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
              >
                <Iconify
                  icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
                  width={13}
                  sx={{ color: "text.subtitle", flexShrink: 0 }}
                />
                <Iconify icon="solar:code-linear" width={15} sx={{ color: "#7857FC", flexShrink: 0 }} />
                <Typography sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO, minWidth: 220 }}>{tool.name}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }} noWrap>
                  {tool.desc}
                </Typography>
                {runsAt && (
                  <Tooltip arrow title={runsAt}>
                    <Chip
                      size="small"
                      label={runsAt}
                      sx={{
                        height: 18, maxWidth: 320, borderRadius: 0.5, color: "text.subtitle",
                        border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                        "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600, fontFamily: MONO },
                      }}
                    />
                  </Tooltip>
                )}
              </Stack>
              <Collapse in={open}>
                <ToolDetail tool={tool} entry={entry} />
              </Collapse>
            </Box>
          );
        })}
      </Stack>
    </SectionCard>
  );
}
ToolsCard.propTypes = { tools: PropTypes.array, entrypoints: PropTypes.object };

function ToolDetail({ tool, entry }) {
  const sourceCode = entry?.source_code || entry?.code || tool?.source_code || tool?.code;
  const file = entry?.module || entry?.service || "contract.json";
  const contractSnippet = JSON.stringify(
    {
      name: tool.name,
      description: tool.desc,
      parameters: Object.fromEntries(
        (tool.args || []).map((arg) => [arg, tool.argTypes?.[arg] || "unknown"]),
      ),
      ...(tool.requires?.length ? { requires: tool.requires } : {}),
      ...(entry ? { entrypoint: entry } : {}),
    },
    null,
    2,
  );

  return (
    <CodeBlock
      file={file}
      label={sourceCode ? "Source snippet" : "Tool contract and entrypoint"}
      code={sourceCode || contractSnippet}
    />
  );
}
ToolDetail.propTypes = { tool: PropTypes.object.isRequired, entry: PropTypes.object };

function CodeBlock({ file, label, code }) {
  const copy = () => {
    navigator.clipboard?.writeText(code);
    enqueueSnackbar("Copied to clipboard", { variant: "info" });
  };
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2.5,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: (theme) => alpha(theme.palette.text.primary, theme.palette.mode === "dark" ? 0.06 : 0.03),
        }}
      >
        <Iconify icon="solar:file-code-linear" width={13} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", fontWeight: 700 }}>{label}</Typography>
        <Typography sx={{ typography: "s3", fontFamily: MONO, color: "text.secondary", flex: 1 }}>
          {file}
        </Typography>
        <Tooltip arrow title="Copy snippet">
          <IconButton size="small" onClick={copy} aria-label={`Copy ${toolSafeLabel(file)} snippet`}>
            <Iconify icon="solar:copy-linear" width={13} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Tooltip>
      </Stack>
      <Box sx={{ px: 2.5, py: 1.5, maxHeight: 360, overflow: "auto" }}>
        <Typography
          component="pre"
          sx={{ typography: "s3", fontFamily: MONO, color: "text.primary", whiteSpace: "pre", m: 0, lineHeight: 1.6 }}
        >
          {code}
        </Typography>
      </Box>
    </Box>
  );
}
CodeBlock.propTypes = { file: PropTypes.string, label: PropTypes.string, code: PropTypes.string };

const toolSafeLabel = (value) => String(value || "tool").replace(/[^a-zA-Z0-9_-]+/g, " ").trim();

const storeValue = (key, value) => {
  if (/(password|secret|token|credential|api[_-]?key)/i.test(key)) return "••••••";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
};
