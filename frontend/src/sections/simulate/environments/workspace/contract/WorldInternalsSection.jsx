import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Collapse, Chip, Tooltip } from "@mui/material";
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
      {schema.length > 0 && <SchemaCard schema={schema} totalRows={totalRows} />}
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

function SchemaCard({ schema, totalRows }) {
  const [openTable, setOpenTable] = useState(null);
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
                  {expandable ? `${t.cols.length} fields` : "fields not reported"}
                  {t.note ? ` · ${t.note}` : ""}
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
SchemaCard.propTypes = { schema: PropTypes.array, totalRows: PropTypes.number };

/* ── tools ──────────────────────────────────────────────────────────── */

function ToolsCard({ tools, entrypoints }) {
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
          const runsAt = entry?.callable
            ? `${entry.module ? `${entry.module}.` : ""}${entry.callable}`
            : entry?.endpoint
              ? `${entry.service ? `${entry.service} ` : ""}${entry.method || "POST"} /${entry.endpoint}`
              : null;
          return (
            <Stack key={tool.name} direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 1.375 }}>
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
          );
        })}
      </Stack>
    </SectionCard>
  );
}
ToolsCard.propTypes = { tools: PropTypes.array, entrypoints: PropTypes.object };
