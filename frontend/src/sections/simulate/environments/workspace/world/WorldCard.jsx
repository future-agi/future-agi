import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  ButtonBase,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import SectionCard from "../../components/SectionCard";
import ViewerLock from "../../components/ViewerLock";
import {
  MONO,
  TOOL_DRAWER_COPY as D,
  TOOL_STATUS_ORDER,
  WORLD_CARD_COPY as C,
  placeTools,
} from "./world.constants";

const REMOVAL = PropTypes.shape({
  onRemove: PropTypes.func.isRequired,
  blockedReason: PropTypes.string,
});

const GRID = "12px minmax(160px, 220px) 90px 80px minmax(0, 1fr)";

function ToolChip({ tool, changes, open, onOpen, removal }) {
  const Base = onOpen ? ButtonBase : Box;
  return (
    <Tooltip
      arrow
      title={
        changes
          ? C.changesThis(tool.description)
          : C.readsThis(tool.description)
      }
    >
      <Base
        onClick={onOpen ? () => onOpen(tool) : undefined}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.25,
          px: 0.875,
          py: 0.25,
          borderRadius: "6px",
          border: "1px solid",
          borderColor: "divider",
          bgcolor: open ? "action.selected" : "transparent",
          ...(onOpen && {
            "&:hover": { bgcolor: open ? "action.selected" : "action.hover" },
          }),
        }}
      >
        <Typography
          component="span"
          sx={{ typography: "s3", fontFamily: MONO }}
        >
          {tool.tool}
        </Typography>
        {!onOpen && removal && (
          <ViewerLock
            locked={Boolean(removal.blockedReason)}
            title={removal.blockedReason || D.remove}
          >
            <IconButton
              size="small"
              aria-label={D.removeNamed(tool.tool)}
              disabled={Boolean(removal.blockedReason)}
              onClick={() => removal.onRemove(tool)}
              sx={{ p: 0.25, mr: -0.5, color: "text.tertiary" }}
            >
              <Iconify icon="solar:close-circle-linear" width={14} />
            </IconButton>
          </ViewerLock>
        )}
      </Base>
    </Tooltip>
  );
}

ToolChip.propTypes = {
  tool: PropTypes.shape({
    tool: PropTypes.string,
    description: PropTypes.string,
  }).isRequired,
  changes: PropTypes.bool,
  open: PropTypes.bool,
  onOpen: PropTypes.func,
  removal: REMOVAL,
};

function TierStrip({ tools }) {
  const groups = TOOL_STATUS_ORDER.map((status) => ({
    status,
    count: tools.filter((t) => (t.status || "unknown") === status).length,
  })).filter((group) => group.count > 0);
  return groups.map((group) => {
    const tier = C.tier[group.status] || C.tier.unknown;
    return (
      <Stack
        key={group.status}
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 0.75,
          bgcolor: "background.neutral",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s3", fontWeight: 600, flexShrink: 0 }}>
          {C.tierLabel(group.count, tools.length, tier.word)}
        </Typography>
        <OverflowTooltip title={tier.note}>
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.tertiary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {tier.note}
          </Typography>
        </OverflowTooltip>
      </Stack>
    );
  });
}

function Chips({ entries, openTool, onOpenTool, removal }) {
  if (!entries.length)
    return (
      <Typography sx={{ typography: "s3", color: "text.tertiary", pt: 0.25 }}>
        {C.noToolUsesIt}
      </Typography>
    );
  return (
    <Stack direction="row" flexWrap="wrap" sx={{ gap: 0.75 }}>
      {entries.map(({ tool, changes }) => (
        <ToolChip
          key={tool.tool}
          tool={tool}
          changes={changes}
          open={openTool === tool.tool}
          onOpen={onOpenTool}
          removal={removal}
        />
      ))}
    </Stack>
  );
}

Chips.propTypes = {
  entries: PropTypes.arrayOf(PropTypes.object).isRequired,
  openTool: PropTypes.string,
  onOpenTool: PropTypes.func,
  removal: REMOVAL,
};

function TableRow({ table, entries, openTool, onOpenTool, removal }) {
  const [open, setOpen] = useState(false);
  const fields = table.fields || [];
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
      <Box
        onClick={() => setOpen((value) => !value)}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setOpen((value) => !value);
          }
        }}
        sx={{
          display: "grid",
          gridTemplateColumns: GRID,
          columnGap: 1.5,
          alignItems: "start",
          px: 2,
          py: 1,
          cursor: "pointer",
          bgcolor: open ? "action.selected" : "transparent",
          "&:hover": { bgcolor: open ? "action.selected" : "action.hover" },
        }}
      >
        <Iconify
          icon={
            open
              ? "solar:alt-arrow-down-linear"
              : "solar:alt-arrow-right-linear"
          }
          width={12}
          sx={{ color: "text.tertiary", mt: 0.5 }}
        />
        <Box minWidth={0}>
          <Typography
            sx={{ typography: "s2", fontWeight: 500, fontFamily: MONO }}
          >
            {table.name}
          </Typography>
          <OverflowTooltip title={table.note || ""}>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {table.note || ""}
            </Typography>
          </OverflowTooltip>
        </Box>
        <Typography
          sx={{
            typography: "s3",
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
            pt: 0.25,
          }}
        >
          {table.rows != null ? Number(table.rows).toLocaleString() : ""}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
            pt: 0.25,
          }}
        >
          {fields.length}
        </Typography>
        <Box onClick={(event) => event.stopPropagation()}>
          <Chips
            entries={entries}
            openTool={openTool}
            onOpenTool={onOpenTool}
            removal={removal}
          />
        </Box>
      </Box>
      {open && fields.length > 0 && (
        <Box
          sx={{
            px: 2,
            pl: 5,
            py: 1.5,
            bgcolor: "background.neutral",
            borderTop: "1px solid",
            borderColor: "divider",
            display: "grid",
            gridTemplateColumns: "20px 1fr 1fr",
            rowGap: 0.5,
            columnGap: 1.5,
          }}
        >
          {fields.map((field) => (
            <Box key={field.name} sx={{ display: "contents" }}>
              <Box sx={{ display: "flex", alignItems: "center" }}>
                {field.pk && (
                  <Tooltip arrow title={C.primaryKey}>
                    <Box component="span" sx={{ display: "inline-flex" }}>
                      <Iconify
                        icon="solar:key-minimalistic-linear"
                        width={12}
                        sx={{ color: "status.warning.fg" }}
                      />
                    </Box>
                  </Tooltip>
                )}
              </Box>
              <Typography
                sx={{
                  typography: "s2",
                  fontFamily: MONO,
                  fontWeight: field.pk ? 600 : 500,
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
                {field.type || ""}
              </Typography>
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
}

TableRow.propTypes = {
  table: PropTypes.shape({
    name: PropTypes.string.isRequired,
    note: PropTypes.string,
    rows: PropTypes.number,
    fields: PropTypes.arrayOf(PropTypes.object),
  }).isRequired,
  entries: PropTypes.arrayOf(PropTypes.object).isRequired,
  openTool: PropTypes.string,
  onOpenTool: PropTypes.func,
  removal: REMOVAL,
};

export default function WorldCard({
  tables,
  tools,
  totalRows,
  openTool,
  onOpenTool,
  removal,
  sx,
}) {
  const { byTable, none } = placeTools(tables, tools);
  const rows =
    totalRows ?? tables.reduce((total, t) => total + (Number(t.rows) || 0), 0);
  return (
    <SectionCard
      roomy
      title={C.title(tables.length, tools.length)}
      subtitle={C.subtitle(rows)}
      sx={sx}
    >
      {tools.length > 0 && <TierStrip tools={tools} />}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: GRID,
          columnGap: 1.5,
          px: 2,
          py: 0.75,
        }}
      >
        {C.head.map((label, index) => (
          <Typography
            key={label || `blank-${index}`}
            sx={{ typography: "s3", color: "text.tertiary" }}
          >
            {label}
          </Typography>
        ))}
      </Box>
      {tables.map((table) => (
        <TableRow
          key={table.name}
          table={table}
          entries={byTable.get(table.name) || []}
          openTool={openTool}
          onOpenTool={onOpenTool}
          removal={removal}
        />
      ))}
      {none.length > 0 && (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: GRID,
            columnGap: 1.5,
            alignItems: "start",
            px: 2,
            py: 1,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {C.noTable}
          </Typography>
          <Box />
          <Box />
          <Chips
            entries={none}
            openTool={openTool}
            onOpenTool={onOpenTool}
            removal={removal}
          />
        </Box>
      )}
    </SectionCard>
  );
}

WorldCard.propTypes = {
  tables: PropTypes.arrayOf(PropTypes.object).isRequired,
  tools: PropTypes.arrayOf(PropTypes.object).isRequired,
  totalRows: PropTypes.number,
  openTool: PropTypes.string,
  onOpenTool: PropTypes.func,
  removal: REMOVAL,
  sx: PropTypes.object,
};
