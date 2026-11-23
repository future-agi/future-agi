import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Collapse, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
import { MONO, WORLD_COPY, toolsUsingTable } from "./world.constants";

const M = WORLD_COPY.map;

export default function WorldMapCard({ tables, totalRows, tools = [], sx }) {
  const [openTable, setOpenTable] = useState(null);
  const recorded = tools.some((t) => t.tables?.recorded);
  const rows = totalRows ?? tables.reduce((sum, t) => sum + (t.rows || 0), 0);
  const grid = recorded
    ? "16px minmax(140px, 1.2fr) 72px 72px minmax(0, 2fr)"
    : "16px minmax(140px, 1.2fr) 72px 72px";
  const headCell = {
    typography: "s3",
    fontWeight: 500,
    color: "text.tertiary",
  };

  return (
    <SectionCard
      title={M.title(tables.length, tools.length)}
      subtitle={M.subtitle(rows, recorded)}
      sx={sx}
    >
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: grid,
          columnGap: 1.5,
          px: 1.5,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box />
        <Typography sx={headCell}>{M.table}</Typography>
        <Typography sx={headCell}>{M.rows}</Typography>
        <Typography sx={headCell}>{M.columns}</Typography>
        {recorded && <Typography sx={headCell}>{M.usedBy}</Typography>}
      </Box>
      {tables.map((table, i) => {
        const fields = table.fields || [];
        const expandable = fields.length > 0;
        const open = expandable && openTable === table.name;
        const used = toolsUsingTable(tools, table.name);
        const toggle = () => setOpenTable(open ? null : table.name);
        return (
          <Box
            key={table.name}
            sx={{ borderTop: i ? "1px solid" : "none", borderColor: "divider" }}
          >
            <Box
              {...(expandable && {
                role: "button",
                tabIndex: 0,
                "aria-expanded": open,
                "aria-label": M.expand(table.name),
                onClick: toggle,
                onKeyDown: (e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    toggle();
                  }
                },
              })}
              sx={{
                display: "grid",
                gridTemplateColumns: grid,
                columnGap: 1.5,
                alignItems: "start",
                px: 1.5,
                py: 1,
                cursor: expandable ? "pointer" : "default",
                bgcolor: open ? "action.selected" : "transparent",
                "&:hover": expandable
                  ? { bgcolor: open ? "action.selected" : "action.hover" }
                  : {},
              }}
            >
              <Iconify
                icon={
                  open
                    ? "solar:alt-arrow-down-linear"
                    : "solar:alt-arrow-right-linear"
                }
                width={12}
                sx={{
                  color: "text.tertiary",
                  mt: 0.5,
                  visibility: expandable ? "visible" : "hidden",
                }}
              />
              <Box sx={{ minWidth: 0 }}>
                <Typography
                  noWrap
                  sx={{ typography: "s1", fontWeight: 500, fontFamily: MONO }}
                >
                  {table.name}
                </Typography>
                {table.note && (
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    {table.note}
                  </Typography>
                )}
              </Box>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.secondary",
                  fontVariantNumeric: "tabular-nums",
                  pt: 0.25,
                }}
              >
                {(table.rows || 0).toLocaleString()}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.secondary",
                  fontVariantNumeric: "tabular-nums",
                  pt: 0.25,
                }}
              >
                {expandable ? fields.length : "-"}
              </Typography>
              {recorded && (
                <Stack direction="row" flexWrap="wrap" sx={{ gap: 0.75 }}>
                  {used.length ? (
                    used.map((use) => <ToolChip key={use.tool} use={use} />)
                  ) : (
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        pt: 0.25,
                      }}
                    >
                      {M.noTool}
                    </Typography>
                  )}
                </Stack>
              )}
            </Box>
            {expandable && (
              <Collapse in={open}>
                <Box
                  sx={{
                    px: 1.5,
                    pl: 5,
                    py: 1.5,
                    bgcolor: "background.neutral",
                    borderTop: "1px solid",
                    borderColor: "divider",
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    rowGap: 0.5,
                    columnGap: 1.5,
                  }}
                >
                  {fields.map((field) => (
                    <Box key={field.name} sx={{ display: "contents" }}>
                      <Typography
                        sx={{
                          typography: "s1",
                          fontFamily: MONO,
                          fontWeight: 500,
                        }}
                      >
                        {field.name}
                      </Typography>
                      <Typography
                        sx={{
                          typography: "s3",
                          fontFamily: MONO,
                          color: "text.tertiary",
                        }}
                      >
                        {field.type}
                      </Typography>
                    </Box>
                  ))}
                </Box>
              </Collapse>
            )}
          </Box>
        );
      })}
    </SectionCard>
  );
}

function ToolChip({ use }) {
  return (
    <Tooltip arrow title={use.changes ? M.changes : M.reads}>
      <Box
        component="span"
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          px: 0.75,
          height: 20,
          borderRadius: "4px",
          border: "1px solid",
          borderColor: "divider",
          typography: "s3",
          fontFamily: MONO,
          color: "text.primary",
        }}
      >
        {use.tool}
        {use.changes && (
          <Iconify
            icon="solar:pen-linear"
            width={12}
            sx={{ color: "text.tertiary" }}
          />
        )}
      </Box>
    </Tooltip>
  );
}
ToolChip.propTypes = {
  use: PropTypes.shape({ tool: PropTypes.string, changes: PropTypes.bool })
    .isRequired,
};

WorldMapCard.propTypes = {
  tables: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string.isRequired,
      rows: PropTypes.number,
      note: PropTypes.string,
      fields: PropTypes.arrayOf(
        PropTypes.shape({ name: PropTypes.string, type: PropTypes.string }),
      ),
    }),
  ).isRequired,
  totalRows: PropTypes.number,
  tools: PropTypes.arrayOf(PropTypes.object),
  sx: PropTypes.object,
};
