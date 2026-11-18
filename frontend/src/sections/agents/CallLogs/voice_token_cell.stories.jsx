import React from "react";
import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule } from "ag-grid-community";
import { expect, within, waitFor } from "storybook/test";
import { addCollection } from "@iconify/react";
import PropTypes from "prop-types";
import "@fontsource/inter/400.css";
import "src/styles/clean-data-table.css";
import { useAgTheme } from "src/hooks/use-ag-theme";
import { getCallLogsColumnDefs } from "../helper";
import VoiceTokenCell from "./VoiceTokenCell";

// Pin the real MDI glyphs so offline previews include their layout width.
// Source: https://api.iconify.design/mdi.json?icons=arrow-down,arrow-up,information-outline
addCollection({
  prefix: "mdi",
  width: 24,
  height: 24,
  icons: {
    "arrow-down": {
      body: '<path fill="currentColor" d="M11 4h2v12l5.5-5.5l1.42 1.42L12 19.84l-7.92-7.92L5.5 10.5L11 16z"/>',
    },
    "arrow-up": {
      body: '<path fill="currentColor" d="M13 20h-2V8l-5.5 5.5l-1.42-1.42L12 4.16l7.92 7.92l-1.42 1.42L13 8z"/>',
    },
    "information-outline": {
      body: '<path fill="currentColor" d="M11 9h2V7h-2m1 13c-4.41 0-8-3.59-8-8s3.59-8 8-8s8 3.59 8 8s-3.59 8-8 8m0-18A10 10 0 0 0 2 12a10 10 0 0 0 10 10a10 10 0 0 0 10-10A10 10 0 0 0 12 2m-1 15h2v-6h-2z"/>',
    },
  },
});

const reportedUsage = {
  "gen_ai.usage.input_tokens": 119318,
  "gen_ai.usage.output_tokens": 846,
  "gen_ai.usage.total_tokens": 120164,
};

// Use the real column definition: a wider fixture would hide this regression.
const TokenGrid = ({ data }) => {
  const theme = useAgTheme();
  const tokenColumn = getCallLogsColumnDefs().find(
    ({ field }) => field === "gen_ai.usage.total_tokens",
  );

  return (
    <Box
      sx={{
        width: 500,
        height: 150,
        "& .ag-cell-wrapper": { flex: "1 !important", height: "100%" },
        "& .ag-cell-wrapper > span": { height: "100%" },
      }}
    >
      <AgGridReact
        className="clean-data-table"
        modules={[AllCommunityModule]}
        theme={theme}
        rowHeight={40}
        rowSelection={{ mode: "multiRow" }}
        selectionColumnDef={{ pinned: true, lockPinned: true }}
        rowData={[data]}
        columnDefs={[{ ...tokenColumn, width: tokenColumn.minWidth }]}
        defaultColDef={{
          resizable: true,
          cellStyle: { padding: "0px", display: "flex", alignItems: "center" },
        }}
      />
    </Box>
  );
};

TokenGrid.propTypes = { data: PropTypes.object };

export default {
  title: "Sections/Agents/CallLogs/VoiceTokenCell",
  component: VoiceTokenCell,
  render: (args) => <TokenGrid {...args} />,
};

const assertUsageFits = async ({ canvasElement }) => {
  const canvas = within(canvasElement);
  await canvas.findAllByText(/119\.32/);
  const cell = canvasElement.querySelector(
    '[col-id="gen_ai.usage.total_tokens"][role="gridcell"]',
  );
  await waitFor(() => expect(cell.querySelectorAll("svg")).toHaveLength(3));
  await document.fonts.load("13px Inter");
  await document.fonts.ready;
  await expect(document.fonts.check("13px Inter")).toBe(true);
  const bounds = cell.getBoundingClientRect();
  const values = cell.querySelectorAll(".MuiTypography-root");
  await expect(values).toHaveLength(3);
  // DOM text alone passes when the leading digit is visually clipped.
  for (const value of values) {
    const range = document.createRange();
    range.selectNodeContents(value);
    const text = range.getBoundingClientRect();
    await expect(text.left).toBeGreaterThanOrEqual(bounds.left);
    await expect(text.right).toBeLessThanOrEqual(bounds.right);
  }
  for (const icon of cell.querySelectorAll("svg")) {
    const rect = icon.getBoundingClientRect();
    await expect(rect.width).toBeGreaterThan(0);
    await expect(rect.left).toBeGreaterThanOrEqual(bounds.left);
    await expect(rect.right).toBeLessThanOrEqual(bounds.right);
  }
};

export const ReportedUsageAtMinimumWidth = {
  args: { data: reportedUsage },
  play: assertUsageFits,
};

export const LargeInputAndOutput = {
  args: {
    data: {
      "gen_ai.usage.input_tokens": 119318000,
      "gen_ai.usage.output_tokens": 119318000,
      "gen_ai.usage.total_tokens": 238636000,
    },
  },
  play: assertUsageFits,
};

export const MissingUsage = { args: { data: {} } };
export const TotalOnly = { args: { data: { total_tokens: 120164 } } };
