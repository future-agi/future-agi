// IT-R4 G6 P6: one collapsible legend line under the chips, with the real
// cell icons — ● pass · ○ fail · dashed = carried from #2B · ! error ·
// ⊘ didn't run, and on voice data ⚡ interruption · 🔧 tool failure.
// Open / closed is remembered (layout `matrix`, `extras.legendOpen`).
import React from "react";
import PropTypes from "prop-types";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import { useLayoutPrefs } from "src/sections/common/ux/oneview/layoutPrefs";
import { TrialDot } from "./MatrixCell";

function Item({ icon, text, title }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.5}
      title={title ?? text}
      sx={{ whiteSpace: "nowrap" }}
    >
      {icon}
      <Typography component="span" typography="s3" color="text.secondary">
        {text}
      </Typography>
    </Stack>
  );
}
Item.propTypes = {
  icon: PropTypes.node,
  text: PropTypes.string,
  title: PropTypes.string,
};

export default function MatrixLegend({ carriedFrom, voice = false }) {
  const [layout, patch] = useLayoutPrefs("matrix");
  const open = layout.extras?.legendOpen !== false;
  const toggle = () =>
    patch({ extras: { legendOpen: open ? false : undefined } });
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1.5}
      data-testid="matrix-legend"
      sx={{ px: 1, minHeight: 22, flexWrap: "wrap", rowGap: 0.25 }}
    >
      <Button
        size="small"
        color="inherit"
        aria-expanded={open}
        onClick={toggle}
        startIcon={
          <Iconify
            icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
            width={14}
          />
        }
        sx={{
          typography: "s3",
          color: "text.secondary",
          height: 22,
          px: 0.5,
          minWidth: 0,
        }}
      >
        Legend
      </Button>
      {open ? (
        <>
          <Item
            icon={<TrialDot kind="pass" />}
            text="pass"
            title="A try that passed every check"
          />
          <Item
            icon={<TrialDot kind="fail" />}
            text="fail"
            title="A try that failed a check"
          />
          {carriedFrom ? (
            <Item
              icon={<TrialDot kind="pass" carried />}
              text={`carried from ${carriedFrom}`}
              title={`Dashed ring: the result was carried over from ${carriedFrom}, not rerun`}
            />
          ) : null}
          <Item
            icon={<TrialDot kind="error" />}
            text="error"
            title="The try errored (counted as a fail)"
          />
          <Item
            icon={<TrialDot kind="infra" />}
            text="didn't run"
            title="Setup failed before the try ran (not counted)"
          />
          {voice ? (
            <>
              <Item
                icon={
                  <Typography
                    component="span"
                    typography="s3"
                    color="warning.dark"
                  >
                    ⚡
                  </Typography>
                }
                text="interruption"
                title="Kept talking over the caller"
              />
              <Item
                icon={
                  <Box component="span" sx={{ display: "inline-flex" }}>
                    <Iconify
                      icon="mdi:wrench-outline"
                      width={12}
                      sx={{ color: "error.main" }}
                    />
                  </Box>
                }
                text="tool failure"
                title="A tool call failed in this call"
              />
            </>
          ) : null}
        </>
      ) : null}
    </Stack>
  );
}
MatrixLegend.propTypes = {
  carriedFrom: PropTypes.string,
  voice: PropTypes.bool,
};

/**
 * IT-R5 G6 P3: the legend as a `?` on the Success header (one row less
 * above the grid): hover / focus lists the real cell marks.
 */
export function LegendHelp({ carriedFrom, voice = false }) {
  const items = [
    [<TrialDot key="p" kind="pass" />, "pass", "A try that passed every check"],
    [<TrialDot key="f" kind="fail" />, "fail", "A try that failed a check"],
    carriedFrom
      ? [
          <TrialDot key="c" kind="pass" carried />,
          `carried from ${carriedFrom}`,
          "Dashed ring: the result was carried over, not rerun",
        ]
      : null,
    [
      <TrialDot key="e" kind="error" />,
      "error",
      "The try errored (counted as a fail)",
    ],
    [
      <TrialDot key="i" kind="infra" />,
      "didn't run",
      "Setup failed before the try ran (not counted)",
    ],
    voice ? ["⚡", "interruption", "Kept talking over the caller"] : null,
    voice
      ? [
          <Iconify
            key="w"
            icon="mdi:wrench-outline"
            width={12}
            sx={{ color: "error.main" }}
          />,
          "tool failure",
          "A tool call failed in this call",
        ]
      : null,
  ].filter(Boolean);
  return (
    <CustomTooltip
      show
      size="small"
      placement="bottom-start"
      title={
        <Stack gap={0.5} data-testid="matrix-legend-card">
          <Typography typography="s2" fontWeight="fontWeightSemiBold">
            How to read a cell
          </Typography>
          {items.map(([icon, text, title]) => (
            <Stack key={text} direction="row" alignItems="center" gap={0.75}>
              <Box component="span" sx={{ display: "inline-flex", width: 14 }}>
                {icon}
              </Box>
              <Typography component="span" typography="s2">
                {`${text} · ${title}`}
              </Typography>
            </Stack>
          ))}
        </Stack>
      }
    >
      <IconButton
        size="small"
        aria-label="Legend: how to read a cell"
        data-testid="matrix-legend"
        sx={{
          p: 0.125,
          ml: 0.5,
          color: "text.disabled",
          verticalAlign: "middle",
        }}
      >
        <Iconify icon="mdi:help-circle-outline" width={14} />
      </IconButton>
    </CustomTooltip>
  );
}
LegendHelp.propTypes = {
  carriedFrom: PropTypes.string,
  voice: PropTypes.bool,
};
