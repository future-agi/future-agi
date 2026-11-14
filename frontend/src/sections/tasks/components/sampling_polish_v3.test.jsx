import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, within } from "src/utils/test-utils";
import axios from "src/utils/axios";
import TaskSamplingSection from "./task_sampling_section";
import TaskSchedulingSection from "./TaskSchedulingSection";
import SavedTaskSelection from "./sampling_selection_summary";
import SamplingPickIllustration from "./sampling_pick_illustration";
import { samplingPolicyForApi } from "./samplingPolicyForm";
import {
  blend,
  contrast,
  productionTheme,
  rgb,
} from "./__tests__/samplingContrastTestUtils";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: { project: { samplingPreview: () => "/sample-preview/" } },
}));
vi.mock(
  "src/sections/projects/LLMTracing/useExactTraceAttributeProperties",
  () => ({
    useExactTraceAttributeProperties: () => ({ data: [], isLoading: false }),
  }),
);
vi.mock("src/hooks/useDashboards", () => ({
  useDashboardFilterValues: () => ({ data: [], isFetching: false }),
}));

const groupPolicy = (live = false) => ({
  version: 1,
  sampling_unit: "session",
  missing_id: "exclude",
  mode: live ? "live" : "closed",
  window: {
    start: "2026-10-01T00:00:00.000Z",
    end: live ? null : "2026-10-02T00:00:00.000Z",
  },
  default: live ? { rate: 0 } : { target_n: 0 },
  strata: ["fr", "en"].map((value, i) => ({
    name: `Group ${i + 1}`,
    filters: [{ source: "span", key: "locale", op: "in", values: [value] }],
    ...(live ? { rate: (i + 1) * 10 } : { target_n: 1 }),
  })),
  group_by: [],
});
let form;
function Harness({ policy = null }) {
  form = useForm({
    defaultValues: {
      name: "Sampling polish",
      project: "project",
      rowType: "spans",
      runType: policy?.mode === "live" ? "continuous" : "historical",
      samplingPolicy: policy,
      samplingRate: 10,
      spansLimit: 100000,
      datePreset: "Custom",
      startDate: "2026-10-01T00:00:00.000Z",
      endDate: "2026-10-02T00:00:00.000Z",
      filters: [],
      evalsDetails: [{ id: "eval" }],
    },
  });
  return (
    <>
      <TaskSchedulingSection control={form.control} />
      <TaskSamplingSection control={form.control} getValues={form.getValues} />
    </>
  );
}
Harness.propTypes = { policy: PropTypes.object };
beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue({
    data: {
      result: {
        strata: [{ eligible_units: 2, selected_units: 2, selected_rows: 20 }],
        evaluator_count: 1,
        expected_evaluations: 20,
      },
    },
  });
});

describe.each(["light", "dark"])("sampling v3 polish in %s", (mode) => {
  it.each([1280, 1600])(
    "reserves nonshrinking label space for the custom percentage at the %s host width",
    async (viewport) => {
      // jsdom cannot measure clipping. Assert the layout contract that prevents
      // flex shrink and reserves room for the full label in either host panel.
      render(
        <div style={{ width: viewport === 1280 ? 553 : 728 }}>
          <Harness />
        </div>,
        { theme: productionTheme(mode) },
      );
      const input = screen.getByRole("spinbutton", {
        name: "Custom percentage (%)",
      });
      expect(input.closest(".MuiFormControl-root")).toHaveStyle({
        width: "190px",
        flexShrink: "0",
        maxWidth: "100%",
      });
      await userEvent.clear(input);
      await userEvent.type(input, "37");
      expect(form.getValues("samplingRate")).toBe(37);
      expect(form.getValues("samplingPolicy")).toBeNull();
      expect(form.getValues("spansLimit")).toBe(100000);
    },
  );

  it("makes the requested total and remainder explicit while keeping real preview and payload distinct", async () => {
    const policy = groupPolicy();
    const before = structuredClone(policy);
    render(<Harness policy={policy} />, { theme: productionTheme(mode) });
    const summary = screen.getByLabelText("Requested group selection");
    expect(summary).toHaveTextContent(
      "Up to 2 conversations in total: fr 1 · en 1 · all other 0",
    );
    const remainder = screen.getByRole("spinbutton", {
      name: "All other conversations (not fr or en)",
    });
    expect(remainder).toHaveValue(0);
    expect(form.getValues("samplingPolicy")).toEqual(before);
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    expect(axios.post.mock.calls[0][1].sampling_policy).toEqual(before);
    expect(axios.post.mock.calls[0][1]).not.toHaveProperty("sampling_rate");
    expect(
      await screen.findByLabelText("Sampling preview summary"),
    ).toHaveTextContent("2 conversations · 20 spans evaluated");
    await userEvent.clear(remainder);
    expect(
      screen.queryByLabelText("Requested group selection"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Sampling preview summary"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Preview sample" }),
    ).toBeDisabled();
    await userEvent.type(remainder, "3");
    expect(
      screen.getByLabelText("Requested group selection"),
    ).toHaveTextContent(
      "Up to 5 conversations in total: fr 1 · en 1 · all other 3",
    );
    expect(samplingPolicyForApi(form.getValues("samplingPolicy"))).toEqual({
      ...before,
      default: { target_n: 3 },
    });
    await act(async () =>
      form.setValue("samplingPolicy", {
        ...before,
        strata: before.strata.map((rule) => ({ ...rule, target_n: 0 })),
      }),
    );
    expect(
      screen.getByLabelText("Requested group selection"),
    ).toHaveTextContent(
      "Up to 0 conversations in total: fr 0 · en 0 · all other 0",
    );
    expect(policy).toEqual(before);
  });

  it("lists persisted per-group counts in the saved header, not just the fallback or a rule count", () => {
    const task = {
      sampling_policy: groupPolicy(),
      row_type: "spans",
      run_type: "historical",
    };
    const before = structuredClone(task);
    render(<SavedTaskSelection task={task} />, {
      theme: productionTheme(mode),
    });
    const summary = screen.getByLabelText("Saved data selection");
    expect(summary).toHaveTextContent(
      "Up to 2 conversations in total: fr 1 · en 1 · all other 0",
    );
    expect(summary).toHaveTextContent(
      "evaluate every matching span in each picked group",
    );
    expect(summary).not.toHaveTextContent("2 per-group counts");
    expect(summary).not.toHaveTextContent("All other conversations: up to 0");
    expect(summary).not.toHaveTextContent("2 evaluations");
    expect(task).toEqual(before);
  });

  it("shows the incoming explanation once and separate per-group shares, never an additive population total", () => {
    const policy = groupPolicy(true);
    const before = structuredClone(policy);
    render(<Harness policy={policy} />, { theme: productionTheme(mode) });
    expect(
      screen.getAllByText(
        "New incoming data uses percentages, not fixed counts.",
      ),
    ).toHaveLength(1);
    const summary = screen.getByLabelText("Requested group selection");
    expect(summary).toHaveTextContent(
      "Shares of new conversations: fr 10% · en 20% · all other 0%",
    );
    expect(summary).not.toHaveTextContent(/in total|30%/);
    expect(
      screen.getByRole("spinbutton", {
        name: "All other conversations (not fr or en) (%)",
      }),
    ).toHaveValue(0);
    expect(form.getValues("samplingPolicy")).toEqual(before);
  });

  it("uses small inline dropdowns for the field and value pickers without changing their disabled guards", async () => {
    render(<Harness policy={groupPolicy()} />, {
      theme: productionTheme(mode),
    });
    for (const name of ["Group field", "Group values"]) {
      expect(screen.getByRole("combobox", { name })).toHaveClass(
        "MuiInputBase-inputSizeSmall",
      );
      expect(screen.getByRole("combobox", { name })).toBeEnabled();
    }
    expect(screen.getByRole("combobox", { name: "Group field" })).toHaveValue(
      "locale",
    );
    expect(screen.getByRole("button", { name: "fr" })).toBeVisible();
    expect(
      screen.queryByRole("dialog", { name: /Choose/ }),
    ).not.toBeInTheDocument();
    await act(async () => form.setValue("project", ""));
    expect(
      screen.getByRole("combobox", { name: "Group field" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("combobox", { name: "Group field location" }),
    ).toHaveAttribute("aria-disabled", "true");
    expect(
      screen.getByRole("combobox", { name: "Group values" }),
    ).toBeDisabled();
  });

  it.each(["span", "trace", "session", "user", "attribute"])(
    "uses one-pixel neutral illustration borders and readable picked text for %s",
    (unit) => {
      const theme = productionTheme(mode);
      render(<SamplingPickIllustration unit={unit} rowType="spans" />, {
        theme,
      });
      const figure = screen.getByRole("figure");
      const picked = within(figure).getByText(/· picked/);
      const box = picked.parentElement;
      expect(box).toHaveStyle({
        borderWidth: "1px",
        borderColor: theme.palette.divider,
      });
      expect(picked).toHaveStyle({ fontWeight: "600" });
      for (const label of within(figure).getAllByText(
        /^(Conversation|Traces|Spans|End user|Custom ID)/,
      )) {
        expect(label.parentElement).toHaveStyle({
          borderWidth: "1px",
          borderColor: theme.palette.divider,
        });
      }
      const surface = blend(
        rgb(theme.palette.action.selected),
        rgb(theme.palette.background.paper),
      );
      expect(
        contrast(rgb(theme.palette.text.primary), surface),
      ).toBeGreaterThanOrEqual(4.5);
      expect(figure).toHaveAccessibleName(/Pick /);
      expect(figure).toHaveTextContent(/Spans · (picked · )?evaluated/);
    },
  );
});
