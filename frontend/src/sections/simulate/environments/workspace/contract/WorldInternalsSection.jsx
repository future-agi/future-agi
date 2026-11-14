import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Collapse, Tooltip } from "@mui/material";
import { ROW_HEIGHT } from "src/theme/sizes";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
import { WORKERS_COPY } from "./workers.constants";

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
  const workers = (env?.workers || []).filter((one) => one?.id);
  const entrypoints = entrypointsByTool(env);
  const totalRows = schema.reduce((a, t) => a + (t.rows || 0), 0);

  return (
    <Stack spacing={2} sx={{ mb: 2 }}>
      {schema.length > 0 && <SchemaCard schema={schema} totalRows={totalRows} />}
      {tools.length > 0 && <ToolsCard tools={tools} entrypoints={entrypoints} />}
      {workers.length > 0 && <WorkersCard workers={workers} />}
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
  // The chevron's slot only exists when some table can open; otherwise the
  // icons would sit indented from the header for a chevron nobody sees.
  const anyExpandable = schema.some((t) => t.cols?.length);
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
                sx={{ px: 1.5, minHeight: ROW_HEIGHT.default, cursor: expandable ? "pointer" : "default", "&:hover": expandable ? { bgcolor: "action.hover" } : {} }}
              >
                {anyExpandable && (
                  <Iconify
                    icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
                    width={13}
                    sx={{ color: "text.subtitle", flexShrink: 0, visibility: expandable ? "visible" : "hidden" }}
                  />
                )}
                <Iconify icon="solar:database-linear" width={15} sx={{ color: "text.secondary", flexShrink: 0 }} />
                <Typography sx={{ typography: "s1", fontWeight: 500, fontFamily: MONO, minWidth: 140 }}>{t.name}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }} noWrap>
                  {[expandable ? `${t.cols.length} fields` : null, t.note].filter(Boolean).join(" · ")}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>
                  {(t.rows || 0).toLocaleString()} rows
                </Typography>
              </Stack>
              {expandable && (
                <Collapse in={open}>
                  <Box sx={{ px: 1.5, py: 1.5, bgcolor: "background.neutral", borderTop: "1px solid", borderColor: "divider" }}>
                    <Box sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", rowGap: 0.5, columnGap: 1.5 }}>
                      {t.cols.map((c) => (
                        <Box key={c.name} sx={{ display: "contents" }}>
                          <Typography sx={{ typography: "s1", fontFamily: MONO, fontWeight: 500 }}>{c.name}</Typography>
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
            <Stack key={tool.name} direction="row" alignItems="center" spacing={1.5} sx={{ px: 1.5, minHeight: ROW_HEIGHT.default }}>
              <Iconify icon="solar:code-linear" width={15} sx={{ color: "text.secondary", flexShrink: 0 }} />
              <Typography sx={{ typography: "s1", fontWeight: 500, fontFamily: MONO, minWidth: 220 }}>{tool.name}</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }} noWrap>
                {tool.desc}
              </Typography>
              {runsAt && (
                <Tooltip arrow title={runsAt}>
                  <Box
                    component="span"
                    sx={{
                      px: 0.75, height: 18, maxWidth: 320, borderRadius: "4px", color: "text.secondary", bgcolor: "action.selected",
                      typography: "s3", fontWeight: 500, fontFamily: MONO,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block", lineHeight: "18px",
                    }}
                  >
                    {runsAt}
                  </Box>
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

/* ── workers ────────────────────────────────────────────────────────── */

const CHIP_SX = {
  px: 0.75,
  height: 18,
  borderRadius: "4px",
  color: "text.secondary",
  bgcolor: "action.selected",
  typography: "s3",
  fontWeight: 500,
  fontFamily: MONO,
  lineHeight: "18px",
  whiteSpace: "nowrap",
};

// Allowed tools are shown only when narrower than what the worker's code holds.
function narrower(worker) {
  const tools = worker.tools || [];
  const allowed = worker.allowed_tools || [];
  return allowed.length > 0 && tools.some((tool) => !allowed.includes(tool));
}

function WorkersCard({ workers }) {
  return (
    <SectionCard
      title={WORKERS_COPY.title}
      subtitle={WORKERS_COPY.subtitle}
      action={
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
          {WORKERS_COPY.count(workers.length)}
        </Typography>
      }
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />} data-testid="contract-workers">
        {workers.map((worker) => (
          <Stack key={worker.id} spacing={0.5} sx={{ px: 1.5, py: 1 }} data-testid="contract-worker">
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Iconify icon="solar:users-group-rounded-linear" width={15} sx={{ color: "text.secondary", flexShrink: 0 }} />
              <Typography sx={{ typography: "s1", fontWeight: 500, minWidth: 180 }}>{worker.name || worker.id}</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }} noWrap>
                {[WORKERS_COPY.binding[worker.binding || "shared"], worker.process ? WORKERS_COPY.ownProcess : null]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            </Stack>
            {(worker.tools || []).length > 0 && (
              <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ pl: 3.5 }}>
                {worker.tools.map((tool) => (
                  <Box key={tool} component="span" sx={CHIP_SX}>
                    {tool}
                  </Box>
                ))}
              </Stack>
            )}
            {narrower(worker) && (
              <Typography sx={{ typography: "s3", color: "text.subtitle", pl: 3.5 }}>
                {WORKERS_COPY.mayUse(worker.allowed_tools)}
              </Typography>
            )}
          </Stack>
        ))}
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.subtitle", px: 1.5, py: 1, borderTop: "1px solid", borderColor: "divider" }}>
        {WORKERS_COPY.footer}
      </Typography>
    </SectionCard>
  );
}
WorkersCard.propTypes = { workers: PropTypes.array };
