import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Collapse, Chip, IconButton, Tooltip, Menu, MenuItem } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { schemaFor, toolImplFor, checkImplFor, classifyToolEffect } from "src/api/simulate-environments/_fixtures/envInternals";
import SectionCard from "../../components/SectionCard";
import MockBadge from "../../components/MockBadge";

// A seeded-from-template env is read-only until forked; the tool-effect override
// chip carries this on its tooltip while locked.
const LOCK_TOOLTIP = "Fork this environment to edit.";

/**
 * "World" internals on the Contract tab.
 *
 * Three sections: DB schema, tool implementations, check implementations.
 * Rows are compact by default — click one open to inspect columns or read
 * the handler code. This keeps Contract as the single home for "what this
 * env is made of" instead of hiding the concrete code behind a second tab.
 *
 * The schema, handler code and check code are synthesised from the env
 * manifest by the `_fixtures/envInternals` helpers, so every card carries a
 * MockBadge — this is sample detail, not read from the real job.
 */
export default function WorldInternalsSection({ env, envState, patch, locked = false }) {
  const schema = schemaFor(env);
  const tools = env?.tools || [];
  const evals = envState?.evals || env?.evalPreset || [];

  const totalRows = schema.reduce((a, t) => a + (t.rows || 0), 0);

  return (
    <Stack spacing={2} sx={{ mb: 2 }}>
      {schema.length > 0 && (
        <SchemaCard schema={schema} totalRows={totalRows} />
      )}
      {tools.length > 0 && (
        <ToolImplsCard tools={tools} envState={envState} patch={patch} locked={locked} />
      )}
      {evals.length > 0 && <CheckImplsCard evals={evals} />}
    </Stack>
  );
}
WorldInternalsSection.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  patch: PropTypes.func,
  locked: PropTypes.bool,
};

/* ── DB schema ────────────────────────────────────────────────────────── */

function SchemaCard({ schema, totalRows }) {
  const [openTable, setOpenTable] = useState(null);
  return (
    <SectionCard
      title="World state · database schema"
      subtitle="The tables and columns that make up this environment's world"
      action={
        <Stack direction="row" alignItems="center" spacing={1}>
          <MockBadge />
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
            {schema.length} tables · {totalRows.toLocaleString()} rows
          </Typography>
        </Stack>
      }
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {schema.map((t) => {
          const open = openTable === t.name;
          return (
            <Box key={t.name}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                onClick={() => setOpenTable(open ? null : t.name)}
                sx={{ px: 2.5, py: 1.375, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
              >
                <Iconify
                  icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
                  width={13}
                  sx={{ color: "text.subtitle", flexShrink: 0 }}
                />
                <Iconify icon="solar:database-linear" width={15} sx={{ color: "#2563EB", flexShrink: 0 }} />
                <Typography
                  sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace", minWidth: 140 }}
                >
                  {t.name}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }} noWrap>
                  {t.cols.length} columns · {t.note}
                </Typography>
                <Typography
                  sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums", flexShrink: 0 }}
                >
                  {(t.rows || 0).toLocaleString()} rows
                </Typography>
              </Stack>
              <Collapse in={open}>
                <Box
                  sx={{
                    px: 2.5,
                    py: 1.5,
                    bgcolor: "background.neutral",
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Box
                    sx={{
                      display: "grid",
                      gridTemplateColumns: "20px 1fr 1fr",
                      rowGap: 0.5,
                      columnGap: 1.5,
                    }}
                  >
                    {t.cols.map((c) => (
                      <SchemaColumnRow key={c.name} col={c} />
                    ))}
                  </Box>
                </Box>
              </Collapse>
            </Box>
          );
        })}
      </Stack>
    </SectionCard>
  );
}
SchemaCard.propTypes = { schema: PropTypes.array, totalRows: PropTypes.number };

function SchemaColumnRow({ col }) {
  return (
    <>
      <Box sx={{ display: "grid", placeItems: "center" }}>
        {col.pk && (
          <Tooltip arrow title="Primary key">
            <Iconify icon="solar:key-minimalistic-linear" width={11} sx={{ color: "#CA8A04" }} />
          </Tooltip>
        )}
      </Box>
      <Typography sx={{ typography: "s2", fontFamily: "ui-monospace, Menlo, monospace", fontWeight: col.pk ? 700 : 600 }}>
        {col.name}
      </Typography>
      <Typography sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", color: "text.subtitle" }}>
        {col.type}
      </Typography>
    </>
  );
}
SchemaColumnRow.propTypes = { col: PropTypes.object };

/* ── tool implementations ─────────────────────────────────────────────── */

function ToolImplsCard({ tools, envState, patch, locked = false }) {
  const [openTool, setOpenTool] = useState(null);
  const overrides = envState?.toolResolutions || {};
  const setOverride = (name, kind) => {
    if (!patch) return;
    patch({ toolResolutions: { ...overrides, [name]: kind } });
  };
  return (
    <SectionCard
      title="Tool implementations"
      subtitle="The handler code each tool call runs against this environment"
      action={
        <Stack direction="row" alignItems="center" spacing={1}>
          <MockBadge />
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
            {tools.length} tools
          </Typography>
        </Stack>
      }
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {tools.map((tool) => {
          const open = openTool === tool.name;
          const impl = toolImplFor(tool);
          const overridden = overrides[tool.name];
          // Prefer an override, then a real classification the job carried on
          // the tool, and only then fall back to the verb heuristic.
          const real = tool.effect || tool.kind;
          const effect = overridden || real || classifyToolEffect(tool).kind;
          const isInferred = !overridden;
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
                <Typography
                  sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace", minWidth: 220 }}
                >
                  {tool.name}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }} noWrap>
                  {tool.desc}
                </Typography>
                {/*
                  The Menu portals out but React synthetic events still bubble
                  to this row's onClick, so a menu/backdrop click would toggle
                  the Collapse. Contain both chip and menu clicks here.
                */}
                <Box sx={{ display: "flex" }} onClick={(e) => e.stopPropagation()}>
                  <EffectPicker
                    effect={effect}
                    isInferred={isInferred}
                    onPick={(next) => setOverride(tool.name, next)}
                    locked={locked}
                  />
                </Box>
                <Chip
                  size="small"
                  label={impl.file.split("/").pop()}
                  sx={{
                    height: 18,
                    borderRadius: 0.5,
                    color: "text.subtitle",
                    border: "1px solid",
                    borderColor: "divider",
                    bgcolor: "transparent",
                    "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" },
                  }}
                />
              </Stack>
              <Collapse in={open}>
                <CodeBlock file={impl.file} code={impl.code} />
              </Collapse>
            </Box>
          );
        })}
      </Stack>
    </SectionCard>
  );
}
ToolImplsCard.propTypes = { tools: PropTypes.array, envState: PropTypes.object, patch: PropTypes.func, locked: PropTypes.bool };

/**
 * Read/write effect chip with a small override menu.
 *
 * The verb heuristic picks a default (`inferred` tag), a stored
 * override wins. Clicking the chip opens a menu with the two choices
 * so a reader can correct the reader without leaving the row.
 */
function EffectPicker({ effect, isInferred, onPick, locked = false }) {
  const [anchor, setAnchor] = useState(null);
  const isWrite = effect === "write";
  const tint = isWrite ? "#CA8A04" : "#16A34A";
  const label = isWrite ? "writes" : "read-only";
  const chipSx = {
    height: 20, borderRadius: 0.75,
    color: tint,
    bgcolor: (t) => alpha(tint, t.palette.mode === "dark" ? 0.14 : 0.08),
    border: "1px solid", borderColor: (t) => alpha(tint, t.palette.mode === "dark" ? 0.35 : 0.3),
    "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 700 },
  };
  const effectIcon = (
    <Iconify
      icon={isWrite ? "solar:pen-linear" : "solar:eye-linear"}
      width={11}
      sx={{ ml: "6px !important", color: `${tint} !important` }}
    />
  );
  const chipLabel = isInferred ? `${label} · inferred` : label;

  // A locked template can't re-classify a tool's effect, so the chip drops its
  // menu entirely and renders as a static read-out with the fork tooltip.
  if (locked) {
    return (
      <Tooltip arrow title={LOCK_TOOLTIP}>
        <Chip
          size="small"
          icon={effectIcon}
          label={chipLabel}
          sx={{ ...chipSx, cursor: "default" }}
        />
      </Tooltip>
    );
  }

  return (
    <>
      <Chip
        size="small"
        onClick={(e) => { e.stopPropagation(); setAnchor(e.currentTarget); }}
        icon={effectIcon}
        label={chipLabel}
        sx={{ ...chipSx, cursor: "pointer" }}
      />
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 220, mt: 0.5 } } }}
      >
        <EffectMenuItem
          selected={!isWrite}
          icon="solar:eye-linear"
          label="Read-only"
          hint="scenarios call it directly"
          onClick={() => { onPick("read"); setAnchor(null); }}
        />
        <EffectMenuItem
          selected={isWrite}
          icon="solar:pen-linear"
          label="Writes data"
          hint="writes hit the sandbox, never production"
          onClick={() => { onPick("write"); setAnchor(null); }}
        />
      </Menu>
    </>
  );
}
EffectPicker.propTypes = {
  effect: PropTypes.string, isInferred: PropTypes.bool, onPick: PropTypes.func, locked: PropTypes.bool,
};

function EffectMenuItem({ selected, icon, label, hint, onClick }) {
  return (
    <MenuItem onClick={onClick} sx={{ alignItems: "flex-start", gap: 1.25, py: 1 }}>
      <Iconify
        icon={selected ? "solar:check-circle-bold" : icon}
        width={16}
        sx={{ color: selected ? "primary.main" : "text.subtitle", mt: "2px", flexShrink: 0 }}
      />
      <Box minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>{label}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "normal" }}>{hint}</Typography>
      </Box>
    </MenuItem>
  );
}
EffectMenuItem.propTypes = {
  selected: PropTypes.bool, icon: PropTypes.string, label: PropTypes.string,
  hint: PropTypes.string, onClick: PropTypes.func,
};

/* ── check implementations ────────────────────────────────────────────── */

function CheckImplsCard({ evals }) {
  const [openCheck, setOpenCheck] = useState(null);
  return (
    <SectionCard
      title="Check implementations"
      subtitle="The grader code that decides pass or fail on every scenario"
      action={
        <Stack direction="row" alignItems="center" spacing={1}>
          <MockBadge />
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
            {evals.length} checks
          </Typography>
        </Stack>
      }
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {evals.map((evalItem) => {
          const id = typeof evalItem === "string" ? evalItem : evalItem.id;
          const open = openCheck === id;
          const impl = checkImplFor(id);
          return (
            <Box key={id}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                onClick={() => setOpenCheck(open ? null : id)}
                sx={{ px: 2.5, py: 1.375, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
              >
                <Iconify
                  icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
                  width={13}
                  sx={{ color: "text.subtitle", flexShrink: 0 }}
                />
                <Iconify icon="solar:shield-check-linear" width={15} sx={{ color: "#16A34A", flexShrink: 0 }} />
                <Typography
                  sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace", minWidth: 220 }}
                >
                  {id}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }} noWrap>
                  {impl.desc}
                </Typography>
                <Chip
                  size="small"
                  label={impl.file.split("/").pop()}
                  sx={{
                    height: 18,
                    borderRadius: 0.5,
                    color: "text.subtitle",
                    border: "1px solid",
                    borderColor: "divider",
                    bgcolor: "transparent",
                    "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" },
                  }}
                />
              </Stack>
              <Collapse in={open}>
                <CodeBlock file={impl.file} code={impl.code} />
              </Collapse>
            </Box>
          );
        })}
      </Stack>
    </SectionCard>
  );
}
CheckImplsCard.propTypes = { evals: PropTypes.array };

/* ── shared code viewer ───────────────────────────────────────────────── */

function CodeBlock({ file, code }) {
  const copy = () => {
    navigator.clipboard?.writeText(code);
    enqueueSnackbar("Copied to clipboard", { variant: "info" });
  };
  return (
    <Box
      sx={{
        borderTop: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2.5,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.03),
        }}
      >
        <Iconify icon="solar:file-text-linear" width={12} sx={{ color: "text.subtitle" }} />
        <Typography
          sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", color: "text.secondary", flex: 1 }}
        >
          {file}
        </Typography>
        <Tooltip arrow title="Copy code">
          <IconButton size="small" onClick={copy}>
            <Iconify icon="solar:copy-linear" width={12} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Tooltip>
      </Stack>
      <Box sx={{ px: 2.5, py: 1.5, maxHeight: 360, overflow: "auto" }}>
        <Typography
          component="pre"
          sx={{
            typography: "s3",
            fontFamily: "ui-monospace, Menlo, monospace",
            color: "text.primary",
            whiteSpace: "pre",
            m: 0,
            lineHeight: 1.6,
          }}
        >
          {code}
        </Typography>
      </Box>
    </Box>
  );
}
CodeBlock.propTypes = { file: PropTypes.string, code: PropTypes.string };
