import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Collapse, Tooltip } from "@mui/material";
import { ROW_HEIGHT } from "src/theme/sizes";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
import {
  TOOLS_CARD_COPY as T,
  returnLine,
  effectLabel,
  inputLine,
} from "./toolsCard.constants";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * "World" internals on the Contract tab: the tables the world holds and the
 * tools the agent has, as the environment's detail states them. The tables are
 * the world's own (`world.tables`, harness tables left out), with their fields
 * when the build read them; each tool's status, where it runs and whether it is
 * reset between trials is `contract.tool_status`. Nothing is derived here.
 */
export default function WorldInternalsSection({ env }) {
  const tables = env?.worldTables;
  const tools = env?.tools || [];
  const entrypoints = entrypointsByTool(env);

  return (
    <Stack spacing={2} sx={{ mb: 2 }}>
      {tables?.length > 0 && (
        <SchemaCard schema={tables} totalRows={env.worldTotalRows} />
      )}
      {tools.length > 0 && (
        <ToolsCard
          tools={tools}
          entrypoints={entrypoints}
          statuses={env?.toolStatus}
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
  const anyExpandable = schema.some((t) => t.fields.length);
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
          const expandable = t.fields.length > 0;
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
                  {[expandable ? `${t.fields.length} fields` : null, t.note]
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
                  {t.rows.toLocaleString()} rows
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
                      {t.fields.map((c) => (
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
SchemaCard.propTypes = {
  // The world's own tables: {name, rows, note, fields: [{name, type}]}.
  schema: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string.isRequired,
      rows: PropTypes.number.isRequired,
      note: PropTypes.string,
      fields: PropTypes.arrayOf(
        PropTypes.shape({ name: PropTypes.string, type: PropTypes.string }),
      ).isRequired,
    }),
  ).isRequired,
  totalRows: PropTypes.number.isRequired,
};

/* ── tools ──────────────────────────────────────────────────────────── */

// How each status reads on its chip, and its colour; `unknown` has no chip.
const STATUS_CHIP = {
  simulated: {
    label: T.labels.simulated,
    color: "success.dark",
    bg: "success.lighter",
  },
  in_process: {
    label: T.labels.inProcess,
    color: "warning.dark",
    bg: "warning.lighter",
  },
  unsupported: {
    label: T.labels.unsupported,
    color: "error.dark",
    bg: "error.lighter",
  },
};

function ToolsCard({ tools, entrypoints, statuses }) {
  const [openTool, setOpenTool] = useState(null);
  return (
    <SectionCard
      title="Tools"
      subtitle={T.subtitle}
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
          const status = statuses?.find((one) => one.tool === tool.name);
          const runsAt = status?.runs_at;
          const chip = status && STATUS_CHIP[status.status];
          const open = openTool === tool.name;
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
                    open
                      ? "solar:alt-arrow-down-linear"
                      : "solar:alt-arrow-right-linear"
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
                {chip && (
                  <Tooltip
                    arrow
                    title={
                      status.status === "unsupported" ? T.unsupportedWhy : ""
                    }
                  >
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
                        color: chip.color,
                        bgcolor: chip.bg,
                      }}
                    >
                      {chip.label}
                    </Box>
                  </Tooltip>
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
                <ToolDetail tool={tool} entry={entry} status={status} />
              </Collapse>
            </Box>
          );
        })}
      </Stack>
    </SectionCard>
  );
}

// One tool's contract: its arguments with types and units, what it changes, when it refuses, what it needs first and
// whether it is reset between trials.
function ToolDetail({ tool, entry, status }) {
  const args = tool.args || [];
  const units = tool.argUnits || {};
  const types = tool.argTypes || {};
  const returned = Object.entries(tool.returns || {});
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
        T.arguments,
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
        T.returns,
        returned.length ? (
          <Stack spacing={0.25}>
            {returned.map(([name, kind]) => (
              <Typography
                key={name}
                sx={{ typography: "s1", fontFamily: MONO }}
              >
                {returnLine(name, kind)}
              </Typography>
            ))}
          </Stack>
        ) : (
          text(T.returnsNotDeclared)
        ),
      )}
      {field(
        T.changes,
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
      {status &&
        status.reset_between_trials !== null &&
        field(
          T.resetLabel,
          text(status.reset_between_trials ? T.reset : T.notReset),
        )}
    </Box>
  );
}
const TOOL_STATUS_SHAPE = PropTypes.shape({
  tool: PropTypes.string.isRequired,
  status: PropTypes.oneOf(["simulated", "in_process", "unsupported", "unknown"])
    .isRequired,
  reset_between_trials: PropTypes.bool,
  runs_at: PropTypes.string,
  writes: PropTypes.bool,
});
ToolDetail.propTypes = {
  tool: PropTypes.object.isRequired,
  entry: PropTypes.object,
  status: TOOL_STATUS_SHAPE,
};
ToolsCard.propTypes = {
  tools: PropTypes.array,
  entrypoints: PropTypes.object,
  // Per contract tool, as the environment's detail states it; absent before the detail is ready.
  statuses: PropTypes.arrayOf(TOOL_STATUS_SHAPE),
};
