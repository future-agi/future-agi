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
 * tools the agent has, as the environment's detail states them. Nothing is derived here.
 */
export default function WorldInternalsSection({ env }) {
  const tables = env?.worldTables;
  const tools = env?.toolStatus || [];

  return (
    <Stack spacing={2} sx={{ mb: 2 }}>
      {tables?.length > 0 && (
        <SchemaCard schema={tables} totalRows={env.worldTotalRows} />
      )}
      {tools.length > 0 && <ToolsCard tools={tools} />}
    </Stack>
  );
}
WorldInternalsSection.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  patch: PropTypes.func,
  locked: PropTypes.bool,
};

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

// `unknown` has no chip.
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

function ToolsCard({ tools }) {
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
          const runsAt = tool.runs_at;
          const chip = STATUS_CHIP[tool.status];
          const open = openTool === tool.tool;
          return (
            <Box key={tool.tool}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                onClick={() => setOpenTool(open ? null : tool.tool)}
                role="button"
                aria-expanded={open}
                aria-label={T.expand(tool.tool)}
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
                  {tool.tool}
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
                  {tool.description}
                </Typography>
                {chip && (
                  <Tooltip
                    arrow
                    title={
                      tool.status === "unsupported" ? T.unsupportedWhy : ""
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
                <ToolDetail tool={tool} />
              </Collapse>
            </Box>
          );
        })}
      </Stack>
    </SectionCard>
  );
}

function ToolDetail({ tool }) {
  const args = tool.arguments;
  const returned = tool.returns;
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
            {args.map((one) => (
              <Typography
                key={one.name}
                sx={{ typography: "s1", fontFamily: MONO }}
              >
                {inputLine(one.name, one.type, one.unit)}
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
            {returned.map((one) => (
              <Typography
                key={one.name}
                sx={{ typography: "s1", fontFamily: MONO }}
              >
                {returnLine(one.name, one.kind)}
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
          {text(effectLabel(tool.effect))}
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {tool.tables.label}
          </Typography>
          {tool.effects && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {tool.effects}
            </Typography>
          )}
        </Stack>,
      )}
      {tool.refuses && field(T.refusesWhen, text(tool.refuses))}
      {tool.requires.length > 0 &&
        field(T.needsFirst, text(tool.requires.join(", ")))}
      {tool.reset_between_trials !== null &&
        field(
          T.resetLabel,
          text(tool.reset_between_trials ? T.reset : T.notReset),
        )}
    </Box>
  );
}
// One `contract.tool_status` entry.
const TOOL_SHAPE = PropTypes.shape({
  tool: PropTypes.string.isRequired,
  status: PropTypes.oneOf(["simulated", "in_process", "unsupported", "unknown"])
    .isRequired,
  reset_between_trials: PropTypes.bool,
  runs_at: PropTypes.string,
  writes: PropTypes.bool,
  description: PropTypes.string.isRequired,
  arguments: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      type: PropTypes.string,
      unit: PropTypes.string,
    }),
  ).isRequired,
  returns: PropTypes.arrayOf(
    PropTypes.shape({ name: PropTypes.string, kind: PropTypes.string }),
  ).isRequired,
  requires: PropTypes.arrayOf(PropTypes.string).isRequired,
  refuses: PropTypes.string.isRequired,
  effect: PropTypes.oneOf(["read", "write", "unknown"]).isRequired,
  effects: PropTypes.string.isRequired,
  tables: PropTypes.shape({ label: PropTypes.string.isRequired }).isRequired,
});
ToolDetail.propTypes = {
  tool: TOOL_SHAPE.isRequired,
};
ToolsCard.propTypes = {
  tools: PropTypes.arrayOf(TOOL_SHAPE).isRequired,
};
