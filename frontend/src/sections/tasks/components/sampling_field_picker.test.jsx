import React from "react";
import PropTypes from "prop-types";
import { useController, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
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
const fieldBox = () => screen.getByRole("combobox", { name: "Group field" });
const valuesBox = () => screen.getByRole("combobox", { name: "Group values" });
// Option names are the value followed by its type caption ("locale string").
const option = (name) =>
  screen.findByRole("option", {
    name: (accessible) =>
      accessible === name || accessible.startsWith(`${name} `),
  });
const group = async () =>
  userEvent.click(
    screen.getByRole("button", { name: "Set a share per group" }),
  );
const chooseField = async (name) => {
  await userEvent.click(fieldBox());
  await userEvent.click(await option(name));
  await waitFor(() => expect(fieldBox()).toHaveValue(name));
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
    await userEvent.click(fieldBox());
    for (const [name, types] of [
      ["customer.regions", "array"],
      ["customer.profile", "map"],
      ["customer.payload", "json"],
      ["customer.mixed", "array, string"],
    ]) {
      const choice = await option(name);
      expect(choice).toHaveAccessibleName(`${name} ${types} · unavailable`);
      expect(choice).toHaveAttribute("aria-disabled", "true");
      expect(choice).toHaveAccessibleDescription(
        /Sampling matches only scalar string, number and boolean fields/,
      );
      choice.click();
      expect(form.getValues("samplingPolicy")).toEqual(before);
    }
    expect(await option("locale")).not.toHaveAttribute("aria-disabled", "true");
    // Usable rows come first, so the default highlight can be chosen.
    // Unavailable rows stay reachable by keyboard so their reason is read,
    // and Enter on one changes nothing.
    expect(screen.getAllByRole("option")[0]).toHaveAccessibleName(
      "locale string",
    );
    await userEvent.keyboard("{ArrowDown}");
    const active = document.getElementById(
      fieldBox().getAttribute("aria-activedescendant"),
    );
    expect(active).toHaveAccessibleName("customer.regions array · unavailable");
    expect(active).toHaveAccessibleDescription(
      /Sampling matches only scalar string, number and boolean fields/,
    );
    await userEvent.keyboard("{Enter}");
    expect(form.getValues("samplingPolicy")).toEqual(before);
    await userEvent.keyboard("{Escape}");
    await userEvent.tab();
    expect(fieldBox()).toHaveValue("");
    expect(form.getValues("samplingPolicy")).toEqual(before);
    expect(form.getValues("samplingPolicy.strata")).toEqual([]);
    expect(valuesBox()).toBeDisabled();
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
    await userEvent.click(valuesBox());
    await screen.findByRole("option", { name: "true boolean" });
    expect(
      screen.queryByRole("option", { name: /^CA / }),
    ).not.toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(5);
    for (const name of ["42 number", "false boolean"])
      await userEvent.click(screen.getByRole("option", { name }));
    const chosen = [42, false].map((value) => ({
      source: "span",
      key: "locale",
      op: "in",
      values: [value],
    }));
    expect(filters(form.getValues("samplingPolicy"))).toEqual(chosen);
    expect(screen.getByRole("button", { name: "42 (number)" })).toBeVisible();
    expect(
      screen.getByRole("button", { name: "false (boolean)" }),
    ).toBeVisible();
    // Text "42" matches the same rows as the number 42, so it would be a
    // second, overlapping group: it is listed but cannot be added.
    const alias = screen.getByRole("option", { name: /^42 string/ });
    expect(alias).toHaveAccessibleName("42 string · same group as 42");
    expect(alias).toHaveAttribute("aria-disabled", "true");
    alias.click();
    expect(filters(form.getValues("samplingPolicy"))).toEqual(chosen);
    await userEvent.type(valuesBox(), "42{Enter}");
    expect(screen.getByText(/Some values match the same group/)).toBeVisible();
    expect(valuesBox()).toHaveValue("42");
    expect(filters(form.getValues("samplingPolicy"))).toEqual(chosen);
    await userEvent.clear(valuesBox());
    expect(
      screen.queryByText(/Some values match the same group/),
    ).not.toBeInTheDocument();
    await userEvent.keyboard("{ArrowDown}");
    for (const name of ["true boolean", "0 number"])
      await userEvent.click(await screen.findByRole("option", { name }));
    await userEvent.keyboard("{Escape}");
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
      screen.queryByRole("combobox", { name: "Group values" }),
    ).not.toBeInTheDocument();
    expect(form.getValues("samplingPolicy")).toEqual(original);
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    expect(axios.post.mock.calls[0][1].sampling_policy).toEqual(original);
    expect(readQuery).not.toHaveBeenCalled();
  });

  it("keeps the value picker's JSON envelope safety and leaves values unchanged until a value is entered, independently of the read-only task policy", async () => {
    const values = [{ value: "saved JSON" }, null, { items: [1, true] }];
    show(<ValueHarness values={values} />);
    expect(
      screen.getByText(
        /Value suggestions are not available for this field location/,
      ),
    ).toBeVisible();
    for (const label of [
      '{"value":"saved JSON"}',
      "null",
      '{"items":[1,true]}',
    ])
      expect(screen.getByRole("button", { name: label })).toBeVisible();
    // Typed text that is never entered is not added, but leaving the box
    // keeps it in view and says so instead of dropping it unseen.
    await userEvent.type(valuesBox(), "draft");
    await userEvent.keyboard("{Escape}");
    await userEvent.tab();
    expect(valuesBox()).not.toHaveFocus();
    expect(valuesBox()).toHaveValue("draft");
    expect(
      screen.getByText(
        "“draft” is not added yet. Press Enter to add it as one value.",
      ),
    ).toBeVisible();
    expect(form.getValues("values")).toEqual(values);
    await userEvent.clear(valuesBox());
    await userEvent.type(valuesBox(), "manual,value{Enter}");
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
    expect(fieldBox()).toHaveValue("locale");
    expect(
      screen.getByRole("combobox", { name: "Group field location" }),
    ).toHaveTextContent("Span attribute");
    // No empty filter is installed while a field/value choice is still a draft.
    expect(form.getValues("samplingPolicy.strata")).toEqual([]);
    await userEvent.click(valuesBox());
    for (const name of ["en,CA string", "42 number", "true boolean"])
      await userEvent.click(await screen.findByRole("option", { name }));
    await userEvent.keyboard("{Escape}");
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
    await userEvent.click(fieldBox());
    // "Load more" runs in place: the list stays open and nothing is chosen.
    await userEvent.click(await option("Load more fields"));
    expect(screen.getByRole("listbox")).toBeVisible();
    await userEvent.click(await option("region"));
    expect(readQuery).toHaveBeenCalledWith(
      endpoints.dashboard.metrics,
      expect.objectContaining({
        params: expect.objectContaining({ cursor: "field-page-2" }),
      }),
    );
    expect(fieldBox()).toHaveValue("region");
    // Browsing and searching without choosing changes nothing, and emptying
    // the box is a search edit, not a removal of the chosen field.
    await userEvent.click(fieldBox());
    await userEvent.keyboard("{Escape}");
    await userEvent.clear(fieldBox());
    await userEvent.type(fieldBox(), "loc");
    await userEvent.tab();
    expect(fieldBox()).toHaveValue("region");
    expect(form.getValues("samplingPolicy")).toEqual(before);
    await userEvent.click(
      screen.getByRole("combobox", { name: "Group field location" }),
    );
    await userEvent.click(screen.getByRole("option", { name: "Metadata" }));
    await userEvent.clear(fieldBox());
    await userEvent.type(fieldBox(), "account.region");
    await userEvent.click(
      await screen.findByRole("option", {
        name: "Use “account.region” Metadata",
      }),
    );
    expect(fieldBox()).toHaveValue("account.region");
    await userEvent.type(valuesBox(), "EU{Enter}");
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
    await userEvent.click(fieldBox());
    const retry = await option("Retry fields");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not load span attributes",
    );
    expect(screen.getByRole("listbox")).not.toHaveTextContent(/No .*found/);
    expect(screen.queryByText(/No .*found/)).not.toBeInTheDocument();
    await userEvent.click(retry);
    await userEvent.click(await option("locale"));
    await waitFor(() => expect(fieldBox()).toHaveValue("locale"));
    readQuery.mockRejectedValueOnce(new Error("unavailable"));
    await userEvent.click(valuesBox());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load values",
    );
    expect(screen.getByRole("listbox")).not.toHaveTextContent(/No .*found/);
    expect(screen.queryByText(/No .*found/)).not.toBeInTheDocument();
    await userEvent.click(await option("Retry values"));
    await userEvent.click(
      await screen.findByRole("option", { name: "en,CA string" }),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await userEvent.type(valuesBox(), "manual,value{Enter}");
    expect(
      filters(form.getValues("samplingPolicy")).map(
        (predicate) => predicate.values[0],
      ),
    ).toEqual(["en,CA", "manual,value"]);
    expect(screen.getByRole("button", { name: "manual,value" })).toBeVisible();
  });

  it("keeps a key typed into an empty group field when focus leaves, as manual entry did", async () => {
    show();
    await group();
    await userEvent.click(
      screen.getByRole("combobox", { name: "Group field location" }),
    );
    await userEvent.click(screen.getByRole("option", { name: "Metadata" }));
    await userEvent.click(fieldBox());
    // With no suggestions the reason is an announced status, not a row.
    expect(
      await screen.findByText(
        "Suggestions list span attributes only. Type the exact metadata key.",
      ),
    ).toHaveAttribute("role", "status");
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    await userEvent.type(fieldBox(), "account.region");
    await userEvent.tab();
    expect(fieldBox()).not.toHaveFocus();
    expect(fieldBox()).toHaveValue("account.region");
    expect(
      screen.getByRole("combobox", { name: "Group field location" }),
    ).toHaveTextContent("Metadata");
    expect(form.getValues("samplingPolicy.strata")).toEqual([]);
    expect(valuesBox()).toBeEnabled();
    await userEvent.type(valuesBox(), "EU{Enter}");
    expect(
      filters(samplingPolicyForApi(form.getValues("samplingPolicy"))),
    ).toEqual([
      { source: "metadata", key: "account.region", op: "in", values: ["EU"] },
    ]);
  });

  it("keeps a Custom ID attribute typed and left with Tab, and names the default while it is empty", async () => {
    show(<Harness policy={{ ...loaded(), sampling_unit: "attribute" }} />);
    const box = screen.getByRole("combobox", { name: "Custom ID attribute" });
    expect(box).toHaveAttribute(
      "placeholder",
      "Project’s contact attribute (if set) · search or type a key",
    );
    expect(box).toHaveAccessibleDescription(
      /project’s contact attribute \(if one is set in project settings\).*Sampling matches only scalar/,
    );
    expect(form.getValues("samplingPolicy")).not.toHaveProperty(
      "unit_attribute",
    );
    await userEvent.type(box, "user.id");
    await userEvent.tab();
    expect(box).not.toHaveFocus();
    expect(box).toHaveValue("user.id");
    expect(form.getValues("samplingPolicy.unit_attribute")).toEqual({
      source: "span",
      key: "user.id",
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(axios.post.mock.calls[0][1].sampling_policy.unit_attribute).toEqual({
      source: "span",
      key: "user.id",
    });
  });

  it("says when a typed value is still being looked up, then when nothing loaded matches, and keeps it on blur", async () => {
    const standardRead = readQuery.getMockImplementation();
    readQuery.mockImplementation((url, options) =>
      url === endpoints.dashboard.filterValues && options.params.search
        ? Promise.resolve({
            data: {
              result: {
                values: [],
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
    await userEvent.click(valuesBox());
    await screen.findByRole("option", { name: "en,CA string" });
    await userEvent.type(valuesBox(), "Ocea");
    expect(screen.getByText("Searching values…")).toHaveAttribute(
      "role",
      "status",
    );
    expect(
      screen.getByRole("option", { name: "Use “Ocea” string" }),
    ).toBeVisible();
    // The rows loaded for the empty search do not contain "Ocea", so none
    // is offered as if it matched while the lookup runs.
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(
      screen.queryByRole("option", { name: "en,CA string" }),
    ).not.toBeInTheDocument();
    expect(
      await screen.findByText("No matching values in the loaded list."),
    ).toHaveAttribute("role", "status");
    expect(screen.queryByText("Searching values…")).not.toBeInTheDocument();
    expect(readQuery).toHaveBeenCalledWith(
      endpoints.dashboard.filterValues,
      expect.objectContaining({
        params: expect.objectContaining({ search: "Ocea" }),
      }),
    );
    expect(
      screen.getByText(
        "“Ocea” is not added yet. Press Enter to add it as one value.",
      ),
    ).toBeVisible();
    await userEvent.tab();
    expect(valuesBox()).toHaveValue("Ocea");
    expect(form.getValues("samplingPolicy.strata")).toEqual([]);
    await userEvent.type(valuesBox(), "{Enter}");
    expect(filters(form.getValues("samplingPolicy"))).toEqual([
      { source: "span", key: "locale", op: "in", values: ["Ocea"] },
    ]);
  });

  it("offers only loaded fields that contain the typed text until the typed search is looked up", async () => {
    show();
    await group();
    await userEvent.click(fieldBox());
    await option("locale");
    expect(await option("Load more fields")).toBeVisible();
    await userEvent.type(fieldBox(), "lo");
    expect(screen.getByText("Searching span attributes…")).toHaveAttribute(
      "role",
      "status",
    );
    expect(screen.getAllByRole("option").map((row) => row.textContent)).toEqual(
      ["locale string", "Use “lo” Span attribute"],
    );
    // "Load more" would page the previous search, so it waits as well.
    expect(
      screen.queryByRole("option", { name: "Load more fields" }),
    ).not.toBeInTheDocument();
    await userEvent.type(fieldBox(), "x");
    expect(screen.getAllByRole("option").map((row) => row.textContent)).toEqual(
      ["Use “lox” Span attribute"],
    );
    // Once looked up, the rows are exactly what the search returned (this
    // mock returns "locale" for every search).
    expect(await option("locale")).toBeVisible();
    expect(
      screen.queryByText("Searching span attributes…"),
    ).not.toBeInTheDocument();
    expect(form.getValues("samplingPolicy.strata")).toEqual([]);
  });

  it("offers only loaded values that contain the typed text until the typed search is looked up", async () => {
    show();
    await group();
    await chooseField("locale");
    await userEvent.click(valuesBox());
    await screen.findByRole("option", { name: "en,CA string" });
    await userEvent.type(valuesBox(), "en");
    expect(screen.getByText("Searching values…")).toBeVisible();
    expect(screen.getAllByRole("option").map((row) => row.textContent)).toEqual(
      ["en,CA string", "Use “en” string"],
    );
    expect(form.getValues("samplingPolicy.strata")).toEqual([]);
    // Looked up: the mock returns every value for any search, shown as is.
    expect(
      await screen.findByRole("option", { name: "42 number" }),
    ).toBeVisible();
    expect(screen.queryByText("Searching values…")).not.toBeInTheDocument();
  });
});
