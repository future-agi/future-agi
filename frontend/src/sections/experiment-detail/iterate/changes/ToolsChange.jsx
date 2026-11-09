import React, { useState } from "react";
import PropTypes from "prop-types";
import { Button, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { NumberInput, ParamRow, SmallSelect } from "./changeParts";

const MODES = [
  { value: "mock", label: "Mock" },
  { value: "recorded", label: "Recorded" },
  { value: "live", label: "Live" },
];
const MIXED = "__mixed";
const label = (m) => MODES.find((x) => x.value === m)?.label ?? m;

/**
 * Tools tab: the Prompt card's Tools block in compact form (R2-SPEC
 * §5.3.2): `Tools: all Mock ▾`; per-tool modes and `Max tool turns` under
 * `Advanced`.
 */
const ToolsChange = ({ dock }) => {
  const { parent, draft, patch, canEdit } = dock;
  const [advanced, setAdvanced] = useState(false);
  const tools = parent.config?.tools ?? [];
  const modes = draft.tools?.modes ?? {};
  const modeOf = (t) => modes[t.name] ?? t.mode;
  const all = new Set(tools.map(modeOf));
  const allValue = all.size === 1 ? [...all][0] : MIXED;
  const setModes = (next) =>
    patch((d) => ({ tools: { ...(d.tools ?? {}), modes: next } }));
  const turns =
    draft.tools?.maxToolTurns ?? parent.config?.max_tool_turns ?? null;
  return (
    <Stack spacing={0.75}>
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography typography="s2" color="text.secondary">
          Tools: all
        </Typography>
        <SmallSelect
          label="Mode for every tool"
          value={allValue}
          disabled={!canEdit}
          options={[
            ...MODES,
            ...(allValue === MIXED ? [{ value: MIXED, label: "Mixed" }] : []),
          ]}
          onChange={(v) =>
            v !== MIXED &&
            setModes(Object.fromEntries(tools.map((t) => [t.name, v])))
          }
        />
        <Typography typography="s3" color="text.disabled">
          {`${tools.length} ${tools.length === 1 ? "tool" : "tools"}`}
        </Typography>
      </Stack>
      <Button
        size="small"
        color="inherit"
        onClick={() => setAdvanced((v) => !v)}
        endIcon={
          <Iconify
            icon={advanced ? "mdi:chevron-up" : "mdi:chevron-down"}
            width={16}
          />
        }
        sx={{
          alignSelf: "flex-start",
          typography: "s2",
          color: "text.secondary",
        }}
      >
        Advanced
      </Button>
      <Collapse in={advanced} unmountOnExit>
        <Stack spacing={0.25}>
          {tools.map((t) => (
            <ParamRow
              key={t.name}
              label={t.name}
              parent={label(t.mode)}
              changed={modeOf(t) !== t.mode}
            >
              <SmallSelect
                label={`${t.name} mode`}
                value={modeOf(t)}
                options={MODES}
                disabled={!canEdit}
                onChange={(v) => setModes({ ...modes, [t.name]: v })}
              />
            </ParamRow>
          ))}
          <ParamRow
            label="Max tool turns"
            parent={parent.config?.max_tool_turns ?? "not set"}
            changed={turns !== (parent.config?.max_tool_turns ?? null)}
          >
            <NumberInput
              label="Max tool turns"
              value={turns}
              min={1}
              max={50}
              disabled={!canEdit}
              onChange={(v) =>
                patch((d) => ({
                  tools: { ...(d.tools ?? {}), maxToolTurns: v },
                }))
              }
            />
          </ParamRow>
        </Stack>
      </Collapse>
    </Stack>
  );
};

ToolsChange.propTypes = { dock: PropTypes.object.isRequired };

export default ToolsChange;
