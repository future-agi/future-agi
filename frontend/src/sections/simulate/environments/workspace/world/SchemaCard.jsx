import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import SectionCard from "../../components/SectionCard";
import UiOnlyFlag from "../../components/UiOnlyFlag";

const MONO = "ui-monospace, Menlo, monospace";

// Seed tables from world.stores, joined with the field types the contract's
// data_schema carries for a collection of the same name. Columns appear only
// when ALK reported them; there's no key flag, so no primary-key marker.
export function schemaRows(env) {
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

// Vel's "World state · database schema": one row per table, open for columns.
export default function SchemaCard({ schema, sample }) {
  const [openTable, setOpenTable] = useState(null);
  const totalRows = schema.reduce((a, t) => a + (t.rows || 0), 0);
  const anyExpandable = schema.some((t) => t.cols?.length);
  return (
    <SectionCard
      title={
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <span>World state · database schema</span>
          {sample && <UiOnlyFlag />}
        </Stack>
      }
      subtitle="The tables and columns that make up this environment's world"
      action={
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>
          {`${schema.length} tables · ${totalRows.toLocaleString()} rows`}
        </Typography>
      }
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {schema.map((t) => {
          const expandable = !!t.cols?.length;
          const open = expandable && openTable === t.name;
          const toggle = () => setOpenTable(open ? null : t.name);
          return (
            <Box key={t.name}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                {...(expandable && {
                  role: "button",
                  tabIndex: 0,
                  "aria-expanded": open,
                  onClick: toggle,
                  onKeyDown: (e) => (e.key === "Enter" || e.key === " ") && toggle(),
                })}
                sx={{ px: 2.5, py: 1.375, cursor: expandable ? "pointer" : "default", "&:hover": expandable ? { bgcolor: "action.hover" } : {} }}
              >
                {anyExpandable && (
                  <Iconify
                    icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
                    width={14}
                    sx={{ color: "text.subtitle", flexShrink: 0, visibility: expandable ? "visible" : "hidden" }}
                  />
                )}
                <Iconify icon="solar:database-linear" width={16} sx={{ color: "info.main", flexShrink: 0 }} />
                <Typography sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO, minWidth: 140 }}>{t.name}</Typography>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
                  {[expandable ? `${t.cols.length} columns` : null, t.note].filter(Boolean).join(" · ")}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>
                  {`${(t.rows || 0).toLocaleString()} rows`}
                </Typography>
              </Stack>
              {expandable && (
                <Collapse in={open}>
                  <Box sx={{ px: 2.5, py: 1.5, bgcolor: "background.neutral", borderTop: "1px solid", borderColor: "divider" }}>
                    <Box sx={{ display: "grid", gridTemplateColumns: "20px 1fr 1fr", rowGap: 0.5, columnGap: 1.5 }}>
                      {t.cols.map((c) => (
                        <Box key={c.name} sx={{ display: "contents" }}>
                          <Box sx={{ display: "grid", placeItems: "center" }}>
                            {c.pk && (
                              <CustomTooltip show arrow size="small" title="Primary key">
                                <Iconify icon="solar:key-minimalistic-linear" width={12} sx={{ color: "warning.main" }} />
                              </CustomTooltip>
                            )}
                          </Box>
                          <Typography sx={{ typography: "s2", fontFamily: MONO, fontWeight: c.pk ? 600 : 500 }}>{c.name}</Typography>
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
SchemaCard.propTypes = { schema: PropTypes.array.isRequired, sample: PropTypes.bool };
