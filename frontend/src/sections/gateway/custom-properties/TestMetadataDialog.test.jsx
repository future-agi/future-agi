import React, { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { act, fireEvent, render, screen, waitFor, within } from "src/utils/test-utils";

import TestMetadataDialog from "./TestMetadataDialog";

// Exercise the existing validation hook; only the HTTP boundary is mocked,
// as in the adjacent gateway mutation specs.
const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, post } };
});

const SCHEMAS = [
  { name: "tenant", property_type: "string", required: true, default_value: "not-a-sample" },
  { name: "priority", property_type: "enum", allowed_values: ["high", "low"] },
  { name: "attempts", property_type: "number" },
  { name: "preview", property_type: "boolean" },
];
const SAMPLE = { tenant: "<tenant>", priority: "high", attempts: 1, preview: true };
const ENDPOINT = "/agentcc/custom-properties/validate/";

function renderDialog(properties = SCHEMAS) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  function Harness() {
    const [open, setOpen] = useState(true);
    return (
      <QueryClientProvider client={client}>
        <button type="button" onClick={() => setOpen(true)}>Open sample</button>
        <TestMetadataDialog open={open} onClose={() => setOpen(false)} properties={properties} />
      </QueryClientProvider>
    );
  }
  return render(<Harness />);
}

const editor = () => screen.getByRole("textbox", { name: "Metadata JSON" });
const edit = (value) => fireEvent.change(editor(), { target: { value } });
const validate = (user) => user.click(screen.getByRole("button", { name: "Validate" }));

beforeEach(() => {
  post.mockReset();
  post.mockResolvedValue({ data: { result: { valid: true, errors: [] } } });
});

describe("TestMetadataDialog", () => {
  it("opens an editable schema sample and copies edits without any POST", async () => {
    const user = userEvent.setup();
    const copy = vi.spyOn(navigator.clipboard, "writeText");
    renderDialog();
    expect(JSON.parse(editor().value)).toEqual(SAMPLE);
    expect(editor().value).not.toContain("not-a-sample");
    expect(post).not.toHaveBeenCalled();

    const draft = '{"tenant":"my-tenant", "extra":{"source":"test"}}';
    edit(draft);
    await user.click(screen.getByRole("button", { name: "Copy sample" }));
    expect(copy).toHaveBeenCalledWith(draft);
    expect(editor()).toHaveValue(draft);
    expect(post).not.toHaveBeenCalled();
  });

  it("only explicit Validate sends the exact properties object and shows the returned pass", async () => {
    const user = userEvent.setup();
    renderDialog();
    const properties = { tenant: "test", priority: "low", attempts: 0, preview: false, extra: { nested: [1] } };
    edit(JSON.stringify(properties));
    expect(post).not.toHaveBeenCalled();
    await validate(user);
    expect(await screen.findByText(/Passed validation/)).toBeInTheDocument();
    expect(screen.getByText(/not enforced by gateway/i)).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(ENDPOINT, { properties });
  });

  it("renders every missing-required, enum and type error returned by the API", async () => {
    const user = userEvent.setup();
    const errors = [
      "Missing required property: tenant",
      "Property 'priority' must be one of: high, low",
      "Property 'attempts' must be a number",
      "Property 'preview' must be a boolean",
      "Property 'feature' must be a string",
    ];
    post.mockResolvedValue({ data: { result: { valid: false, errors } } });
    renderDialog();
    edit('{"priority":"urgent","attempts":"many","preview":"yes","feature":42}');
    await validate(user);
    expect(await screen.findByText("Failed validation")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Validation errors" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(errors.length);
    for (const error of errors) expect(within(list).getByText(error)).toBeInTheDocument();
    expect(screen.queryByText(/Passed validation/)).not.toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("uses the returned valid flag rather than inferring a pass from an empty error list", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({ data: { result: { valid: false, errors: [] } } });
    renderDialog();
    await validate(user);
    expect(await screen.findByText("Failed validation")).toBeInTheDocument();
    expect(screen.queryByText(/Passed validation/)).not.toBeInTheDocument();
  });

  it("does not turn a missing API result into a pass", async () => {
    const user = userEvent.setup();
    post.mockResolvedValueOnce({ data: {} });
    renderDialog();
    const draft = editor().value;
    await validate(user);
    expect(await screen.findByText(/Could not validate metadata/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Retry" })).toBeEnabled();
    expect(screen.queryByText(/Passed validation/)).not.toBeInTheDocument();
    expect(editor()).toHaveValue(draft);
    expect(post).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["malformed JSON", '{"tenant":', /Enter valid JSON/],
    ["empty input", "", /Enter valid JSON/],
    ["null", "null", /must be a JSON object/],
    ["an array", "[]", /must be a JSON object/],
    ["a string", '"tenant"', /must be a JSON object/],
    ["a number", "42", /must be a JSON object/],
    ["a boolean", "false", /must be a JSON object/],
  ])("rejects %s locally without posting", async (_kind, value, message) => {
    const user = userEvent.setup();
    renderDialog();
    edit(value);
    await validate(user);
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(editor()).toHaveAttribute("aria-invalid", "true");
    expect(editor()).toHaveValue(value);
    expect(post).not.toHaveBeenCalled();
  });

  it("retains the edited sample after network failure and only retries on a deliberate click", async () => {
    const user = userEvent.setup();
    post.mockRejectedValueOnce(new Error("Network Error"));
    renderDialog();
    const draft = '{"tenant":"keep this", "priority":"low"}';
    edit(draft);
    await validate(user);
    expect(await screen.findByText(/Could not validate metadata/)).toBeInTheDocument();
    expect(editor()).toHaveValue(draft);
    const retry = await screen.findByRole("button", { name: "Retry" });
    expect(retry).toBeEnabled();
    expect(post).toHaveBeenCalledTimes(1);
    await user.click(retry);
    expect(await screen.findByText(/Passed validation/)).toBeInTheDocument();
    expect(post.mock.calls).toEqual([
      [ENDPOINT, { properties: JSON.parse(draft) }],
      [ENDPOINT, { properties: JSON.parse(draft) }],
    ]);
    expect(editor()).toHaveValue(draft);
  });

  it("removes a previous result when the sample changes without automatically validating", async () => {
    const user = userEvent.setup();
    renderDialog();
    await validate(user);
    expect(await screen.findByText(/Passed validation/)).toBeInTheDocument();
    edit('{"tenant":12}');
    expect(screen.queryByText(/Passed validation/)).not.toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("disables edits and duplicate validation while a request is pending", async () => {
    const user = userEvent.setup();
    let complete;
    post.mockImplementationOnce(() => new Promise((resolve) => { complete = resolve; }));
    renderDialog();
    await validate(user);
    const pending = await screen.findByRole("button", { name: "Validating…" });
    expect(pending).toBeDisabled();
    expect(editor()).toBeDisabled();
    await user.click(pending);
    expect(post).toHaveBeenCalledTimes(1);
    await act(async () => complete({ data: { result: { valid: true, errors: [] } } }));
    expect(await screen.findByText(/Passed validation/)).toBeInTheDocument();
    await waitFor(() => expect(editor()).toBeEnabled());
  });

  it("discards the local draft and result on close and starts from the saved-schema sample", async () => {
    const user = userEvent.setup();
    renderDialog();
    edit('{"tenant":"local draft"}');
    await validate(user);
    expect(await screen.findByText(/Passed validation/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Open sample" }));
    expect(JSON.parse(editor().value)).toEqual(SAMPLE);
    expect(screen.queryByText(/Passed validation/)).not.toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("does not attach a late result from a closed dialog to a reopened draft", async () => {
    const user = userEvent.setup();
    let complete;
    post.mockImplementationOnce(() => new Promise((resolve) => { complete = resolve; }));
    renderDialog();
    await validate(user);
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Close" }));
    await user.click(screen.getByRole("button", { name: "Open sample" }));
    edit('{"tenant":"new draft"}');
    await act(async () => complete({ data: { result: { valid: true, errors: [] } } }));
    expect(screen.queryByText(/Passed validation/)).not.toBeInTheDocument();
    expect(editor()).toHaveValue('{"tenant":"new draft"}');
    expect(screen.getByRole("button", { name: "Validate" })).toBeEnabled();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("explains saved-schema scope, unknown properties, lack of ingestion/enforcement proof and draft reset", () => {
    renderDialog();
    expect(screen.getByText(/saved schemas only/i)).toBeInTheDocument();
    expect(screen.getByText(/Unknown properties are allowed by the current API/)).toBeInTheDocument();
    expect(screen.getByText(/does not prove request ingestion or gateway enforcement/)).toBeInTheDocument();
    expect(screen.getByText(/Closing discards this sample and its result/)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("starts with an editable empty object when there are no represented schemas", async () => {
    const user = userEvent.setup();
    renderDialog([]);
    expect(JSON.parse(editor().value)).toEqual({});
    expect(post).not.toHaveBeenCalled();
    edit('{"custom":"value"}');
    await validate(user);
    expect(await screen.findByText(/Passed validation/)).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(ENDPOINT, { properties: { custom: "value" } });
  });
});
