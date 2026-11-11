import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import axios from "src/utils/axios";
import TaskSamplingSection from "./task_sampling_section";
import TaskSchedulingSection from "./TaskSchedulingSection";
import { NewTaskValidationSchema } from "../schema";
import { samplingPolicyForApi } from "./samplingPolicyForm";
import {
  advancedSamplingSummary,
  isSimpleSamplingPolicy,
  pickLabel,
  pickLevels,
  pickNames,
  rowNames,
  samplingContextError,
  simpleSamplingPolicy,
} from "./simpleSamplingPolicy";
import { productionTheme } from "./__tests__/samplingContrastTestUtils";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: { project: { samplingPreview: () => "/sample-preview/" } },
}));
// Discovery adapters have their own production-picker interaction suite. This
// harness makes selected typed values explicit without a catalog/network.
vi.mock("./sampling_field_picker", () => {
  const SamplingFieldPicker = ({
    onChange,
    label = "Group field",
    helperText,
  }) => (
    <>
      <button onClick={() => onChange({ source: "metadata", key: "language" })}>
        {`Choose ${label.toLowerCase()}`}
      </button>
      {helperText && <p>{helperText}</p>}
    </>
  );
  SamplingFieldPicker.propTypes = {
    onChange: PropTypes.func,
    label: PropTypes.string,
    helperText: PropTypes.string,
  };
  const SamplingValuePicker = ({ onChange }) => (
    <button onClick={() => onChange(["French", "English"])}>
      Choose values
    </button>
  );
  SamplingValuePicker.propTypes = { onChange: PropTypes.func };
  return { SamplingFieldPicker, SamplingValuePicker };
});
let form;
const defaults = (rowType = "spans", runType = "historical") => ({
  name: "Quality check",
  project: "project",
  rowType,
  filters: [],
  evalsDetails: [{ id: "eval" }],
  samplingPolicy: null,
  samplingRate: 50,
  spansLimit: 100000,
  runType,
  datePreset: "Custom",
  startDate: "2026-10-01T00:00:00.000Z",
  endDate: "2026-10-02T00:00:00.000Z",
});
function Harness({ values = {} }) {
  form = useForm({ defaultValues: { ...defaults(), ...values } });
  return (
    <>
      <TaskSchedulingSection control={form.control} />
      <TaskSamplingSection control={form.control} getValues={form.getValues} />
    </>
  );
}
Harness.propTypes = { values: PropTypes.object };
const show = (values, mode = "light") =>
  render(<Harness values={values} />, { theme: productionTheme(mode) });
const closeDialog = () =>
  waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
const choose = async (unit, rowType) => {
  await userEvent.click(screen.getByRole("combobox", { name: "Pick:" }));
  await userEvent.click(
    screen.getByRole("option", { name: pickLabel(unit, rowType) }),
  );
};
const enable = async (rowType = "spans") =>
  userEvent.click(
    screen.getByRole("radio", {
      name: `Pick whole groups of ${rowNames(rowType)}`,
    }),
  );
const previewResponse = {
  data: {
    result: {
      strata: [
        {
          stratum: '["default",[]]',
          eligible_units: 20,
          selected_units: 10,
          selected_rows: 1000,
          expected_units: 10,
        },
      ],
      evaluator_count: 2,
      expected_evaluations: 2000,
    },
  },
};
beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue(previewResponse);
});

describe("two-choice task data selection", () => {
  it.each(["spans", "traces", "sessions"])(
    "keeps percentage payload and processing limit unchanged for %s",
    async (rowType) => {
      show(defaults(rowType));
      expect(
        screen.getAllByRole("radio", {
          name: new RegExp(
            `^(A percentage of matching|Pick whole groups of) ${rowNames(rowType)}$`,
          ),
        }),
      ).toHaveLength(2);
      for (const rate of [10, 25, 50, 100]) {
        await userEvent.click(
          screen.getByRole("button", {
            name: rate === 100 ? "All" : `${rate}%`,
            exact: true,
          }),
        );
        const parsed = NewTaskValidationSchema().parse(form.getValues());
        expect(parsed.samplingRate).toBe(rate);
        expect(parsed.samplingPolicy).toBeNull();
        expect(parsed.spansLimit).toBe(100000);
        expect(parsed.rowType).toBe(rowType);
      }
      const input = screen.getByRole("spinbutton", {
        name: "Custom percentage (%)",
      });
      await userEvent.clear(input);
      await userEvent.type(input, "37");
      expect(form.getValues("samplingRate")).toBe(37);
      expect(screen.getByRole("slider", { name: "Sampling rate" })).toHaveValue(
        "37",
      );
      expect(screen.getByLabelText("Data selection summary")).toHaveTextContent(
        "About 37%",
      );
    },
  );

  it("keeps a cleared custom percentage explicit and prevents hiding its required error behind group picking", async () => {
    show();
    const input = screen.getByRole("spinbutton", {
      name: "Custom percentage (%)",
    });
    await userEvent.clear(input);
    expect(form.getValues("samplingRate")).toBe("");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(
      screen.getByRole("radio", { name: "Pick whole groups of spans" }),
    ).toBeDisabled();
    expect(NewTaskValidationSchema().safeParse(form.getValues()).success).toBe(
      false,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "25%", exact: true }),
    );
    expect(
      screen.getByRole("radio", { name: "Pick whole groups of spans" }),
    ).toBeEnabled();
    await enable();
    expect(
      NewTaskValidationSchema().parse(form.getValues()).samplingPolicy.default,
    ).toEqual({ target_n: 100 });
  });

  it("drops the selection summary while the custom percentage is invalid and never shows a cleared limit as 0", async () => {
    show();
    const input = screen.getByRole("spinbutton", {
      name: "Custom percentage (%)",
    });
    for (const invalid of [0, 101]) {
      await userEvent.clear(input);
      await userEvent.type(input, String(invalid));
      // The typed value is kept as entered; only the summary echo goes.
      expect(form.getValues("samplingRate")).toBe(invalid);
      expect(input).toHaveAttribute("aria-invalid", "true");
      expect(
        screen.getByText("Enter a percentage from 1 to 100."),
      ).toBeVisible();
      // One error, under the field. The disabled method names its reason.
      const groups = screen.getByRole("radio", {
        name: "Pick whole groups of spans",
      });
      expect(groups).toBeDisabled();
      expect(groups).toHaveAccessibleDescription(
        "Enter a valid percentage to switch to group picking.",
      );
      expect(
        screen.queryByText(/Choose a valid percentage/),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByLabelText("Data selection summary"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText(new RegExp(`About ${invalid}%`)),
      ).not.toBeInTheDocument();
    }
    await userEvent.clear(input);
    await userEvent.type(input, "25");
    expect(screen.getByLabelText("Data selection summary")).toHaveTextContent(
      "About 25% of filter-matching spans. Stop after processing 100,000 matching rows, not after selecting that many evaluations.",
    );
    const groupPicking = screen.getByRole("radio", {
      name: "Pick whole groups of spans",
    });
    expect(groupPicking).toBeEnabled();
    expect(groupPicking).not.toHaveAttribute("aria-describedby");
    expect(
      screen.queryByText(
        "Enter a valid percentage to switch to group picking.",
      ),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Custom limit" }));
    const limit = screen.getByRole("spinbutton", {
      name: "Matching rows to process",
    });
    await userEvent.clear(limit);
    expect(form.getValues("spansLimit")).toBe("");
    expect(screen.getByLabelText("Data selection summary")).toHaveTextContent(
      "About 25% of filter-matching spans. The processing limit is not set yet.",
    );
    expect(
      screen.getByLabelText("Data selection summary"),
    ).not.toHaveTextContent(/\b0 matching rows/);
    for (const invalid of ["0", "-5", "1000001", "2.5"]) {
      await userEvent.clear(limit);
      await userEvent.type(limit, invalid);
      const summary = screen.getByLabelText("Data selection summary");
      expect(summary).toHaveTextContent(
        "About 25% of filter-matching spans. The processing limit must be a whole number from 1 to 1,000,000.",
      );
      expect(summary).not.toHaveTextContent(/Stop after processing/);
    }
    await userEvent.clear(limit);
    await userEvent.type(limit, "2500");
    expect(screen.getByLabelText("Data selection summary")).toHaveTextContent(
      "Stop after processing 2,500 matching rows",
    );
  });

  const combinations = ["spans", "traces", "sessions"].flatMap((rowType) =>
    pickLevels(rowType).flatMap((unit) =>
      ["historical", "continuous"].map((runType) => [rowType, unit, runType]),
    ),
  );
  it.each(combinations)(
    "previews/saves %s picked by %s under %s with no second row sample",
    async (rowType, unit, runType) => {
      show(defaults(rowType, runType));
      await enable(rowType);
      expect(form.getValues("samplingPolicy.sampling_unit")).toBe("session");
      await choose(unit, rowType);
      const policy = structuredClone(form.getValues("samplingPolicy"));
      expect(policy.missing_id).toBe("exclude");
      expect(policy.default).toEqual(
        runType === "continuous" ? { rate: 50 } : { target_n: 100 },
      );
      expect(policy.mode).toBe(runType === "continuous" ? "live" : "closed");
      expect(policy.window.end === null).toBe(runType === "continuous");
      expect(screen.getByRole("figure")).toHaveAccessibleName(
        new RegExp(`Pick (individual )?${pickNames(unit)}`),
      );
      expect(screen.queryByRole("slider")).not.toBeInTheDocument();
      expect(
        screen.queryByText("Historical processing limit"),
      ).not.toBeInTheDocument();
      await userEvent.click(
        screen.getByRole("button", { name: "Preview sample" }),
      );
      await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
      expect(axios.post.mock.calls[0][1]).toMatchObject({
        row_type: rowType,
        sampling_policy: policy,
        evals: ["eval"],
      });
      expect(axios.post.mock.calls[0][1]).not.toHaveProperty("sampling_rate");
      expect(axios.post.mock.calls[0][1].filters).not.toHaveProperty(
        "date_range",
      );
      const parsed = NewTaskValidationSchema().parse(form.getValues());
      expect(parsed.samplingPolicy).toEqual(policy);
      expect(parsed.rowType).toBe(rowType);
      expect(samplingContextError(form.getValues())).toBe("");
      expect(
        await screen.findByLabelText("Sampling preview summary"),
      ).toHaveTextContent(
        `10 ${pickNames(unit)} · 1,000 ${rowNames(rowType)} evaluated`,
      );
      expect(screen.getByText(/Preview only, not a reservation/)).toBeVisible();
      for (const label of [
        /Plan sample size/,
        /Coverage options/,
        /Coordination/,
        /Missing unit ID/,
        /Priority/,
      ])
        expect(screen.queryByText(label)).not.toBeInTheDocument();
    },
  );

  it.each(["spans", "traces", "sessions"])(
    "only offers valid visible pick levels for %s",
    async (rowType) => {
      show(defaults(rowType));
      await enable(rowType);
      await userEvent.click(screen.getByRole("combobox", { name: "Pick:" }));
      expect(
        screen.getAllByRole("option").map((node) => node.textContent),
      ).toEqual(pickLevels(rowType).map((unit) => pickLabel(unit, rowType)));
    },
  );

  it.each(["historical", "continuous"])(
    "keeps exact per-group %s amounts and zero fallback",
    async (runType) => {
      show(defaults("spans", runType));
      await enable();
      const live = runType === "continuous",
        key = live ? "rate" : "target_n";
      await userEvent.click(
        screen.getByRole("button", {
          name: live ? "Set a share per group" : "Set a count per group",
        }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Choose group field" }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Choose values" }),
      );
      for (const [label, amount] of [
        [
          `All other conversations (not French or English)${live ? " (%)" : ""}`,
          "0",
        ],
        [`French${live ? " (%)" : ""}`, live ? "12.5" : "50"],
        [`English${live ? " (%)" : ""}`, live ? "25" : "60"],
      ]) {
        const input = screen.getByRole("spinbutton", { name: label });
        await userEvent.clear(input);
        await userEvent.type(input, amount);
        // The unit is on screen too, not only in the accessible name.
        const unit = within(input.closest(".MuiInputBase-root")).queryByText(
          "%",
        );
        if (live) expect(unit).toBeVisible();
        else expect(unit).toBeNull();
      }
      const saved = NewTaskValidationSchema().parse(
        form.getValues(),
      ).samplingPolicy;
      expect(saved.default[key]).toBe(0);
      expect(saved.strata.map((rule) => rule[key])).toEqual(
        live ? [12.5, 25] : [50, 60],
      );
      expect(saved.strata.map((rule) => rule.filters)).toEqual(
        ["French", "English"].map((value) => [
          { source: "metadata", key: "language", op: "in", values: [value] },
        ]),
      );
      expect(saved.group_by).toEqual([]);
      expect(isSimpleSamplingPolicy(saved)).toBe(true);
      await userEvent.click(
        screen.getByRole("button", { name: "Preview sample" }),
      );
      expect(axios.post.mock.calls[0][1].sampling_policy).toEqual(saved);
    },
  );

  it("chooses a bounded window explicitly without truncating retained historical row dates", async () => {
    show({
      startDate: "2025-10-01T00:00:00Z",
      endDate: "2026-10-01T00:00:00Z",
    });
    const before = structuredClone(form.getValues());
    const choice = screen.getByRole("radio", {
      name: "Pick whole groups of spans",
    });
    choice.focus();
    await userEvent.keyboard(" ");
    // A year is longer than group picking allows: the dialog proposes the
    // latest 31 days of it instead of an unrelated last-24-hours window.
    const dialog = await screen.findByRole("dialog", {
      name: "Use the last 31 days of your time window?",
    });
    expect(dialog).toHaveTextContent(
      "uses the most recent 31 days of your time window",
    );
    expect(screen.getByLabelText("New UTC window start")).toHaveValue(
      "2026-08-31T00:00",
    );
    expect(screen.getByLabelText("New UTC window end")).toHaveValue(
      "2026-10-01T00:00",
    );
    expect(form.getValues()).toEqual(before);
    await userEvent.keyboard("{Escape}");
    await closeDialog();
    expect(choice).toHaveFocus();
    expect(form.getValues()).toEqual(before);
    await userEvent.click(choice);
    await userEvent.click(
      screen.getByRole("button", { name: "Use group picking" }),
    );
    await closeDialog();
    expect(form.getValues("samplingPolicy.default")).toEqual({ target_n: 100 });
    expect(form.getValues("samplingPolicy.window")).toEqual({
      start: "2026-08-31T00:00:00.000Z",
      end: "2026-10-01T00:00:00.000Z",
    });
    expect(form.getValues("startDate")).toBe(before.startDate);
    expect(form.getValues("endDate")).toBe(before.endDate);
  });

  it("keeps a rolling time window that ends in the future without asking, ending it now", async () => {
    const startDate = new Date(Date.now() - 7 * 86400000).toISOString();
    show({
      startDate,
      endDate: new Date(Date.now() + 86400000).toISOString(),
    });
    await enable();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const { start, end } = form.getValues("samplingPolicy.window");
    expect(start).toBe(startDate);
    expect(Date.parse(end)).toBeLessThanOrEqual(Date.now());
    expect(Date.parse(end) - Date.parse(start)).toBeGreaterThan(6 * 86400000);
    expect(samplingContextError(form.getValues())).toBe("");
  });

  it("asks for a past window when the time window is entirely in the future", async () => {
    show({
      startDate: new Date(Date.now() + 86400000).toISOString(),
      endDate: new Date(Date.now() + 2 * 86400000).toISOString(),
    });
    await enable();
    expect(
      await screen.findByRole("dialog", {
        name: "Choose a fixed sample window",
      }),
    ).toHaveTextContent("nothing in the past to pick from");
    expect(form.getValues("samplingPolicy")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Use group picking" }),
    ).toBeEnabled();
  });

  it("explains the disabled group option directly under it", async () => {
    show();
    const input = screen.getByRole("spinbutton", {
      name: "Custom percentage (%)",
    });
    await userEvent.clear(input);
    const option = screen.getByRole("radio", {
      name: "Pick whole groups of spans",
    });
    const caption = screen.getByText(
      "Enter a valid percentage to switch to group picking.",
    );
    // Same radio group, right after the option it explains, not after the
    // percentage controls.
    expect(
      screen.getByRole("radiogroup", { name: "Data selection method" }),
    ).toContainElement(caption);
    expect(
      option.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      caption.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it.each(["light", "dark"])(
    "round trips per-group historical counts and incoming shares in %s without trapping the draft",
    async (mode) => {
      const policy = {
        ...simpleSamplingPolicy(defaults()),
        default: { target_n: 0 },
        strata: ["French", "English"].map((value, index) => ({
          name: `Language ${index}`,
          filters: [
            { source: "metadata", key: "language", op: "in", values: [value] },
          ],
          target_n: index ? 60 : 50,
        })),
      };
      show({ samplingPolicy: policy }, mode);
      const counts = structuredClone(form.getValues());
      await userEvent.click(
        screen.getByRole("radio", { name: "New incoming data" }),
      );
      expect(form.getValues()).toEqual(counts);
      await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
      await closeDialog();
      expect(form.getValues()).toEqual(counts);
      await userEvent.click(
        screen.getByRole("radio", { name: "New incoming data" }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Use percentages" }),
      );
      await closeDialog();
      expect(form.getValues("samplingPolicy")).toEqual({
        ...policy,
        mode: "live",
        window: { start: policy.window.start, end: null },
        default: { rate: 10 },
        strata: policy.strata.map(({ target_n, ...rule }) => ({
          ...rule,
          rate: 10,
        })),
      });
      for (const [name, value] of [
        ["All other conversations (not French or English) (%)", "0"],
        ["French (%)", "12.5"],
        ["English (%)", "25"],
      ]) {
        const input = screen.getByRole("spinbutton", { name });
        await userEvent.clear(input);
        await userEvent.type(input, value);
      }
      const shares = structuredClone(form.getValues());
      const historical = screen.getByRole("radio", { name: "Historical data" });
      expect(historical).toBeEnabled();
      await userEvent.click(historical);
      expect(await screen.findByRole("dialog")).toHaveTextContent(
        "Reset each group and All other conversations to 100",
      );
      expect(form.getValues()).toEqual(shares);
      expect(screen.getByLabelText("New UTC window start")).toHaveValue(
        "2026-10-01T00:00",
      );
      expect(screen.getByLabelText("New UTC window end")).toHaveValue(
        "2026-10-02T00:00",
      );
      await userEvent.keyboard("{Escape}");
      await closeDialog();
      expect(form.getValues()).toEqual(shares);
      await userEvent.click(historical);
      await userEvent.click(
        screen.getByRole("button", { name: "Use historical counts" }),
      );
      await closeDialog();
      expect(form.getValues("runType")).toBe("historical");
      expect(form.getValues("samplingPolicy")).toEqual({
        ...policy,
        default: { target_n: 100 },
        strata: policy.strata.map((rule) => ({ ...rule, target_n: 100 })),
      });
      for (const [name, value] of [
        ["All other conversations (not French or English)", "0"],
        ["French", "50"],
        ["English", "60"],
      ]) {
        const input = screen.getByRole("spinbutton", { name });
        expect(input).toHaveValue(100);
        await userEvent.clear(input);
        await userEvent.type(input, value);
      }
      expect(
        NewTaskValidationSchema().parse(form.getValues()).samplingPolicy,
      ).toEqual(policy);
      expect(samplingContextError(form.getValues())).toBe("");
      await userEvent.click(
        screen.getByRole("button", { name: "Preview sample" }),
      );
      expect(axios.post.mock.calls[0][1].sampling_policy).toEqual(policy);
      expect(form.getValues("startDate")).toBe(counts.startDate);
      expect(form.getValues("endDate")).toBe(counts.endDate);
      await userEvent.click(
        screen.getByRole("radio", { name: "New incoming data" }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Use percentages" }),
      );
      await closeDialog();
      expect(form.getValues("runType")).toBe("continuous");
      expect(form.getValues("samplingPolicy.default")).toEqual({ rate: 10 });
      expect(
        form.getValues("samplingPolicy.strata").map((rule) => rule.rate),
      ).toEqual([10, 10]);
      expect(samplingContextError(form.getValues())).toBe("");
    },
  );

  it("offers historical counts instead of forcing retained live shares back onto the incoming schedule", async () => {
    const policy = {
      ...simpleSamplingPolicy(defaults("spans", "continuous")),
      default: { rate: 0 },
      strata: [
        {
          name: "French speakers",
          filters: [
            {
              source: "metadata",
              key: "language",
              op: "in",
              values: ["French"],
            },
          ],
          rate: 12.5,
        },
      ],
    };
    show({ samplingPolicy: policy, runType: "continuous" });
    const loaded = structuredClone(form.getValues());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("radio", { name: "A percentage of matching spans" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Use a percentage" }),
    );
    await closeDialog();
    await userEvent.click(
      screen.getByRole("radio", { name: "Historical data" }),
    );
    const rows = structuredClone(form.getValues());
    await enable();
    await userEvent.click(
      screen.getByRole("button", { name: "Use historical counts" }),
    );
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Reset each group and All other conversations to 100",
    );
    expect(form.getValues()).toEqual(rows);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await closeDialog();
    expect(form.getValues()).toEqual(rows);
    // Cancel has not changed the retained policy or forced a schedule switch.
    await enable();
    await userEvent.click(
      screen.getByRole("button", { name: "Restore group picking" }),
    );
    await closeDialog();
    expect(form.getValues()).toEqual(loaded);
    await userEvent.click(
      screen.getByRole("radio", { name: "A percentage of matching spans" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Use a percentage" }),
    );
    await closeDialog();
    await userEvent.click(
      screen.getByRole("radio", { name: "Historical data" }),
    );
    await enable();
    await userEvent.click(
      screen.getByRole("button", { name: "Use historical counts" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Use historical counts" }),
    );
    await closeDialog();
    expect(form.getValues("runType")).toBe("historical");
    expect(form.getValues("samplingPolicy")).toEqual({
      ...policy,
      mode: "closed",
      window: { start: rows.startDate, end: rows.endDate },
      default: { target_n: 100 },
      strata: policy.strata.map(({ rate, ...rule }) => ({
        ...rule,
        target_n: 100,
      })),
    });
    expect(screen.getByRole("spinbutton", { name: "French" })).toHaveValue(100);
    expect(NewTaskValidationSchema().safeParse(form.getValues()).success).toBe(
      true,
    );
    expect(samplingContextError(form.getValues())).toBe("");
  });

  it("requires a bounded historical count window before replacing loaded incoming shares", async () => {
    const policy = simpleSamplingPolicy(defaults("spans", "continuous"));
    show({
      samplingPolicy: policy,
      runType: "continuous",
      startDate: "2025-10-01T00:00:00.000Z",
      endDate: "2026-10-01T00:00:00.000Z",
    });
    const loaded = structuredClone(form.getValues());
    const historical = screen.getByRole("radio", { name: "Historical data" });
    expect(historical).toBeEnabled();
    await userEvent.click(historical);
    const start = screen.getByLabelText("New UTC window start");
    const end = screen.getByLabelText("New UTC window end");
    const confirm = screen.getByRole("button", {
      name: "Use historical counts",
    });
    expect(confirm).toBeEnabled();
    expect(form.getValues()).toEqual(loaded);
    for (const value of [
      "",
      "2025-10-01T00:00",
      "2026-10-03T00:00",
      "2099-01-01T00:00",
    ]) {
      fireEvent.change(end, { target: { value: "2026-10-02T00:00" } });
      fireEvent.change(start, { target: { value } });
      expect(confirm).toBeDisabled();
      expect(form.getValues()).toEqual(loaded);
    }
    fireEvent.change(start, { target: { value: "2026-10-01T00:00" } });
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await closeDialog();
    expect(form.getValues("samplingPolicy.window")).toEqual({
      start: "2026-10-01T00:00:00.000Z",
      end: "2026-10-02T00:00:00.000Z",
    });
    expect(form.getValues("startDate")).toBe(loaded.startDate);
    expect(form.getValues("endDate")).toBe(loaded.endDate);
    expect(samplingContextError(form.getValues())).toBe("");
  });

  it("requires explicit confirmation to convert historical counts to incoming shares; Cancel is lossless", async () => {
    const policy = simpleSamplingPolicy(defaults());
    show({ samplingPolicy: policy });
    await userEvent.click(
      screen.getByRole("radio", { name: "New incoming data" }),
    );
    expect(await screen.findByRole("dialog")).toHaveTextContent(
      "Fixed counts do not apply",
    );
    expect(form.getValues("samplingPolicy")).toEqual(policy);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await closeDialog();
    expect(form.getValues("runType")).toBe("historical");
    expect(form.getValues("samplingPolicy")).toEqual(policy);
    await userEvent.click(
      screen.getByRole("radio", { name: "New incoming data" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Use percentages" }),
    );
    await closeDialog();
    expect(form.getValues("samplingPolicy.default")).toEqual({ rate: 10 });
    expect(form.getValues("samplingPolicy.window.end")).toBeNull();
    expect(
      screen.getByRole("spinbutton", {
        name: "What share of new conversations? (%)",
      }),
    ).toHaveValue(10);
    expect(
      screen.getByRole("radio", { name: "Historical data" }),
    ).toBeEnabled();
  });

  it("keeps a loaded closed continuous policy and late-row behavior without auto-converting it", () => {
    const policy = simpleSamplingPolicy(defaults());
    show({ samplingPolicy: policy, runType: "continuous" });
    expect(form.getValues("samplingPolicy")).toEqual(policy);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      screen.getByText(/New groups are not admitted after membership freezes/),
    ).toBeVisible();
  });

  it.each([
    [
      "historical",
      {},
      "Switch to 50% of matching spans, with your processing limit of 100,000 rows? Your group settings stay available if you switch back before leaving.",
    ],
    [
      "historical",
      { samplingRate: 100, spansLimit: "" },
      "Switch to all matching spans? Your group settings stay available if you switch back before leaving.",
    ],
    [
      "continuous",
      {},
      "Switch to 50% of matching spans? Your group settings stay available if you switch back before leaving.",
    ],
  ])(
    "names the percentage it switches back to, as the radio does (%s)",
    async (runType, values, text) => {
      const policy = simpleSamplingPolicy(defaults("spans", runType));
      show({ ...values, samplingPolicy: policy, runType });
      await userEvent.click(
        screen.getByRole("radio", { name: "A percentage of matching spans" }),
      );
      const dialog = screen.getByRole("dialog");
      expect(dialog).toHaveTextContent(text);
      expect(
        within(dialog).getByRole("button", { name: "Use a percentage" }),
      ).toBeVisible();
      await userEvent.click(
        within(dialog).getByRole("button", { name: "Cancel" }),
      );
      await closeDialog();
      expect(form.getValues("samplingPolicy")).toEqual(policy);
    },
  );

  it("does not confuse a saved-form reset with a deliberate switch to incoming data", async () => {
    show();
    const loaded = {
      ...defaults("traces", "continuous"),
      samplingPolicy: {
        ...simpleSamplingPolicy(defaults()),
        sampling_unit: "trace",
      },
    };
    await act(async () => form.reset(loaded));
    expect(form.getValues("samplingPolicy")).toEqual(loaded.samplingPolicy);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("revalidates a retained simple pick when the row type changed while using percentages", async () => {
    const policy = {
      ...simpleSamplingPolicy(defaults()),
      sampling_unit: "span",
    };
    show({ samplingPolicy: policy });
    await userEvent.click(
      screen.getByRole("radio", { name: "A percentage of matching spans" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Use a percentage" }),
    );
    await closeDialog();
    await act(async () => form.setValue("rowType", "sessions"));
    await enable("sessions");
    expect(form.getValues("samplingPolicy.sampling_unit")).toBe("session");
    expect(
      screen.getByText(/Pick changed to individual conversations/),
    ).toBeVisible();
    expect(form.getValues("samplingPolicy.default")).toEqual(policy.default);
  });

  it("revalidates finer pick levels after a row-type switch with a visible explanation", async () => {
    const policy = {
      ...simpleSamplingPolicy(defaults()),
      sampling_unit: "span",
    };
    show({ samplingPolicy: policy });
    await act(async () => form.setValue("rowType", "traces"));
    expect(form.getValues("samplingPolicy.sampling_unit")).toBe("trace");
    expect(screen.getByText(/Pick changed to individual traces/)).toBeVisible();
    await act(async () => form.setValue("rowType", "sessions"));
    expect(form.getValues("samplingPolicy.sampling_unit")).toBe("session");
    expect(
      screen.getByText(/Pick changed to individual conversations/),
    ).toBeVisible();
    expect(form.getValues("samplingPolicy.default")).toEqual(policy.default);
  });

  const advanced = [
    { coordination: "project", panel: "retained", prn_start: 12.5 },
    { default: { target_n: 3, min_n: 1, max_n: 5, rare_below: 2 } },
    { missing_id: "row" },
    { cell_quotas: { '["default",[]]': 0 } },
    { group_by: [{ source: "metadata", key: "language" }] },
    {
      strata: [
        {
          name: "First",
          target_n: 10,
          filters: [{ source: "span", key: "cost", op: "gt", values: ["0.5"] }],
        },
      ],
    },
    { default: { rate: 37.25 } },
    {
      mode: "live",
      window: { start: "2026-10-01T00:00:00Z", end: null },
      default: { rate: 10 },
      strata: [
        {
          name: "Complex",
          rate: 50,
          filters: [
            { source: "span", key: "language", op: "not_in", values: ["en"] },
          ],
        },
      ],
    },
    {
      strata: [42, "42"].map((value, index) => ({
        name: `Alias ${index}`,
        target_n: index + 1,
        filters: [
          { source: "span", key: "contact", op: "in", values: [value] },
        ],
      })),
    },
    { future_field: "opaque" },
  ];
  it.each(advanced)(
    "loads advanced settings losslessly and only replaces on explicit confirmation: %j",
    async (patch) => {
      const policy = { ...simpleSamplingPolicy(defaults()), ...patch };
      show({
        samplingPolicy: policy,
        runType: policy.mode === "live" ? "continuous" : "historical",
      });
      const before = structuredClone(form.getValues());
      expect(
        screen.getByLabelText("Advanced sampling settings"),
      ).toHaveTextContent(advancedSamplingSummary(policy));
      expect(
        screen.queryByRole("combobox", { name: "Pick:" }),
      ).not.toBeInTheDocument();
      expect(form.getValues()).toEqual(before);
      // Preview serializes the retained policy, including normalization of complete
      // numeric predicates, without rewriting any form bytes.
      await userEvent.click(
        screen.getByRole("button", { name: "Preview sample" }),
      );
      expect(axios.post.mock.calls[0][1].sampling_policy).toEqual(
        samplingPolicyForApi(policy),
      );
      expect(form.getValues()).toEqual(before);
      await userEvent.click(
        screen.getByRole("button", {
          name: "Replace with simple group picking",
        }),
      );
      expect(form.getValues()).toEqual(before);
      await userEvent.keyboard("{Escape}");
      await closeDialog();
      expect(form.getValues()).toEqual(before);
      await userEvent.click(
        screen.getByRole("button", {
          name: "Replace with simple group picking",
        }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Replace settings" }),
      );
      await closeDialog();
      expect(isSimpleSamplingPolicy(form.getValues("samplingPolicy"))).toBe(
        true,
      );
      expect(form.getValues("samplingPolicy")).not.toEqual(policy);
    },
  );

  it("retains an advanced policy when row type changes and blocks incompatible preview/save contexts", async () => {
    const policy = {
      ...simpleSamplingPolicy(defaults()),
      sampling_unit: "span",
      coordination: "project",
    };
    show({ samplingPolicy: policy });
    await act(async () => form.setValue("rowType", "sessions"));
    expect(form.getValues("samplingPolicy")).toEqual(policy);
    expect(
      screen.getByRole("button", { name: "Preview sample" }),
    ).toBeDisabled();
    expect(samplingContextError(form.getValues())).toMatch(/cannot be finer/);
  });

  it("restores a retained live policy only with its required schedule and keeps it exact on Cancel", async () => {
    const policy = {
      ...simpleSamplingPolicy(defaults("spans", "continuous")),
      coordination: "project",
    };
    show({ samplingPolicy: policy, runType: "continuous" });
    expect(
      screen.getByRole("radio", { name: "Historical data" }),
    ).toBeDisabled();
    await userEvent.click(
      screen.getByRole("radio", { name: "A percentage of matching spans" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Use a percentage" }),
    );
    await closeDialog();
    await userEvent.click(
      screen.getByRole("radio", { name: "Historical data" }),
    );
    await enable();
    expect(
      screen.queryByRole("button", { name: "Use historical counts" }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await closeDialog();
    expect(form.getValues("samplingPolicy")).toBeNull();
    expect(form.getValues("runType")).toBe("historical");
    await enable();
    await userEvent.click(
      screen.getByRole("button", { name: "Restore group picking" }),
    );
    await closeDialog();
    expect(form.getValues("samplingPolicy")).toEqual(policy);
    expect(form.getValues("runType")).toBe("continuous");
  });

  it.each(["light", "dark"])(
    "gives each illustration a visible text equivalent in %s",
    async (mode) => {
      show(defaults(), mode);
      await enable();
      for (const unit of pickLevels("spans")) {
        await choose(unit, "spans");
        const figure = screen.getByRole("figure");
        expect(figure).toHaveAccessibleName(
          figure.querySelector(".MuiTypography-body2").textContent,
        );
        expect(figure).toHaveTextContent("evaluated");
        if (unit === "user")
          expect(figure).toHaveTextContent("end users of your application");
        if (unit === "attribute")
          expect(
            screen.getByText(
              /project’s contact attribute \(if one is set in project settings\)/,
            ),
          ).toBeVisible();
      }
    },
  );
});

describe("per-group counts and the preview by group", () => {
  const addGroups = async () => {
    await enable();
    await userEvent.click(
      screen.getByRole("button", { name: "Set a count per group" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Choose group field" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Choose values" }),
    );
  };

  it("lists every count after the groups, says what the first count now covers, and totals them", async () => {
    show();
    await addGroups();
    expect(
      screen.queryByRole("spinbutton", { name: "How many conversations?" }),
    ).not.toBeInTheDocument();
    const list = screen.getByLabelText("Counts per group");
    const inputs = within(list).getAllByRole("spinbutton");
    expect(inputs.map((input) => input.getAttribute("aria-label"))).toEqual([
      "French",
      "English",
      "All other conversations (not French or English)",
    ]);
    expect(inputs.map((input) => input.value)).toEqual(["50", "50", "100"]);
    expect(within(list).getByRole("status")).toHaveTextContent(
      "The count of 100 you set now applies only to conversations outside these groups.",
    );
    expect(
      within(list).getByLabelText("Requested group selection"),
    ).toHaveTextContent(/^Up to 200 conversations in total$/);
    await userEvent.click(
      screen.getByRole("button", { name: "Remove per-group counts" }),
    );
    expect(
      screen.getByRole("spinbutton", { name: "How many conversations?" }),
    ).toHaveValue(100);
    expect(screen.queryByLabelText("Counts per group")).toBeNull();
  });

  it("shows requested, available and picked per group and says why the sample is short", async () => {
    show();
    await addGroups();
    const cell = (name, eligible, selected, rows) => ({
      stratum: JSON.stringify([name, []]),
      eligible_units: eligible,
      selected_units: selected,
      selected_rows: rows,
      expected_units: selected,
    });
    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          strata: [cell("Group 1", 8, 8, 80), cell("default", 400, 100, 900)],
          evaluator_count: 1,
          expected_evaluations: 980,
        },
      },
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    expect(
      await screen.findByLabelText("Sampling preview summary"),
    ).toHaveTextContent("108 conversations · 980 spans evaluated");
    expect(
      screen.getByLabelText("Sampling preview shortfall"),
    ).toHaveTextContent(
      "French (8 of 50) and English (0 of 50) have fewer than requested, so 108 of 200 conversations can be picked. Widen the window or lower those counts.",
    );
    const table = screen.getByRole("table", { name: "Preview by group" });
    expect(
      within(table)
        .getAllByRole("row")
        .map((row) =>
          within(row)
            .queryAllByRole("cell")
            .map((node) => node.textContent),
        )
        .filter((cells) => cells.length),
    ).toEqual([
      ["French", "50", "8", "8", "80"],
      ["English", "50", "0", "0", "0"],
      ["All other conversations", "100", "400", "100", "900"],
    ]);
  });

  it("keeps a one-group preview to the summary and states a short count plainly", async () => {
    show();
    await enable();
    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          strata: [
            {
              stratum: '["default",[]]',
              eligible_units: 18,
              selected_units: 18,
              selected_rows: 288,
              expected_units: 18,
            },
          ],
          evaluator_count: 0,
          expected_evaluations: null,
        },
      },
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    expect(
      await screen.findByLabelText("Sampling preview shortfall"),
    ).toHaveTextContent(
      "Only 18 conversations match in this window, fewer than the 100 requested. Widen the window or lower the count.",
    );
    expect(
      screen.getByText(
        "Picked from 18 matching conversations. Add evaluators to estimate runs.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("table", { name: "Preview by group" }),
    ).toBeNull();
    // Said once, not again as a second "select evaluators" line.
    expect(screen.queryAllByText(/evaluators to estimate/)).toHaveLength(1);
  });
});
