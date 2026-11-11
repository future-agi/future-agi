import React from "react";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { enqueueSnackbar } from "notistack";
import { handleError } from "src/utils/queryErrorHandler";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import SamplingAuditPanel from "./SamplingAuditPanel";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    project: { samplingAudit: () => "/tracer/eval-task/sampling_audit/" },
  },
}));

const renderPanel = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false } },
          queryCache: new QueryCache({ onError: handleError }),
        })
      }
    >
      <SamplingAuditPanel taskId="task-1" />
    </QueryClientProvider>,
  );

const stratum = (name, locale) =>
  JSON.stringify([name, locale ? [JSON.stringify([true, locale])] : []]);

const row = (overrides) => ({
  target: null,
  expected_size: null,
  shortage: null,
  exact: null,
  srm_z: null,
  srm_p_value: null,
  srm_flagged: null,
  population_check: "match",
  ...overrides,
});

const CLOSED = {
  cohort_id: "c1",
  policy_version: "v",
  mode: "closed",
  sampling_unit: "session",
  coordination: "project",
  frozen_at: "2026-10-22T10:00:00Z",
  complete: true,
  population_source: "frozen_snapshot",
  strata: [
    row({
      stratum: stratum("rare", "en"),
      rule: "rare",
      population_size: 3,
      target: 5,
      expected_size: 3,
      achieved: 3,
      shortage: 2,
      exact: true,
      inclusion_probability: 1,
      design_weight: 1,
    }),
    row({
      stratum: stratum("default", "fr"),
      rule: "default",
      population_size: 20,
      target: 3,
      expected_size: 3,
      achieved: 3,
      shortage: 0,
      exact: true,
      inclusion_probability: 0.15,
      design_weight: 6.666666666666667,
    }),
  ],
  summary: {
    strata: 2,
    population_size: 23,
    achieved: 6,
    kish_effective_n: 3.9384615384615387,
    design_effect: 1.5234375,
    weight_cv: 0.7234,
    weighted_population: 23,
    represented_population: 23,
    unrepresented_strata: 0,
    unrepresented_population: 0,
    flagged_strata: 0,
    integrity: "ok",
    srm_alpha: 0.0005,
  },
  balance: [
    {
      attribute: "slice",
      source: null,
      categories: [
        {
          value: "default",
          present: true,
          population_size: 20,
          achieved: 3,
          population_share: 20 / 23,
          sample_share: 0.5,
          weighted_share: 20 / 23,
          standardized_difference: -0.84,
          weighted_standardized_difference: 0,
        },
      ],
      chi_square: 4.5,
      degrees_of_freedom: 1,
      p_value: 0.0339,
      max_abs_standardized_difference: 0.84,
      max_abs_weighted_standardized_difference: 0,
      weighted_imbalance: false,
    },
  ],
  notes: ["Population per stratum is re-read at the cohort's freeze time."],
};

beforeEach(() => {
  vi.clearAllMocks();
  axios.get.mockReset();
});

describe("Sampling audit panel", () => {
  it.each([
    [14, "Too many audit requests. Try again in 14 seconds."],
    [0, "Too many audit requests. Try again in 0 seconds."],
    [undefined, "Too many audit requests. Please wait before trying again."],
    [
      "<script>14</script>",
      "Too many audit requests. Please wait before trying again.",
    ],
  ])(
    "keeps Retry and gives wait advice for audit throttling (%s)",
    async (retryAfterSeconds, message) => {
      axios.get
        .mockRejectedValueOnce({
          statusCode: 429,
          retryAfterSeconds,
          detail: "Check your policy",
        })
        .mockResolvedValueOnce({ data: { result: CLOSED } });
      renderPanel();
      expect(await screen.findByRole("alert")).toHaveTextContent(message);
      expect(screen.getByRole("alert")).not.toHaveTextContent(/check.*policy/i);
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(await screen.findByLabelText("Sampling audit")).toBeVisible();
      expect(axios.get).toHaveBeenCalledTimes(2);
    },
  );

  it("handles only the expected no-cohort 404 inline without a duplicate global toast and retries", async () => {
    const message =
      "No sampling cohort exists for this task's current policy yet. It is created when the task first runs.";
    axios.get
      .mockRejectedValueOnce({ statusCode: 404, result: message })
      .mockResolvedValueOnce({ data: { result: CLOSED } });
    renderPanel();
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("alert")).toHaveClass("MuiAlert-standardInfo");
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByLabelText("Sampling audit")).toBeVisible();
    expect(axios.get).toHaveBeenCalledTimes(2);
  });

  it.each([404, 409, 500])(
    "still surfaces unexpected audit %s errors globally",
    async (statusCode) => {
      axios.get.mockRejectedValue({
        statusCode,
        result: "Unexpected audit failure",
      });
      renderPanel();
      expect(await screen.findByRole("alert")).toBeVisible();
      await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalledTimes(1));
      expect(enqueueSnackbar).toHaveBeenCalledWith(expect.any(String), {
        variant: "error",
      });
      expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    },
  );

  it("shows the frozen design, weights, shortage and balance read-only", async () => {
    axios.get.mockResolvedValue({ data: { result: CLOSED } });
    renderPanel();
    expect(
      await screen.findByText(
        "Quota and population counts match in every group; identical membership is not verified.",
      ),
    ).toBeVisible();
    expect(axios.get).toHaveBeenCalledWith(
      "/tracer/eval-task/sampling_audit/",
      { params: { eval_task_id: "task-1" } },
    );
    expect(screen.getByText("3.9")).toBeVisible(); // Kish effective n
    expect(screen.getByText("1.523")).toBeVisible(); // design effect
    const strata = screen.getByRole("table", {
      name: "Sampling audit by group",
    });
    const rare = within(strata).getByText("rare · en").closest("tr");
    expect(
      within(rare)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["rare · en", "3", "5", "3", "2", "1", "1", "Exact"]);
    expect(
      within(strata).getByText("All other conversations · fr"),
    ).toBeVisible();
    const balance = screen.getByRole("table", {
      name: "Balance on group rule",
    });
    expect(within(balance).getByText("All other conversations")).toBeVisible();
    // Population share and weighted share are equal: weights restore it.
    expect(within(balance).getAllByText("87%")).toHaveLength(2);
    expect(within(balance).getByText("50%")).toBeVisible();
    expect(
      screen.getByText(/No weighted imbalance above the threshold/),
    ).toBeVisible();
    // The only action reloads server truth; none edits the policy or cohort.
    // The other button only shows or hides the statistics on this page.
    expect(screen.getAllByRole("button")).toEqual([
      screen.getByRole("button", { name: "Refresh sampling audit" }),
      screen.getByRole("button", { name: "Statistical details" }),
    ]);
    // Never collides with the cohort-sampling flow's preview table locator.
    expect(
      screen.queryByRole("columnheader", { name: /eligible units/i }),
    ).not.toBeInTheDocument();
  });

  it("flags a live stratum whose selections drift from its rate", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: {
          ...CLOSED,
          mode: "live",
          frozen_at: null,
          population_source: "persisted_decisions",
          strata: [
            row({
              stratum: stratum("default"),
              rule: "default",
              population_size: 2000,
              expected_size: 200,
              achieved: 140,
              inclusion_probability: 0.1,
              design_weight: 10,
              population_check: "persisted",
              srm_z: -4.47,
              srm_p_value: 3.07e-6,
              srm_flagged: true,
            }),
          ],
          summary: {
            ...CLOSED.summary,
            flagged_strata: 1,
            integrity: "attention",
          },
        },
      },
    });
    renderPanel();
    // One stratum and no groups: the sample itself is flagged, with why.
    expect(
      await screen.findByText(
        "This sample needs attention: the share picked differs from the set share by more than chance (p below 0.0005).",
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Results from it may be skewed. Refresh to check again as new data arrives.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Mismatch p=3.1e-6")).toBeVisible();
    // Every check uses the one status badge, not a second chip style.
    expect(document.querySelector(".MuiChip-root")).toBeNull();
    expect(screen.getByLabelText("Sampling audit")).toHaveTextContent(
      "New incoming data · Picks a share of new conversations",
    );
    expect(screen.getByText("Decided conversations")).toBeVisible();
  });

  it("never shows an empty frozen sample as a pass, and says what to do next", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: {
          ...CLOSED,
          coordination: "independent",
          strata: [],
          balance: [],
          notes: [],
          summary: {
            ...CLOSED.summary,
            strata: 0,
            population_size: 0,
            achieved: 0,
            kish_effective_n: null,
            design_effect: null,
            weight_cv: null,
            integrity: "ok",
          },
        },
      },
    });
    const onEditSelection = vi.fn();
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <SamplingAuditPanel
          taskId="task-1"
          onEditSelection={onEditSelection}
          policy={{
            version: 1,
            sampling_unit: "session",
            missing_id: "exclude",
            mode: "closed",
            window: {
              start: "2026-11-03T00:00:00.000Z",
              end: "2026-11-10T00:00:00.000Z",
            },
            default: { target_n: 1000 },
            strata: [],
            group_by: [],
          }}
        />
      </QueryClientProvider>,
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      `Nothing was sampled: no conversations matched this task’s filters and window when the sample froze. Requested: up to ${(1000).toLocaleString()}. Widen the window or loosen the filters, then save to pick a new sample.`,
    );
    expect(alert).not.toHaveClass("MuiAlert-standardSuccess");
    expect(screen.queryByText(/Quota and population counts match/)).toBeNull();
    expect(screen.queryByText("Counts match")).toBeNull();
    // Zero tiles and a header over no rows add nothing to the warning.
    expect(
      screen.queryByRole("table", { name: "Sampling audit by group" }),
    ).toBeNull();
    expect(screen.queryByText("Matching conversations")).toBeNull();
    await userEvent.click(
      within(alert).getByRole("button", { name: "Edit task" }),
    );
    expect(onEditSelection).toHaveBeenCalledTimes(1);
    // Unavailable diagnostics stay explicit, never 0.
    expect(screen.getAllByText("—")).toHaveLength(3);
  });

  it("keeps the counts of a matched population that had no picks", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: {
          ...CLOSED,
          strata: [
            row({
              stratum: stratum("default"),
              rule: "default",
              population_size: 40,
              target: 0,
              expected_size: 0,
              achieved: 0,
              shortage: 0,
              exact: true,
              inclusion_probability: 0,
              design_weight: null,
            }),
          ],
          balance: [],
          notes: [],
          summary: {
            ...CLOSED.summary,
            strata: 1,
            population_size: 40,
            achieved: 0,
            kish_effective_n: null,
            design_effect: null,
            weight_cv: null,
          },
        },
      },
    });
    renderPanel();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Nothing was sampled from the 40 conversations that matched when the sample froze. Raise the counts, then save to pick a new sample.",
    );
    // No edit handler (no permission): no button in the alert.
    expect(within(screen.getByRole("alert")).queryByRole("button")).toBeNull();
    const strata = screen.getByRole("table", {
      name: "Sampling audit by group",
    });
    expect(
      within(within(strata).getByText("All conversations").closest("tr"))
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["All conversations", "40", "0", "0", "0", "Exact"]);
  });

  it("states a plain random pick once and keeps the statistics folded", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: {
          ...CLOSED,
          coordination: "independent",
          frozen_at: "2026-11-10T20:27:05Z",
          strata: [
            row({
              stratum: stratum("default"),
              rule: "default",
              population_size: 350,
              target: 10,
              expected_size: 10,
              achieved: 10,
              shortage: 0,
              exact: true,
              inclusion_probability: 10 / 350,
              design_weight: 35,
            }),
          ],
          summary: {
            ...CLOSED.summary,
            strata: 1,
            population_size: 350,
            achieved: 10,
            kish_effective_n: 10,
            design_effect: 1,
            weight_cv: 0,
          },
        },
      },
    });
    renderPanel();
    expect(await screen.findByText("Counts match")).toBeVisible();
    expect(screen.getByLabelText("Sampling audit")).toHaveTextContent(
      "Historical window · Picks conversations at random · Frozen",
    );
    expect(screen.queryByText(/coordination:/)).toBeNull();
    // One name per number: the strip and the table both say Matching / Picked.
    const matching = within(
      screen.getByText("Matching conversations").parentElement,
    );
    expect(matching.getByText("350")).toBeVisible();
    expect(
      within(screen.getByText("Picked conversations").parentElement).getByText(
        "10",
      ),
    ).toBeVisible();
    // With one group nothing can be unrepresented beside it.
    expect(screen.queryByText("Unrepresented")).toBeNull();
    const strata = screen.getByRole("table", {
      name: "Sampling audit by group",
    });
    expect(
      within(strata)
        .getAllByRole("columnheader")
        .map((cell) => cell.textContent),
    ).toEqual([
      "Group",
      "Matching",
      "Requested",
      "Picked",
      "Shortage",
      "Check",
    ]);
    expect(
      within(within(strata).getByText("All conversations").closest("tr"))
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["All conversations", "350", "10", "10", "0", "Exact"]);
    const toggle = screen.getByRole("button", { name: "Statistical details" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    // The single group's weights sit with the other statistics.
    const diagnostics = screen.getByLabelText("Weight diagnostics");
    const text = (selector) =>
      [...diagnostics.querySelectorAll(selector)].map(
        (node) => node.textContent,
      );
    // The page's own number format (at most three decimals).
    expect(text("dd")).toEqual(["10", "1", "0", "0.029", "35"]);
    expect(text("dt")).toEqual([
      "Kish effective n",
      "Design effect",
      "Weight CV",
      "Inclusion probability",
      "Design weight",
    ]);
  });

  it("names a simple policy's groups by the value picked and opens the statistics when a group is flagged", async () => {
    const group = (name, overrides) =>
      row({
        stratum: stratum(name),
        rule: name,
        exact: true,
        shortage: 0,
        ...overrides,
      });
    axios.get.mockResolvedValue({
      data: {
        result: {
          ...CLOSED,
          coordination: "independent",
          strata: [
            group("Group 1", {
              population_size: 8,
              target: 10,
              achieved: 8,
              shortage: 2,
              inclusion_probability: 1,
              design_weight: 1,
              population_check: "changed",
            }),
            group("default", {
              population_size: 400,
              target: 15,
              achieved: 15,
              inclusion_probability: 0.0375,
              design_weight: 26.667,
            }),
          ],
          summary: {
            ...CLOSED.summary,
            population_size: 408,
            achieved: 23,
            flagged_strata: 1,
            integrity: "attention",
          },
          balance: [
            {
              ...CLOSED.balance[0],
              categories: [
                { ...CLOSED.balance[0].categories[0], value: "Group 1" },
                CLOSED.balance[0].categories[0],
              ],
            },
          ],
        },
      },
    });
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <SamplingAuditPanel
          taskId="task-1"
          policy={{
            version: 1,
            sampling_unit: "session",
            missing_id: "exclude",
            mode: "closed",
            window: {
              start: "2026-11-03T00:00:00.000Z",
              end: "2026-11-10T00:00:00.000Z",
            },
            default: { target_n: 15 },
            strata: [
              {
                name: "Group 1",
                filters: [
                  {
                    source: "span",
                    key: "region",
                    op: "in",
                    values: ["Europe"],
                  },
                ],
                target_n: 10,
              },
            ],
            group_by: [],
          }}
        />
      </QueryClientProvider>,
    );
    // The flagged group is named by its value, with what its badge means and
    // an honest next step: re-running keeps the same frozen picks.
    expect(
      await screen.findByText(
        "Europe needs attention: the conversations that match it changed after the sample froze.",
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        "To pick a new sample from the current data, duplicate the task or change its window, filters or counts, then save.",
      ),
    ).toBeVisible();
    // No edit handler, no action.
    expect(screen.queryByRole("button", { name: "Edit task" })).toBeNull();
    const strata = screen.getByRole("table", {
      name: "Sampling audit by group",
    });
    expect(
      within(within(strata).getByText("Europe").closest("tr"))
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["Europe", "8", "10", "8", "2", "1", "1", "Population changed"]);
    expect(within(strata).queryByText("Group 1")).toBeNull();
    expect(within(strata).getByText("All other conversations")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Statistical details" }),
    ).toHaveAttribute("aria-expanded", "true");
    const balance = screen.getByRole("table", {
      name: "Balance on group rule",
    });
    expect(within(balance).getByText("Europe")).toBeVisible();
    expect(screen.getByText("Unrepresented")).toBeVisible();
  });

  it("names up to three flagged groups and explains each badge shown", async () => {
    const onEditSelection = vi.fn();
    const flaggedRow = (name, overrides) =>
      row({
        stratum: stratum(name),
        rule: name,
        population_size: 10,
        target: 5,
        expected_size: 5,
        achieved: 5,
        shortage: 0,
        exact: true,
        inclusion_probability: 0.5,
        design_weight: 2,
        ...overrides,
      });
    axios.get.mockResolvedValue({
      data: {
        result: {
          ...CLOSED,
          strata: [
            flaggedRow("a", { population_check: "changed" }),
            flaggedRow("b", { exact: false, achieved: 4, shortage: 1 }),
            flaggedRow("c", { population_check: "changed" }),
            flaggedRow("d", { exact: false, achieved: 3, shortage: 2 }),
            flaggedRow("e"),
          ],
          summary: {
            ...CLOSED.summary,
            flagged_strata: 4,
            integrity: "attention",
          },
        },
      },
    });
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <SamplingAuditPanel taskId="task-1" onEditSelection={onEditSelection} />
      </QueryClientProvider>,
    );
    expect(
      await screen.findByText("a, b, c, and 1 more group need attention."),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Population changed: the conversations that match the group changed after the sample froze.",
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Not exact: the number picked differs from the expected count.",
      ),
    ).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Edit task" }));
    expect(onEditSelection).toHaveBeenCalledTimes(1);
  });

  it("falls back to the count when the flagged groups cannot be matched", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: {
          ...CLOSED,
          summary: {
            ...CLOSED.summary,
            flagged_strata: 2,
            integrity: "attention",
          },
        },
      },
    });
    renderPanel();
    expect(await screen.findByText("2 groups need attention.")).toBeVisible();
    expect(screen.queryByText(/^Population changed:/)).toBeNull();
  });

  it("explains a missing cohort instead of failing", async () => {
    axios.get.mockRejectedValue({
      response: {
        status: 404,
        data: {
          message:
            "No sampling cohort exists for this task's current policy yet. It is created when the task first runs.",
        },
      },
    });
    renderPanel();
    expect(await screen.findByText(/No sampling cohort exists/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });
});
