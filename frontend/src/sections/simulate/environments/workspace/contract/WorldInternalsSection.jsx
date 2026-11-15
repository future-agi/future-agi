import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Collapse, Tooltip } from "@mui/material";
import { ROW_HEIGHT } from "src/theme/sizes";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
import {
  TOOLS_CARD_COPY as T,
  effectLabel,
  inputLine,
} from "./toolsCard.constants";

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
      {schema.length > 0 && (
        <SchemaCard schema={schema} totalRows={totalRows} />
      )}
      {tools.length > 0 && (
        <ToolsCard
          tools={tools}
          entrypoints={entrypoints}
          reach={env?.doorReach || {}}
        />
      )}
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
        ? Object.entries(fields).map(([name, type]) => ({
            name,
            type: String(type),
          }))
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
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {schema.length} tables · {totalRows.toLocaleString()} rows
        </Typography>
      }
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {schema.map((t) => {
          const expandable = !!t.cols?.length;
          const open = expandable && openTable === t.name;
          return (
            <Box key={t.name}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                onClick={
                  expandable
                    ? () => setOpenTable(open ? null : t.name)
                    : undefined
                }
                sx={{
                  px: 1.5,
                  minHeight: ROW_HEIGHT.default,
                  cursor: expandable ? "pointer" : "default",
                  "&:hover": expandable ? { bgcolor: "action.hover" } : {},
                }}
              >
                {anyExpandable && (
                  <Iconify
                    icon={
                      open
                        ? "solar:alt-arrow-down-linear"
                        : "solar:alt-arrow-right-linear"
                    }
                    width={13}
                    sx={{
                      color: "text.subtitle",
                      flexShrink: 0,
                      visibility: expandable ? "visible" : "hidden",
                    }}
                  />
                )}
                <Iconify
                  icon="solar:database-linear"
                  width={15}
                  sx={{ color: "text.secondary", flexShrink: 0 }}
                />
                <Typography
                  sx={{
                    typography: "s1",
                    fontWeight: 500,
                    fontFamily: MONO,
                    minWidth: 140,
                  }}
                >
                  {t.name}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.subtitle",
                    flex: 1,
                    minWidth: 0,
                  }}
                  noWrap
                >
                  {[expandable ? `${t.cols.length} fields` : null, t.note]
                    .filter(Boolean)
                    .join(" · ")}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.subtitle",
                    fontVariantNumeric: "tabular-nums",
                    flexShrink: 0,
                  }}
                >
                  {(t.rows || 0).toLocaleString()} rows
                </Typography>
              </Stack>
              {expandable && (
                <Collapse in={open}>
                  <Box
                    sx={{
                      px: 1.5,
                      py: 1.5,
                      bgcolor: "background.neutral",
                      borderTop: "1px solid",
                      borderColor: "divider",
                    }}
                  >
                    <Box
                      sx={{
                        display: "grid",
                        gridTemplateColumns: "1fr 1fr",
                        rowGap: 0.5,
                        columnGap: 1.5,
                      }}
                    >
                      {t.cols.map((c) => (
                        <Box key={c.name} sx={{ display: "contents" }}>
                          <Typography
                            sx={{
                              typography: "s1",
                              fontFamily: MONO,
                              fontWeight: 500,
                            }}
                          >
                            {c.name}
                          </Typography>
                          <Typography
                            sx={{
                              typography: "s3",
                              fontFamily: MONO,
                              color: "text.subtitle",
                            }}
                          >
                            {c.type}
                          </Typography>
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

function ToolsCard({ tools, entrypoints, reach }) {
  const [openTool, setOpenTool] = useState(null);
  return (
    <SectionCard
      title="Tools"
      subtitle="The tools the agent declares, and where each one runs when ALK could read it"
      action={
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {tools.length} tools
        </Typography>
      }
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {tools.map((tool) => {
          const entry = entrypoints[tool.name];
          const runsAt = entry?.callable
            ? `${entry.module ? `${entry.module}.` : ""}${entry.callable}`
            : entry?.endpoint
              ? `${entry.service ? `${entry.service} ` : ""}${entry.method || "POST"} /${entry.endpoint}`
              : null;
          const open = openTool === tool.name;
          // Behind the world's door it acts on the seeded test world; otherwise it runs inside the agent.
          const known = tool.name in reach;
          const behindDoor = Boolean(reach[tool.name]);
          return (
            <Box key={tool.name}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                onClick={() => setOpenTool(open ? null : tool.name)}
                role="button"
                aria-expanded={open}
                aria-label={T.expand(tool.name)}
                sx={{
                  px: 1.5,
                  minHeight: ROW_HEIGHT.default,
                  cursor: "pointer",
                }}
              >
                <Iconify
                  icon={
                    open ? "eva:chevron-down-fill" : "eva:chevron-right-fill"
                  }
                  width={16}
                  sx={{ color: "text.secondary", flexShrink: 0 }}
                />
                <Typography
                  sx={{
                    typography: "s1",
                    fontWeight: 500,
                    fontFamily: MONO,
                    minWidth: 220,
                  }}
                >
                  {tool.name}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.subtitle",
                    flex: 1,
                    minWidth: 0,
                  }}
                  noWrap
                >
                  {tool.desc}
                </Typography>
                {known && (
                  <Box
                    component="span"
                    sx={{
                      px: 0.75,
                      height: 18,
                      borderRadius: "4px",
                      typography: "s3",
                      fontWeight: 500,
                      lineHeight: "18px",
                      flexShrink: 0,
                      color: behindDoor ? "success.dark" : "warning.dark",
                      bgcolor: behindDoor
                        ? "success.lighter"
                        : "warning.lighter",
                    }}
                  >
                    {behindDoor ? T.labels.simulated : T.labels.inProcess}
                  </Box>
                )}
                {runsAt && (
                  <Tooltip arrow title={runsAt}>
                    <Box
                      component="span"
                      sx={{
                        px: 0.75,
                        height: 18,
                        maxWidth: 320,
                        borderRadius: "4px",
                        color: "text.secondary",
                        bgcolor: "action.selected",
                        typography: "s3",
                        fontWeight: 500,
                        fontFamily: MONO,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        display: "block",
                        lineHeight: "18px",
                      }}
                    >
                      {runsAt}
                    </Box>
                  </Tooltip>
                )}
              </Stack>
              <Collapse in={open}>
                <ToolDetail
                  tool={tool}
                  entry={entry}
                  known={known}
                  behindDoor={behindDoor}
                />
              </Collapse>
            </Box>
          );
        })}
      </Stack>
    </SectionCard>
  );
}

// One tool's contract: its inputs with types and units, what it changes, when it refuses, what it needs first and
// what happens to it between trials.
function ToolDetail({ tool, entry, known, behindDoor }) {
  const args = tool.args || [];
  const units = tool.argUnits || {};
  const types = tool.argTypes || {};
  const field = (label, body) => (
    <Box key={label} sx={{ display: "contents" }}>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {label}
      </Typography>
      <Box>{body}</Box>
    </Box>
  );
  const text = (value) => (
    <Typography sx={{ typography: "s1" }}>{value}</Typography>
  );
  return (
    <Box
      sx={{
        px: 1.5,
        py: 1.5,
        bgcolor: "background.neutral",
        borderTop: "1px solid",
        borderColor: "divider",
        display: "grid",
        gridTemplateColumns: "140px minmax(0, 1fr)",
        rowGap: 1,
        columnGap: 1.5,
      }}
    >
      {field(
        T.inputs,
        args.length ? (
          <Stack spacing={0.25}>
            {args.map((name) => (
              <Typography
                key={name}
                sx={{ typography: "s1", fontFamily: MONO }}
              >
                {inputLine(name, types[name], units[name])}
              </Typography>
            ))}
          </Stack>
        ) : (
          text(T.noInputs)
        ),
      )}
      {field(
        T.changesRecords,
        <Stack spacing={0.25}>
          {text(effectLabel(entry?.effect))}
          {tool.effects && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {tool.effects}
            </Typography>
          )}
        </Stack>,
      )}
      {tool.refuses && field(T.refusesWhen, text(tool.refuses))}
      {tool.requires?.length > 0 &&
        field(T.needsFirst, text(tool.requires.join(", ")))}
      {known && field(T.betweenTrials, text(behindDoor ? T.reset : T.notReset))}
    </Box>
  );
}
ToolDetail.propTypes = {
  tool: PropTypes.object.isRequired,
  entry: PropTypes.object,
  known: PropTypes.bool,
  behindDoor: PropTypes.bool,
};
ToolsCard.propTypes = {
  tools: PropTypes.array,
  entrypoints: PropTypes.object,
  reach: PropTypes.object,
};
