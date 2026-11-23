import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Collapse, Stack, Tooltip, Typography } from "@mui/material";
import { ROW_HEIGHT } from "src/theme/sizes";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
import {
  MONO,
  TOOLS_CARD_COPY as T,
  effectLabel,
  groupToolsByStatus,
  inputLine,
  returnLine,
} from "./world.constants";

export default function ToolsCard({ tools, sx }) {
  const [openTool, setOpenTool] = useState(null);
  const groups = groupToolsByStatus(tools);
  return (
    <SectionCard title={T.title(tools.length)} subtitle={T.subtitle} sx={sx}>
      {groups.map((group, gi) => (
        <Box key={group.status} role="group" aria-label={group.label}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 1.5,
              py: 0.75,
              bgcolor: "background.neutral",
              borderTop: gi ? "1px solid" : "none",
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography sx={{ typography: "s3", fontWeight: 600 }}>
              {group.label}
            </Typography>
            <Typography
              noWrap
              sx={{
                typography: "s3",
                color: "text.tertiary",
                flex: 1,
                minWidth: 0,
              }}
            >
              {`${group.note} ${T.groupCount(group.tools.length)}.`}
            </Typography>
          </Stack>
          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
          >
            {group.tools.map((tool) => {
              const open = openTool === tool.tool;
              return (
                <Box key={tool.tool}>
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={1.5}
                    onClick={() => setOpenTool(open ? null : tool.tool)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setOpenTool(open ? null : tool.tool);
                      }
                    }}
                    aria-expanded={open}
                    aria-label={T.expand(tool.tool)}
                    sx={{
                      px: 1.5,
                      minHeight: ROW_HEIGHT.default,
                      cursor: "pointer",
                      bgcolor: open ? "action.selected" : "transparent",
                      "&:hover": {
                        bgcolor: open ? "action.selected" : "action.hover",
                      },
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
                      noWrap
                      sx={{
                        typography: "s1",
                        fontWeight: 500,
                        fontFamily: MONO,
                        width: 220,
                        flexShrink: 0,
                      }}
                    >
                      {tool.tool}
                    </Typography>
                    <Typography
                      noWrap
                      sx={{
                        typography: "s3",
                        color: "text.secondary",
                        flex: 1,
                        minWidth: 0,
                      }}
                    >
                      {tool.description}
                    </Typography>
                    {tool.runs_at && (
                      <Tooltip arrow title={tool.runs_at}>
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
                          {tool.runs_at}
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
        </Box>
      ))}
    </SectionCard>
  );
}

export function ToolDetail({ tool }) {
  const args = tool.arguments || [];
  const returned = tool.returns || [];
  const requires = tool.requires || [];
  const field = (label, body) => (
    <Box key={label} sx={{ display: "contents" }}>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
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
          {tool.tables?.label && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {tool.tables.label}
            </Typography>
          )}
          {tool.effects && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {tool.effects}
            </Typography>
          )}
        </Stack>,
      )}
      {tool.refuses && field(T.refusesWhen, text(tool.refuses))}
      {requires.length > 0 && field(T.needsFirst, text(requires.join(", ")))}
      {tool.reset_between_trials != null &&
        field(
          T.resetLabel,
          text(tool.reset_between_trials ? T.reset : T.notReset),
        )}
    </Box>
  );
}

const TOOL_SHAPE = PropTypes.shape({
  tool: PropTypes.string.isRequired,
  status: PropTypes.oneOf(["simulated", "in_process", "unsupported", "unknown"])
    .isRequired,
  reset_between_trials: PropTypes.bool,
  runs_at: PropTypes.string,
  writes: PropTypes.bool,
  description: PropTypes.string,
  arguments: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      type: PropTypes.string,
      unit: PropTypes.string,
    }),
  ),
  returns: PropTypes.arrayOf(
    PropTypes.shape({ name: PropTypes.string, kind: PropTypes.string }),
  ),
  requires: PropTypes.arrayOf(PropTypes.string),
  refuses: PropTypes.string,
  effect: PropTypes.oneOf(["read", "write", "unknown"]),
  effects: PropTypes.string,
  tables: PropTypes.shape({
    recorded: PropTypes.bool,
    read: PropTypes.arrayOf(PropTypes.string),
    changed: PropTypes.arrayOf(PropTypes.string),
    label: PropTypes.string,
  }),
});

ToolDetail.propTypes = { tool: TOOL_SHAPE.isRequired };
ToolsCard.propTypes = {
  tools: PropTypes.arrayOf(TOOL_SHAPE).isRequired,
  sx: PropTypes.object,
};
