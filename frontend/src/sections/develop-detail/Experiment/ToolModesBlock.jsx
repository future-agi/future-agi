import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Collapse,
  Divider,
  IconButton,
  InputAdornment,
  ListItemIcon,
  ListItemText,
  ListSubheader,
  Menu,
  MenuItem,
  Popover,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { enqueueSnackbar } from "src/components/snackbar";
import { copyToClipboard } from "src/utils/utils";
import ConfigTool from "../RunPrompt/ToolConfig/ConfigTool";
import { FieldTag } from "src/sections/common/ValueViewer/FieldCard";
import {
  AUTO_FIXTURES_HELP,
  MENU_HELP,
  blockHelpFor,
  menuHelpFor,
  recordedHelpFor,
  DEFAULT_MAX_TOOL_TURNS,
  MAX_TOOL_TURNS_RANGE,
  ON_UNRECORDED,
  TOOL_MODES,
  cardTools,
  effectiveMode,
  fixtureCountText,
  fixtureMatchText,
  fixtureResultText,
  fullSignature,
  isHttpUrl,
  liveUrlHint,
  modeSummary,
  paramSignature,
  recordedAvailable,
  toolWarnings,
  versionToolsOf,
} from "./toolSetup";

const MONO = { fontFamily: "monospace", fontSize: 12 };

/** A tool name chip; hover shows `lookup_order(order_id: string)`. */
const ToolChip = ({ tool }) => (
  <CustomTooltip
    show
    title={[fullSignature(tool), tool.description].filter(Boolean).join(" · ")}
    placement="top"
    size="small"
    type="black"
  >
    <Box component="span" sx={{ display: "inline-flex", minWidth: 0 }}>
      <FieldTag>{tool.name}</FieldTag>
    </Box>
  </CustomTooltip>
);
ToolChip.propTypes = { tool: PropTypes.object.isRequired };

/**
 * `66 auto fixtures (6 tools) · View`: read-only list of what Mock answers,
 * every case of every tool (the cases the run's Mock calls answer from).
 */
const FixturesLink = ({ fixtures }) => {
  const [anchor, setAnchor] = useState(null);
  if (!fixtures?.length) return null;
  return (
    <>
      <Stack direction="row" alignItems="center" gap={0.5}>
        <Typography typography="s2" color="text.secondary">
          {`${fixtureCountText(fixtures)} ·`}
        </Typography>
        <Button
          size="small"
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{ typography: "s2", minWidth: 0, px: 0.5, py: 0 }}
        >
          View
        </Button>
      </Stack>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{
          paper: { sx: { p: 1.5, width: 460, maxHeight: 420, mt: 0.5 } },
        }}
      >
        <Typography typography="s1" fontWeight="fontWeightMedium">
          Auto fixtures
        </Typography>
        <Typography typography="s3" color="text.secondary" sx={{ mb: 1 }}>
          What a Mock tool answers, by its input. Read-only.
        </Typography>
        <Stack spacing={1.25} divider={<Divider flexItem />}>
          {fixtures.map((f) => (
            <Stack key={f.tool} spacing={0.5}>
              <Stack direction="row" alignItems="center" gap={0.75}>
                <FieldTag>{f.tool}</FieldTag>
                <Typography typography="s3" color="text.secondary">
                  {`${(f.cases ?? []).length} ${(f.cases ?? []).length === 1 ? "case" : "cases"}`}
                </Typography>
              </Stack>
              {(f.cases ?? []).map((c, i) => (
                <Stack key={i} spacing={0.25} sx={{ userSelect: "text" }}>
                  <Typography sx={{ ...MONO, color: "text.secondary" }}>
                    {fixtureMatchText(c.match)}
                  </Typography>
                  <Box
                    sx={{
                      ...MONO,
                      px: 1,
                      py: 0.5,
                      borderRadius: 0.5,
                      bgcolor: "action.hover",
                      wordBreak: "break-word",
                    }}
                  >
                    {`→ ${fixtureResultText(c.result)}`}
                  </Box>
                </Stack>
              ))}
            </Stack>
          ))}
        </Stack>
      </Popover>
    </>
  );
};
FixturesLink.propTypes = { fixtures: PropTypes.array };

/** Live endpoint of one tool, with copy. */
const EndpointField = ({ name, value, onChange }) => {
  const valid = isHttpUrl(value);
  return (
    <Stack spacing={0.25} sx={{ pl: 0.5 }}>
      <TextField
        size="small"
        value={value ?? ""}
        placeholder={liveUrlHint(name)}
        onChange={(e) => onChange(e.target.value)}
        error={!valid}
        inputProps={{ "aria-label": `${name} endpoint` }}
        sx={{ "& .MuiOutlinedInput-root": { height: 30, ...MONO } }}
        InputProps={{
          endAdornment: valid ? (
            <InputAdornment position="end">
              <IconButton
                size="small"
                aria-label="Copy endpoint"
                onClick={() => {
                  copyToClipboard(value.trim());
                  enqueueSnackbar("Endpoint copied", { variant: "success" });
                }}
              >
                <Iconify icon="mdi:content-copy" width={14} />
              </IconButton>
            </InputAdornment>
          ) : null,
        }}
      />
      <Typography
        typography="s3"
        color={valid ? "text.secondary" : "error.main"}
      >
        {valid
          ? TOOL_MODES.live.help.replace("{url}", value.trim())
          : `Live tool ${name} has no endpoint`}
      </Typography>
    </Stack>
  );
};
EndpointField.propTypes = {
  name: PropTypes.string,
  value: PropTypes.string,
  onChange: PropTypes.func,
};

/**
 * Tools of a Prompt run card (R2-SPEC §5.3.2): the prompt version's tools
 * plus any added with `+ Add tool`. Default: one line `Tools (6): all Mock
 * ▾ · auto fixtures` and its help. `Advanced` (collapsed) holds per-tool
 * modes, fixtures, max tool turns and the missing-recording fallback.
 */
const ToolModesBlock = ({
  control,
  setValue,
  getValues,
  fieldPrefix,
  version,
  promptName,
  rowCount,
}) => {
  const setupField = `${fieldPrefix}.toolSetup`;
  const setup = useWatch({ control, name: setupField }) ?? {};
  const added = useWatch({
    control,
    name: `${fieldPrefix}.configuration.tools`,
  });
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [advanced, setAdvanced] = useState(false);
  const [adding, setAdding] = useState(false);
  const rootRef = useRef(null);

  // Mirror the selected version's tools, fixtures and recordings.
  const versionId = version?.id ?? null;
  useEffect(() => {
    if (!versionId) return;
    const cur = getValues(setupField) ?? {};
    if (cur.versionId === versionId) return;
    const wire = version?.prompt_config_snapshot?.tool_fixtures ?? {};
    const snap = version?.prompt_config_snapshot?.configuration ?? {};
    setValue(setupField, {
      mode: "mock",
      perTool: {},
      endpoints: {},
      onUnrecorded: "mock",
      ...cur,
      versionId,
      versionLabel: [promptName, version?.template_version]
        .filter(Boolean)
        .join(" "),
      versionTools: versionToolsOf(version),
      fixtures: (wire.fixtures ?? []).map((f) => ({
        tool: f.tool,
        cases: f.cases ?? [],
      })),
      recorded: {
        available: Boolean(wire.recorded_available),
        reason: wire.recorded_reason ?? null,
        sessions: Number(wire.recorded_sessions ?? 0),
      },
      maxToolTurns:
        cur.versionId && cur.maxToolTurns
          ? cur.maxToolTurns
          : snap.max_tool_turns ?? DEFAULT_MAX_TOOL_TURNS,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionId]);

  const tools = cardTools(setup, added);
  const canRecord = recordedAvailable(setup);
  const patch = (next) =>
    setValue(
      setupField,
      { ...(getValues(setupField) ?? {}), ...next },
      {
        shouldDirty: true,
      },
    );
  const setToolMode = (name, mode) => {
    if (!mode) return;
    const perTool = Object.fromEntries(
      tools.map((t) => [t.name, effectiveMode(setup, t.name)]),
    );
    patch({ mode: "per_tool", perTool: { ...perTool, [name]: mode } });
  };
  const chooseAll = (mode) => {
    setMenuAnchor(null);
    patch({ mode });
  };
  const openAdvanced = () => {
    setMenuAnchor(null);
    setAdvanced(true);
  };

  const picker = (
    <Collapse in={adding} unmountOnExit>
      <Box sx={{ pt: 0.5 }}>
        <ConfigTool
          control={control}
          fieldName={`${fieldPrefix}.configuration.tools`}
        />
      </Box>
    </Collapse>
  );

  // No tools: one control, `+ Add tool`.
  if (!tools.length)
    return (
      <Stack spacing={0.5} ref={rootRef} data-testid="tool-modes-block">
        <Stack direction="row" alignItems="center" gap={0.75}>
          <Iconify
            icon="mdi:wrench-outline"
            width={14}
            sx={{ color: "text.secondary" }}
          />
          <Typography typography="s2" color="text.secondary">
            Tools: none
          </Typography>
          <Button
            size="small"
            startIcon={<Iconify icon="mdi:plus" width={14} />}
            onClick={() => setAdding((v) => !v)}
            sx={{ typography: "s2", px: 0.75, py: 0 }}
          >
            Add tool
          </Button>
        </Stack>
        {picker}
      </Stack>
    );

  const summary = modeSummary(setup, tools);
  const warnings = toolWarnings(
    { toolSetup: setup, configuration: { tools: added } },
    { rowCount },
  );
  const fromLine = setup.versionTools?.length
    ? `Tools (${setup.versionTools.length}) from ${setup.versionLabel}`
    : `Tools (${tools.length})`;

  return (
    <Stack spacing={0.25} ref={rootRef} data-testid="tool-modes-block">
      <Stack direction="row" alignItems="center" gap={0.5}>
        <Iconify
          icon="mdi:wrench-outline"
          width={14}
          sx={{ color: "text.secondary" }}
        />
        <Button
          size="small"
          color="inherit"
          endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
          onClick={(e) => setMenuAnchor(e.currentTarget)}
          aria-haspopup="menu"
          data-testid="tool-modes-trigger"
          sx={{
            typography: "s2",
            fontWeight: "fontWeightMedium",
            px: 0.5,
            py: 0,
            minWidth: 0,
            "& .MuiButton-endIcon": { ml: 0.25 },
          }}
        >
          {/* IT-R2 G1 P10: name the tools the version carries. */}
          {`Tools: ${tools
            .slice(0, 3)
            .map((t) => t.name)
            .join(
              ", ",
            )}${tools.length > 3 ? ` +${tools.length - 3}` : ""} · ${summary}`}
        </Button>
        <CustomTooltip
          show
          title={AUTO_FIXTURES_HELP}
          size="small"
          placement="top"
        >
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{
              cursor: "help",
              borderBottom: "1px dotted",
              borderColor: "text.disabled",
            }}
          >
            · auto fixtures
          </Typography>
        </CustomTooltip>
      </Stack>
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{ pl: 2.5 }}
        data-testid="tool-modes-help"
      >
        {blockHelpFor(setup.mode, setup.onUnrecorded)}
      </Typography>

      <Menu
        open={Boolean(menuAnchor)}
        anchorEl={menuAnchor}
        onClose={() => setMenuAnchor(null)}
        slotProps={{ paper: { sx: { minWidth: 260, mt: 0.5 } } }}
        MenuListProps={{ dense: true }}
      >
        <ListSubheader
          disableSticky
          sx={{ lineHeight: 1.8, typography: "s3", bgcolor: "transparent" }}
        >
          {fromLine}
        </ListSubheader>
        {["mock", ...(canRecord ? ["recorded"] : [])].map((m) => (
          <MenuItem
            key={m}
            selected={setup.mode === m}
            onClick={() => chooseAll(m)}
          >
            <ListItemIcon>
              <Iconify icon={TOOL_MODES[m].icon} width={16} />
            </ListItemIcon>
            <ListItemText
              primaryTypographyProps={{ typography: "s1" }}
              secondary={menuHelpFor(m, setup.onUnrecorded)}
              secondaryTypographyProps={{
                typography: "s3",
                sx: { whiteSpace: "normal", maxWidth: 300 },
              }}
            >
              {`All ${TOOL_MODES[m].label}`}
            </ListItemText>
          </MenuItem>
        ))}
        <MenuItem
          selected={setup.mode === "per_tool"}
          onClick={() => {
            patch({
              mode: "per_tool",
              perTool: Object.fromEntries(
                tools.map((t) => [t.name, effectiveMode(setup, t.name)]),
              ),
            });
            openAdvanced();
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:tune-variant" width={16} />
          </ListItemIcon>
          <ListItemText
            primaryTypographyProps={{ typography: "s1" }}
            secondary={MENU_HELP.per_tool}
            secondaryTypographyProps={{
              typography: "s3",
              sx: { whiteSpace: "normal", maxWidth: 300 },
            }}
          >
            Per tool…
          </ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem
          onClick={() => {
            openAdvanced();
            setAdding(true);
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:plus" width={16} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ typography: "s1" }}>
            Add tool
          </ListItemText>
        </MenuItem>
        <MenuItem onClick={openAdvanced}>
          <ListItemIcon>
            <Iconify icon="mdi:cog-outline" width={16} />
          </ListItemIcon>
          <ListItemText
            primaryTypographyProps={{ typography: "s1" }}
            secondary={MENU_HELP.advanced}
            secondaryTypographyProps={{
              typography: "s3",
              sx: { whiteSpace: "normal", maxWidth: 300 },
            }}
          >
            Advanced
          </ListItemText>
        </MenuItem>
      </Menu>

      <Collapse in={advanced} unmountOnExit>
        <Stack
          spacing={1}
          data-testid="tool-modes-advanced"
          sx={{
            mt: 0.75,
            p: 1.25,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 0.5,
            bgcolor: "background.neutral",
          }}
        >
          <Stack direction="row" alignItems="center" gap={1}>
            <Typography typography="s2" fontWeight="fontWeightMedium">
              Advanced
            </Typography>
            <Typography typography="s3" color="text.secondary" noWrap>
              {fromLine}
            </Typography>
            <Box sx={{ flex: 1 }} />
            <Button
              size="small"
              startIcon={<Iconify icon="mdi:plus" width={14} />}
              onClick={() => setAdding((v) => !v)}
              sx={{ typography: "s2", px: 0.75, py: 0 }}
            >
              Add tool
            </Button>
            <IconButton
              size="small"
              aria-label="Hide advanced"
              onClick={() => setAdvanced(false)}
            >
              <Iconify icon="mdi:chevron-up" width={16} />
            </IconButton>
          </Stack>
          {picker}

          <Stack spacing={0.25}>
            {["mock", ...(canRecord ? ["recorded"] : []), "live"].map((m) => (
              <Stack key={m} direction="row" gap={0.75} alignItems="baseline">
                <Iconify
                  icon={TOOL_MODES[m].icon}
                  width={12}
                  sx={{ color: "text.secondary", flexShrink: 0 }}
                />
                <Typography typography="s3" color="text.secondary">
                  <Box component="span" fontWeight={600}>
                    {TOOL_MODES[m].label}:
                  </Box>{" "}
                  {m === "live"
                    ? TOOL_MODES.live.help.replace(
                        "{url}",
                        liveUrlHint(tools[0]?.name),
                      )
                    : m === "recorded"
                      ? recordedHelpFor(setup.onUnrecorded)
                      : TOOL_MODES[m].help}
                </Typography>
              </Stack>
            ))}
          </Stack>

          <Stack spacing={0.75}>
            {tools.map((t) => {
              const mode = effectiveMode(setup, t.name);
              return (
                <Stack key={t.name} spacing={0.5}>
                  <Stack direction="row" alignItems="center" gap={0.75}>
                    <ToolChip tool={t} />
                    <Typography
                      sx={{ ...MONO, color: "text.secondary" }}
                      noWrap
                    >
                      {paramSignature(t)}
                    </Typography>
                    {t.added ? (
                      <Typography typography="s3" color="text.disabled">
                        added
                      </Typography>
                    ) : null}
                    <Box sx={{ flex: 1 }} />
                    <ToggleButtonGroup
                      size="small"
                      exclusive
                      value={mode}
                      onChange={(_, v) => setToolMode(t.name, v)}
                      aria-label={`${t.name} mode`}
                      sx={{
                        "& .MuiToggleButton-root": {
                          typography: "s3",
                          py: 0.125,
                          px: 0.75,
                          gap: 0.5,
                          textTransform: "none",
                        },
                      }}
                    >
                      {["mock", ...(canRecord ? ["recorded"] : []), "live"].map(
                        (m) => (
                          <ToggleButton key={m} value={m}>
                            <Iconify icon={TOOL_MODES[m].icon} width={12} />
                            {TOOL_MODES[m].label}
                          </ToggleButton>
                        ),
                      )}
                    </ToggleButtonGroup>
                  </Stack>
                  {mode === "live" ? (
                    <EndpointField
                      name={t.name}
                      value={setup.endpoints?.[t.name] ?? ""}
                      onChange={(v) =>
                        patch({
                          endpoints: {
                            ...(setup.endpoints ?? {}),
                            [t.name]: v,
                          },
                        })
                      }
                    />
                  ) : null}
                </Stack>
              );
            })}
          </Stack>

          <Divider flexItem />
          <FixturesLink fixtures={setup.fixtures} />
          <Stack direction="row" alignItems="center" gap={1}>
            <Typography typography="s2" color="text.secondary">
              Max tool turns
            </Typography>
            <TextField
              size="small"
              type="number"
              value={setup.maxToolTurns ?? DEFAULT_MAX_TOOL_TURNS}
              onChange={(e) => patch({ maxToolTurns: e.target.value })}
              inputProps={{
                min: MAX_TOOL_TURNS_RANGE.min,
                max: MAX_TOOL_TURNS_RANGE.max,
                "aria-label": "Max tool turns",
              }}
              sx={{ width: 72, "& .MuiOutlinedInput-root": { height: 28 } }}
            />
          </Stack>
          {canRecord ? (
            <Stack direction="row" alignItems="center" gap={1}>
              <Typography typography="s2" color="text.secondary">
                If a recorded call is missing:
              </Typography>
              <TextField
                select
                size="small"
                value={setup.onUnrecorded ?? "mock"}
                onChange={(e) => patch({ onUnrecorded: e.target.value })}
                inputProps={{ "aria-label": "If a recorded call is missing" }}
                sx={{
                  minWidth: 140,
                  "& .MuiOutlinedInput-root": { height: 28, typography: "s2" },
                }}
              >
                {ON_UNRECORDED.map((o) => (
                  <MenuItem key={o.value} value={o.value} dense>
                    {o.label}
                  </MenuItem>
                ))}
              </TextField>
            </Stack>
          ) : null}
        </Stack>
      </Collapse>
      {warnings.map((w) => (
        <Stack
          key={w}
          direction="row"
          gap={0.5}
          alignItems="center"
          sx={{ pl: 2.5, color: "amber.700" }}
        >
          <Iconify icon="mdi:alert-outline" width={14} />
          <Typography typography="s3" color="inherit">
            {w}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
};

ToolModesBlock.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
  getValues: PropTypes.func.isRequired,
  fieldPrefix: PropTypes.string.isRequired,
  version: PropTypes.object,
  promptName: PropTypes.string,
  rowCount: PropTypes.number,
};

export default ToolModesBlock;
