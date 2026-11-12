import { describe, expect, it } from "vitest";
import {
  apiFilterToPanelRow,
  apiFiltersToPanelRows,
  panelRowsToApiFilters,
} from "../observeFilterConfig";

// A Timestamp (start_time) row holds the viewer's
// local wall time, the list API reads a bare date-time as UTC, so the filter
// carries the local instant with the viewer's offset and reopens as local
// wall time. Every expectation is derived from local Date fields, so the file
// passes in any time zone.

const ZONED = (localDateTime) =>
  new RegExp(`^${localDateTime}[+-]\\d{2}:\\d{2}$`);
const local = (...parts) => new Date(...parts).getTime();

const timestampRow = (operator, value, extra = {}) => ({
  field: "start_time",
  registryId: "system_attribute:traces:start_time",
  fieldName: "Timestamp",
  fieldCategory: "system",
  fieldType: "date",
  apiColType: "SYSTEM_METRIC",
  operator,
  value,
  ...extra,
});

const savedTimestamp = (
  filter_op,
  filter_value,
  col_type = "SYSTEM_METRIC",
) => ({
  column_id: "start_time",
  property_id: "system_attribute:traces:start_time",
  display_name: "Timestamp",
  filter_config: {
    filter_type: "datetime",
    filter_op,
    filter_value,
    col_type,
  },
});

describe("Timestamp filters keep local time", () => {
  it.each([
    "greater_than",
    "less_than",
    "greater_than_or_equal",
    "less_than_or_equal",
  ])("sends a %s bound as the local instant with its offset", (operator) => {
    const [filter] = panelRowsToApiFilters([
      timestampRow(operator, "2026-11-10T10:30"),
    ]);

    expect(filter).toMatchObject({
      column_id: "start_time",
      property_id: "system_attribute:traces:start_time",
      display_name: "Timestamp",
      filter_config: {
        filter_type: "datetime",
        filter_op: operator,
        col_type: "SYSTEM_METRIC",
      },
    });
    expect(filter.filter_config.filter_value).toMatch(
      ZONED("2026-11-10T10:30:00"),
    );
    expect(Date.parse(filter.filter_config.filter_value)).toBe(
      local(2026, 10, 10, 10, 30),
    );
  });

  it.each(["between", "not_between"])(
    "sends both %s bounds as local instants, in order",
    (operator) => {
      const [filter] = panelRowsToApiFilters([
        timestampRow(operator, ["2026-11-09T08:00", "2026-11-10T18:45:15"]),
      ]);
      const [from, to] = filter.filter_config.filter_value;

      expect(filter.filter_config.filter_op).toBe(operator);
      expect(from).toMatch(ZONED("2026-11-09T08:00:00"));
      expect(to).toMatch(ZONED("2026-11-10T18:45:15"));
      expect([Date.parse(from), Date.parse(to)]).toEqual([
        local(2026, 10, 9, 8, 0),
        local(2026, 10, 10, 18, 45, 15),
      ]);
    },
  );

  it("reads a bare day range as whole local days", () => {
    const sent = panelRowsToApiFilters([
      timestampRow("between", ["2026-11-09", "2026-11-10"]),
    ]);
    const [from, to] = sent[0].filter_config.filter_value;

    // The API reads a range as [start, end), so the upper day runs through
    // its last millisecond, not 23:59:00.
    expect(from).toMatch(ZONED("2026-11-09T00:00:00"));
    expect(to).toMatch(ZONED("2026-11-10T23:59:59\\.999"));
    expect([Date.parse(from), Date.parse(to)]).toEqual([
      local(2026, 10, 9, 0, 0),
      local(2026, 10, 10, 23, 59, 59, 999),
    ]);

    // Reopened, it shows that last moment and applies unchanged.
    const [row] = apiFiltersToPanelRows(sent);
    expect(row.value).toEqual(["2026-11-09T00:00", "2026-11-10T23:59:59.999"]);
    expect(panelRowsToApiFilters([row])).toEqual(sent);
  });

  it("reopens a UTC instant as the local date and time it names", () => {
    const row = apiFilterToPanelRow(
      savedTimestamp(
        "greater_than",
        new Date(2026, 10, 10, 10, 30).toISOString(),
      ),
    );

    expect(row).toMatchObject({
      field: "start_time",
      registryId: "system_attribute:traces:start_time",
      fieldName: "Timestamp",
      fieldCategory: "system",
      fieldType: "datetime",
      apiColType: "SYSTEM_METRIC",
      operator: "greater_than",
      value: "2026-11-10T10:30",
    });
  });

  it("round-trips a range through reopen and apply without moving it", () => {
    const sent = panelRowsToApiFilters([
      timestampRow("between", ["2026-11-09T08:00", "2026-11-10T18:45:15"]),
    ]);
    const [row] = apiFiltersToPanelRows(sent);

    expect(row).toMatchObject({
      fieldType: "datetime",
      operator: "between",
      value: ["2026-11-09T08:00", "2026-11-10T18:45:15"],
    });
    expect(panelRowsToApiFilters([row])).toEqual(sent);
  });

  it("leaves other dates, raw attributes and finer-than-millisecond instants alone", () => {
    const lastActive = {
      column_id: "last_active",
      property_id: "users:last_active",
      display_name: "Last Active",
      filter_config: {
        filter_type: "datetime",
        filter_op: "greater_than",
        filter_value: "2026-11-10T10:30",
        col_type: "SYSTEM_METRIC",
      },
    };
    expect(panelRowsToApiFilters(apiFiltersToPanelRows([lastActive]))).toEqual([
      lastActive,
    ]);

    const [attribute] = panelRowsToApiFilters([
      timestampRow("greater_than", "2026-11-10T10:30", {
        registryId: "custom_attribute:start_time",
        fieldCategory: "attribute",
        apiColType: "SPAN_ATTRIBUTE",
      }),
    ]);
    expect(attribute.filter_config.filter_value).toBe("2026-11-10T10:30");

    const micro = "2026-11-10T05:00:00.123456Z";
    expect(apiFilterToPanelRow(savedTimestamp("equals", micro)).value).toBe(
      micro,
    );
  });
});
