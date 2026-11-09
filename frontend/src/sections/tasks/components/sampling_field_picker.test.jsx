import React from "react";
import PropTypes from "prop-types";
import { useController, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import axios, { readQuery, endpoints } from "src/utils/axios";
import TaskSamplingSection from "./task_sampling_section";
import { SamplingValuePicker } from "./sampling_field_picker";
import {
  defaultSamplingPolicy,
  samplingPolicyForApi,
} from "./samplingPolicyForm";

vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  readQuery: vi.fn(),
  default: { post: vi.fn(), get: vi.fn() },
}));
const catalogPage = (metrics, more = false) => ({
  metrics,
  query_complete: true,
  query_status: "complete",
  query_exact: false,
  query_provenance: "current_property_catalog",
  total: null,
  total_is_exact: false,
  has_more: more,
  next_cursor: more ? "field-page-2" : null,
});
const field = (name) => ({
  name,
  display_name: name,
  property_id: `custom_attribute:${name}`,
  type: "string",
});
let form;
const loaded = () => ({
  ...defaultSamplingPolicy(),
  mode: "live",
  window: { start: "2026-10-01T00:00:00Z", end: null },
  default: { rate: 25 },
});
function Harness({ policy }) {
  form = useForm({
    defaultValues: {
      project: "project-a",
      rowType: "spans",
      runType: "continuous",
      samplingRate: 50,
      filters: [],
      evalsDetails: [],
      samplingPolicy: policy || loaded(),
    },
  });
  return (
    <TaskSamplingSection control={form.control} getValues={form.getValues} />
  );
}
Harness.propTypes = { policy: PropTypes.object };
function ValueHarness({ values }) {
  form = useForm({ defaultValues: { values } });
  const { field: selected } = useController({
    control: form.control,
    name: "values",
  });
  return (
    <SamplingValuePicker
      project="project-a"
      predicate={{
        source: "metadata",
        key: "account",
        op: "in",
        values: selected.value,
      }}
      onChange={selected.onChange}
    />
  );
}
ValueHarness.propTypes = { values: PropTypes.array.isRequired };
const show = (element = <Harness />) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {element}
    </QueryClientProvider>,
  );
const close = () =>
  waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
const group = async () =>
  userEvent.click(
    screen.getByRole("button", { name: "Set a share per group" }),
  );
const chooseField = async (name) => {
  await userEvent.click(
    screen.getByRole("button", { name: "Choose group field" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name, exact: true }),
  );
  await close();
};
const filters = (policy) => policy.strata.map((rule) => rule.filters[0]);
beforeEach(() => {
  vi.clearAllMocks();
  readQuery.mockImplementation(async (url, { params }) => {
    if (url === endpoints.dashboard.metrics)
      return {
        data: {
          result: catalogPage(
            params.cursor ? [field("region")] : [field("locale")],
            !params.cursor,
          ),
        },
      };
    if (url === endpoints.dashboard.filterValues)
      return {
        data: {
          result: {
            values: [
              { value: "en,CA", type: "string" },
              { value: 42, type: "number" },
              { value: true, type: "boolean" },
            ],
            has_more: false,
            next_cursor: null,
            query_complete: true,
            query_status: "complete",
          },
        },
      };
    throw new Error(`Unexpected request ${url}`);
  });
  axios.post.mockResolvedValue({ data: { result: { strata: [] } } });
});

describe("sampling matcher compatibility", () => {
  it("disables array/object catalog fields so they cannot become dead group rules", async () => {
    const standardRead = readQuery.getMockImplementation();
    readQuery.mockImplementation((url, options) =>
      url === endpoints.dashboard.metrics
        ? Promise.resolve({
            data: {
              result: catalogPage([
                {
                  ...field("customer.regions"),
                  type: "array",
                  attribute_types: ["array"],
                },
                {
                  ...field("customer.profile"),
                  type: "map",
                  attribute_types: ["map"],
                },
                {
                  ...field("customer.payload"),
                  type: "json",
                  attribute_types: ["json"],
                },
                {
                  ...field("customer.mixed"),
                  attribute_types: ["array", "string"],
                },
                field("locale"),
              ]),
            },
          })
        : standardRead(url, options),
    );
    show();
    await group();
    const before = structuredClone(form.getValues("samplingPolicy"));
    await userEvent.click(
      screen.getByRole("button", { name: "Choose group field" }),
    );
    for (const name of [
      "customer.regions",
      "customer.profile",
      "customer.payload",
      "customer.mixed",
    ]) {
      const choice = await screen.findByRole("button", { name, exact: true });
      expect(choice).toBeDisabled();
      expect(choice).toHaveAccessibleDescription(
        /Sampling matches only scalar string, number and boolean fields/,
      );
      choice.click();
      expect(form.getValues("samplingPolicy")).toEqual(before);
    }
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await close();
    expect(form.getValues("samplingPolicy")).toEqual(before);
    expect(form.getValues("samplingPolicy.strata")).toEqual([]);
    expect(
      screen.getByRole("button", { name: "Choose values" }),
    ).toBeDisabled();
  });

  it("rejects array-member/object suggestions and serializes scalar values using catalog type metadata", async () => {
    const standardRead = readQuery.getMockImplementation();
    readQuery.mockImplementation((url, options) =>
      url === endpoints.dashboard.filterValues
        ? Promise.resolve({
            data: {
              result: {
                values: [
                  { value: "CA", type: "array" },
                  { value: { region: "CA" }, type: "map" },
                  { value: ["CA"], type: "json" },
                  { value: null, type: "json" },
                  { value: "42", type: "number" },
                  { value: "false", type: "boolean" },
                  { value: "42", type: "string" },
                  { value: true, type: "boolean" },
                  { value: 0, type: "number" },
                ],
                has_more: false,
                next_cursor: null,
                query_complete: true,
                query_status: "complete",
              },
            },
          })
        : standardRead(url, options),
    );
    show();
    await group();
    await chooseField("locale");
    await userEvent.click(
      screen.getByRole("button", { name: "Choose values" }),
    );
    await userEvent.click(
      screen.getByRole("combobox", { name: "Find or enter values" }),
    );
    await screen.findByRole("option", { name: "true boolean" });
    expect(
      screen.queryByRole("option", { name: /^CA / }),
    ).not.toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(5);
    for (const name of [
      "42 number",
      "false boolean",
      "42 string",
      "true boolean",
      "0 number",
    ])
      await userEvent.click(screen.getByRole("option", { name }));
    expect(screen.getByRole("button", { name: "Use values" })).toBeDisabled();
    expect(screen.getByText(/Some values match the same group/)).toBeVisible();
    expect(form.getValues("samplingPolicy.strata")).toEqual([]);
    const aliasChip = screen.getByText("42 (text)").closest(".MuiChip-root");
    await userEvent.click(aliasChip.querySelector(".MuiChip-deleteIcon"));
    expect(screen.getByRole("button", { name: "Use values" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Use values" }));
    await close();
    const expected = [42, false, true, 0].map((value) => ({
      source: "span",
      key: "locale",
      op: "in",
      values: [value],
    }));
    expect(
      filters(samplingPolicyForApi(form.getValues("samplingPolicy"))),
    ).toEqual(expected);
    expect(form.getValues("samplingPolicy")).not.toHaveProperty("cell_quotas");
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(filters(axios.post.mock.calls[0][1].sampling_policy)).toEqual(
      expected,
    );
  });
});

describe("sampling group discovery using existing query adapters", () => {
  it("keeps saved structured/null predicates and quotas read-only without modifying their wire values", async () => {
    const values = [{ value: "saved JSON" }, null, { items: [1, true] }];
    const original = {
      ...loaded(),
      strata: [
        {
          name: "Priority",
          rate: 25,
          filters: [{ source: "metadata", key: "account", op: "in", values }],
        },
      ],
      cell_quotas: { '["default",[]]': 2 },
    };
    show(<Harness policy={original} />);
    expect(screen.getByLabelText("Advanced sampling settings")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Choose values" }),
    ).not.toBeInTheDocument();
    expect(form.getValues("samplingPolicy")).toEqual(original);
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    expect(axios.post.mock.calls[0][1].sampling_policy).toEqual(original);
    expect(readQuery).not.toHaveBeenCalled();
  });

  it("keeps the value picker's JSON envelope safety and Cancel behavior independently of the read-only task policy", async () => {
    const values = [{ value: "saved JSON" }, null, { items: [1, true] }];
    show(<ValueHarness values={values} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Choose values" }),
    );
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Value suggestions are not available for this field location",
    );
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await close();
    expect(form.getValues("values")).toEqual(values);
    await userEvent.click(
      screen.getByRole("button", { name: "Choose values" }),
    );
    await userEvent.type(
      screen.getByRole("combobox", { name: "Find or enter values" }),
      "manual,value{Enter}",
    );
    await userEvent.click(screen.getByRole("button", { name: "Use values" }));
    await close();
    expect(form.getValues("values")).toEqual([...values, "manual,value"]);
    expect(readQuery).not.toHaveBeenCalled();
  });

  it("chooses existing fields and typed values without Source/comma syntax, and sends the exact policy", async () => {
    show();
    await group();
    await chooseField("locale");
    expect(readQuery).toHaveBeenCalledWith(
      endpoints.dashboard.metrics,
      expect.objectContaining({
        params: expect.objectContaining({
          category: "custom_attribute",
          project_ids: "project-a",
          source: "traces",
          cursor_mode: true,
        }),
      }),
    );
    expect(
      screen.getByText("Group field: Span attribute: locale"),
    ).toBeVisible();
    // No empty filter is installed while a field/value choice is still a draft.
    expect(form.getValues("samplingPolicy.strata")).toEqual([]);
    await userEvent.click(
      screen.getByRole("button", { name: "Choose values" }),
    );
    await userEvent.click(
      screen.getByRole("combobox", { name: "Find or enter values" }),
    );
    for (const name of ["en,CA string", "42 number", "true boolean"])
      await userEvent.click(await screen.findByRole("option", { name }));
    await userEvent.click(screen.getByRole("button", { name: "Use values" }));
    await close();
    expect(readQuery).toHaveBeenCalledWith(
      endpoints.dashboard.filterValues,
      expect.objectContaining({
        params: expect.objectContaining({
          metric_name: "locale",
          property_id: "custom_attribute:locale",
          metric_type: "custom_attribute",
          project_ids: "project-a",
          source: "traces",
        }),
      }),
    );
    const expected = ["en,CA", 42, true].map((value) => ({
      source: "span",
      key: "locale",
      op: "in",
      values: [value],
    }));
    expect(filters(form.getValues("samplingPolicy"))).toEqual(expected);
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(filters(axios.post.mock.calls[0][1].sampling_policy)).toEqual(
      expected,
    );
  });

  it("pages fields, cancels browsing losslessly, and keeps a manual metadata fallback", async () => {
    show();
    await group();
    const before = structuredClone(form.getValues("samplingPolicy"));
    await userEvent.click(
      screen.getByRole("button", { name: "Choose group field" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Load more fields" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "region", exact: true }),
    );
    await close();
    expect(readQuery).toHaveBeenCalledWith(
      endpoints.dashboard.metrics,
      expect.objectContaining({
        params: expect.objectContaining({ cursor: "field-page-2" }),
      }),
    );
    expect(
      screen.getByText("Group field: Span attribute: region"),
    ).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Choose group field" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await close();
    expect(form.getValues("samplingPolicy")).toEqual(before);
    await userEvent.click(screen.getByText("Enter field manually"));
    await userEvent.click(
      screen.getByRole("combobox", { name: "Group field location" }),
    );
    await userEvent.click(screen.getByRole("option", { name: "Metadata" }));
    await userEvent.clear(
      screen.getByRole("textbox", { name: "Group field key" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Group field key" }),
      "account.region",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Choose values" }),
    );
    await userEvent.type(
      screen.getByRole("combobox", { name: "Find or enter values" }),
      "EU{Enter}",
    );
    await userEvent.click(screen.getByRole("button", { name: "Use values" }));
    await close();
    expect(
      filters(samplingPolicyForApi(form.getValues("samplingPolicy"))),
    ).toEqual([
      { source: "metadata", key: "account.region", op: "in", values: ["EU"] },
    ]);
    expect(form.getValues("samplingPolicy.group_by")).toEqual([]);
  });

  it("keeps lookup errors retryable and does not claim unavailable values are an empty vocabulary", async () => {
    readQuery.mockRejectedValueOnce(new Error("unavailable"));
    show();
    await group();
    await userEvent.click(
      screen.getByRole("button", { name: "Choose group field" }),
    );
    const retry = await screen.findByRole("button", { name: "Retry fields" });
    expect(screen.getByRole("dialog")).not.toHaveTextContent("No fields found");
    await userEvent.click(retry);
    await userEvent.click(
      await screen.findByRole("button", { name: "locale" }),
    );
    await close();
    readQuery.mockRejectedValueOnce(new Error("unavailable"));
    await userEvent.click(
      screen.getByRole("button", { name: "Choose values" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load values",
    );
    await userEvent.click(screen.getByRole("button", { name: "Retry values" }));
    const combo = screen.getByRole("combobox", {
      name: "Find or enter values",
    });
    await userEvent.click(combo);
    await userEvent.click(
      await screen.findByRole("option", { name: "en,CA string" }),
    );
    await userEvent.type(combo, "manual,value{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Use values" }));
    await close();
    expect(
      filters(form.getValues("samplingPolicy")).map(
        (predicate) => predicate.values[0],
      ),
    ).toEqual(["en,CA", "manual,value"]);
    expect(
      within(screen.getByRole("group", { name: "Group values" })).getByText(
        "manual,value",
      ),
    ).toBeVisible();
  });
});
