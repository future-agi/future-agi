import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "src/utils/test-utils";
import ObserveToolbar from "../ObserveToolbar";

// On the Traces list and the Spans view the toolbar
// hands its filter panel a Timestamp field, reopens a saved Timestamp filter
// as the local date and time it was set to, and applies an edited one as that
// local instant with the viewer's UTC offset (the list API reads a bare
// date-time as UTC). The panel is a double; the toolbar's own field list,
// hydration and apply path run for real. No fixture here carries an icon of
// its own.

const traceFilterPanelPropsMock = vi.hoisted(() => vi.fn());

vi.mock("../TraceFilterPanel", async (importOriginal) => ({
  ...(await importOriginal()),
  default: (props) => {
    traceFilterPanelPropsMock(props);
    return null;
  },
}));
vi.mock("../DisplayPanel", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../BulkActionsBar", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../tabStore", async (importOriginal) => ({
  ...(await importOriginal()),
  useTabStoreShallow: (selector) => selector({ openCreateModal: vi.fn() }),
}));
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock(
  "src/components/custom-datepicker/DatePicker",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);

const savedTimestamp = (filter_op, filter_value) => ({
  column_id: "start_time",
  property_id: "system_attribute:traces:start_time",
  display_name: "Timestamp",
  filter_config: {
    col_type: "SYSTEM_METRIC",
    filter_type: "datetime",
    filter_op,
    filter_value,
  },
});

const ZONED = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/;

const PROJECT_FIELD = { id: "project_id", name: "Project", type: "string" };

const renderToolbar = (props = {}) =>
  render(
    <ObserveToolbar
      inline
      isFilterOpen={false}
      onFilterToggle={vi.fn()}
      onApplyExtraFilters={vi.fn()}
      {...props}
    />,
  );

const panelFieldIds = () =>
  (traceFilterPanelPropsMock.mock.calls.at(-1)[0].filterFields || []).map(
    (field) => field.id,
  );

beforeEach(() => {
  traceFilterPanelPropsMock.mockClear();
});

describe("Timestamp filters through the Observe toolbar", () => {
  it.each(["trace", "spans"])(
    "%s: hands the filter panel a Timestamp field beside the view's own fields",
    (tab) => {
      renderToolbar({ mode: "traces", tab, filterFields: [PROJECT_FIELD] });

      const { filterFields } = traceFilterPanelPropsMock.mock.calls.at(-1)[0];
      expect(filterFields).toEqual([
        PROJECT_FIELD,
        expect.objectContaining({
          id: "start_time",
          name: "Timestamp",
          category: "system",
          type: "datetime",
        }),
      ]);
    },
  );

  it.each([
    ["Sessions", { mode: "sessions", filterFields: [PROJECT_FIELD] }],
    ["Users", { mode: "users" }],
    [
      "a simulator's call log",
      { mode: "traces", tab: "trace", isSimulator: true },
    ],
    ["the user-detail trace list", { mode: "traces" }],
  ])("keeps %s without the Timestamp field", (_view, props) => {
    renderToolbar(props);

    expect(panelFieldIds()).not.toContain("start_time");
  });

  it.each(["trace", "spans"])(
    "%s: reopens a saved Timestamp as local time and applies it with the viewer's offset",
    (tab) => {
      const onApplyExtraFilters = vi.fn();
      render(
        <ObserveToolbar
          inline
          mode="traces"
          tab={tab}
          isSpansView={tab === "spans"}
          isFilterOpen={false}
          onFilterToggle={vi.fn()}
          onApplyExtraFilters={onApplyExtraFilters}
          graphFilters={[
            savedTimestamp(
              "greater_than",
              new Date(2026, 10, 10, 10, 30).toISOString(),
            ),
          ]}
        />,
      );

      const panel = traceFilterPanelPropsMock.mock.calls.at(-1)[0];
      expect(panel.currentFilters).toHaveLength(1);
      expect(panel.currentFilters[0]).toMatchObject({
        field: "start_time",
        registryId: "system_attribute:traces:start_time",
        fieldName: "Timestamp",
        fieldType: "datetime",
        apiColType: "SYSTEM_METRIC",
        operator: "greater_than",
        value: "2026-11-10T10:30",
      });

      act(() =>
        panel.onApply([
          {
            ...panel.currentFilters[0],
            operator: "between",
            value: ["2026-11-09T08:00", "2026-11-10T18:45"],
          },
        ]),
      );
      const [applied] = onApplyExtraFilters.mock.calls.at(-1)[0];
      expect(applied).toMatchObject({
        column_id: "start_time",
        property_id: "system_attribute:traces:start_time",
        filter_config: {
          col_type: "SYSTEM_METRIC",
          filter_type: "datetime",
          filter_op: "between",
        },
      });
      const bounds = applied.filter_config.filter_value;
      expect(bounds).toHaveLength(2);
      bounds.forEach((bound) => expect(bound).toMatch(ZONED));
      expect(bounds.map(Date.parse)).toEqual([
        new Date(2026, 10, 9, 8, 0).getTime(),
        new Date(2026, 10, 10, 18, 45).getTime(),
      ]);
    },
  );
});
