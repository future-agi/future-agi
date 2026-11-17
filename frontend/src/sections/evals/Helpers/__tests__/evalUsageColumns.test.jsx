import React from "react";
import {
  bpText,
  bpRendered,
} from "../../components/__tests__/pinCopyBp.fixture";
import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import { createTheme } from "@mui/material/styles";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import DataTable from "src/components/data-table/DataTable";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import {
  DEFAULT_COLUMN_CONFIG,
  DATE_OPTION_TO_PERIOD,
  decodeColumnConfig,
  encodeColumnConfig,
  normalizeRow,
  periodForRange,
  useColumns,
} from "../evalUsageColumns";

const RECORDED_SETTINGS = bpText["A2-01"];
const theme = createTheme({
  palette: palette("light"),
  typography,
  spacing: (factor) => `${0.25 * factor}rem`,
});

describe("recorded usage version copy", () => {
  it.each(["Recorded version", "Version"])(
    "renders an accessible DataTable header and tooltip for column label %s",
    async (label) => {
      expect(
        DEFAULT_COLUMN_CONFIG.find((c) => c.value === "version").label,
      ).toBe(bpText["A1-01"]);
      const config = DEFAULT_COLUMN_CONFIG.map((c) =>
        c.value === "version" ? { ...c, label } : c,
      );
      const { result } = renderHook(() => useColumns(config));
      render(
        <div style={{ height: 400, width: 800 }}>
          <DataTable
            columns={result.current.filter((c) => c.id === "version")}
            data={[{ id: "usage-1", version: 3 }]}
            rowCount={1}
          />
        </div>,
        { theme },
      );
      const header = screen.getByRole("columnheader", {
        name: "Recorded version",
      });
      expect(header).toHaveAttribute("aria-label", "Recorded version");
      expect(screen.queryByText("Version", { exact: true })).toBeNull();
      fireEvent.mouseOver(within(header).getByText("Recorded version"));
      expect(await screen.findByRole("tooltip")).toHaveTextContent(
        RECORDED_SETTINGS,
      );
    },
  );
  it.each([3, "3", "v3"])(
    "marks cell %s as recorded rather than executed",
    (value) => {
      const { result } = renderHook(() => useColumns(DEFAULT_COLUMN_CONFIG));
      const Cell = result.current.find((c) => c.id === "version").cell;
      render(<Cell getValue={() => value} row={{ original: {} }} />, { theme });
      expect(
        screen.getByText(bpRendered.usage(value)).closest("[title]"),
      ).toHaveAttribute("title", bpText["A2-02"]);
      expect(screen.queryByText("v3", { exact: true })).toBeNull();
    },
  );
  it.each([null, ""])("keeps an absent version %s blank", (value) => {
    const { result } = renderHook(() => useColumns(DEFAULT_COLUMN_CONFIG));
    const Cell = result.current.find((c) => c.id === "version").cell;
    render(<Cell getValue={() => value} row={{ original: {} }} />, { theme });
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.queryByText(/Recorded/)).toBeNull();
  });
});

describe("DATE_OPTION_TO_PERIOD", () => {
  it("maps the optional one-hour picker label to the bounded one-hour API period", () => {
    expect(DATE_OPTION_TO_PERIOD["1 hr"]).toBe("1h");
  });
});

describe("periodForRange", () => {
  // The backend derives the chart bucket size from `period`, not from the
  // start/end delta, so a Custom range must map its span to a sensible period.
  it.each([
    ["2026-01-01T00:00:00Z", "2026-01-01T00:30:00Z", "30m"], // 30 min
    ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", "30m"], // exactly 1h
    ["2026-01-01T00:00:00Z", "2026-01-01T05:00:00Z", "6h"], // few hours
    ["2026-01-01T00:00:00Z", "2026-01-01T20:00:00Z", "1d"], // < 1 day
    ["2026-01-01T00:00:00Z", "2026-01-04T00:00:00Z", "7d"], // few days
    ["2026-01-01T00:00:00Z", "2026-01-20T00:00:00Z", "30d"], // ~3 weeks
    ["2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z", "90d"], // ~2 months
    ["2026-01-01T00:00:00Z", "2026-05-01T00:00:00Z", "180d"], // ~4 months
    ["2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "365d"], // multi-year
  ])("maps span %s → %s to period %s", (start, end, expected) => {
    expect(periodForRange(start, end)).toBe(expected);
  });

  it("falls back to 30d for a zero-length or inverted range", () => {
    expect(periodForRange("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")).toBe(
      "30d",
    );
    expect(periodForRange("2026-02-01T00:00:00Z", "2026-01-01T00:00:00Z")).toBe(
      "30d",
    );
  });
});

describe("normalizeRow", () => {
  it("keys the row on row_id (the only id the usage serializer emits)", () => {
    expect(normalizeRow({ row_id: "r1", score: 0.9 })).toEqual({
      id: "r1",
      score: 0.9,
    });
  });

  it("does not fall back to raw.id when row_id is absent", () => {
    // Contract: the serializer never emits `id`, only `row_id`.
    expect(normalizeRow({ score: 0.5 }).id).toBeUndefined();
  });

  it("unwraps { cell_value } wrapper objects", () => {
    expect(
      normalizeRow({
        row_id: "r2",
        result: { cell_value: "Pass" },
        input_var_topic: { cell_value: "billing" },
      }),
    ).toEqual({ id: "r2", result: "Pass", input_var_topic: "billing" });
  });

  it("leaves arrays and plain scalars untouched", () => {
    expect(
      normalizeRow({ row_id: "r3", tags: ["a", "b"], reason: "because" }),
    ).toEqual({ id: "r3", tags: ["a", "b"], reason: "because" });
  });
});

describe("encodeColumnConfig / decodeColumnConfig", () => {
  it("encodes visible columns bare and hidden columns with a ~ prefix, in order", () => {
    const cols = [
      { value: "score", enabled: true, is_visible: true, order_index: 0 },
      { value: "result", enabled: false, is_visible: false, order_index: 1 },
      { value: "input", enabled: true, is_visible: true, order_index: 2 },
    ];
    expect(encodeColumnConfig(cols)).toBe("score,~result,input");
  });

  it("round-trips the default config", () => {
    const encoded = encodeColumnConfig(DEFAULT_COLUMN_CONFIG);
    const decoded = decodeColumnConfig(encoded, DEFAULT_COLUMN_CONFIG);
    expect(decoded.map((c) => c.value)).toEqual(
      DEFAULT_COLUMN_CONFIG.map((c) => c.value),
    );
    expect(decoded.every((c) => c.enabled && c.is_visible)).toBe(true);
  });

  it("preserves an unknown input_var_* token discovered on another page", () => {
    const decoded = decodeColumnConfig(
      "score,input_var_topic",
      DEFAULT_COLUMN_CONFIG,
    );
    const topic = decoded.find((c) => c.value === "input_var_topic");
    expect(topic).toMatchObject({
      value: "input_var_topic",
      label: "topic",
      enabled: true,
      is_visible: true,
    });
  });

  it("keeps a hidden unknown input_var_* token hidden", () => {
    const decoded = decodeColumnConfig(
      "~input_var_topic",
      DEFAULT_COLUMN_CONFIG,
    );
    const topic = decoded.find((c) => c.value === "input_var_topic");
    expect(topic).toMatchObject({ enabled: false, is_visible: false });
  });

  it("returns null for empty input", () => {
    expect(decodeColumnConfig("", DEFAULT_COLUMN_CONFIG)).toBeNull();
    expect(decodeColumnConfig(null, DEFAULT_COLUMN_CONFIG)).toBeNull();
  });
});
