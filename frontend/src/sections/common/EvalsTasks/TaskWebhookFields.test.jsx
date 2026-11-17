// @vitest-environment jsdom
import React from "react";
import PropTypes from "prop-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useForm } from "react-hook-form";
import TaskWebhookFields, { webhookPayload } from "./TaskWebhookFields";
import { NewTaskValidationSchema } from "./NewTaskDrawer/validation";
import { getDefaultTaskValues } from "./common";

const evalId = "30000000-0000-4000-8000-000000000001";
const url = "https://hooks.example.test/evaluations";
const rule = { eval_config_id: evalId, operator: "gt", value: 0.5 };
const base = {
  name: "Quality task", project: "project", runType: "continuous", rowType: "spans",
  samplingRate: 100, spansLimit: "", filters: [],
  startDate: "2026-11-01", endDate: "2026-11-02",
  evalsDetails: [{ id: evalId, name: "Quality" }],
};

afterEach(cleanup);

function Form({ initial = {}, submit }) {
  const { control, handleSubmit } = useForm({ defaultValues: { ...base, ...initial } });
  return <form onSubmit={handleSubmit((data) => submit(webhookPayload(data)))}>
    <TaskWebhookFields control={control} />
    <button type="submit">Save</button>
  </form>;
}

Form.propTypes = {
  initial: PropTypes.object,
  submit: PropTypes.func.isRequired,
};

describe("task webhook form", () => {
  it("collects a webhook destination and condition", async () => {
    const submit = vi.fn();
    render(<Form submit={submit} />);
    fireEvent.change(screen.getByLabelText("Webhook URL"), { target: { value: url } });
    fireEvent.change(screen.getByLabelText("Webhook evaluation"), { target: { value: evalId } });
    fireEvent.change(screen.getByLabelText("Webhook operator"), { target: { value: "lt" } });
    fireEvent.change(screen.getByLabelText("Webhook value"), { target: { value: "0.4" } });
    fireEvent.click(screen.getByText("Save"));
    await vi.waitFor(() => expect(submit).toHaveBeenCalledWith({
      webhook_url: url, webhook_condition: { eval_config_id: evalId, operator: "lt", value: 0.4 },
    }));
  });

  it("restores the saved rule and clears it when the URL is removed", async () => {
    const submit = vi.fn();
    render(<Form submit={submit} initial={{ webhook_url: url, webhook_condition: rule }} />);
    expect(screen.getByLabelText("Webhook URL").value).toBe(url);
    expect(screen.getByLabelText("Webhook value").value).toBe("0.5");
    fireEvent.change(screen.getByLabelText("Webhook URL"), { target: { value: "" } });
    fireEvent.click(screen.getByText("Save"));
    await vi.waitFor(() => expect(submit).toHaveBeenCalledWith({ webhook_url: "", webhook_condition: null }));
  });

  it("does not require a threshold for fail", () => {
    render(<Form submit={vi.fn()} initial={{ webhook_url: url, webhook_condition: { ...rule, operator: "fail" } }} />);
    expect(screen.queryByLabelText("Webhook value")).toBeNull();
    const parsed = NewTaskValidationSchema().parse({ ...base, webhook_url: url, webhook_condition: { ...rule, operator: "fail" } });
    expect(parsed.webhook_condition).toEqual({ ...rule, operator: "fail", value: null });
  });

  it("preserves webhook fields through schema and edit defaults", () => {
    const parsed = NewTaskValidationSchema().parse({ ...base, webhook_url: url, webhook_condition: rule });
    expect(parsed.webhook_url).toBe(url);
    expect(parsed.webhook_condition).toEqual(rule);
    const defaults = getDefaultTaskValues({ webhook_url: url, webhook_condition: rule, run_type: "continuous" });
    expect(defaults.webhook_url).toBe(url);
    expect(defaults.webhook_condition).toEqual(rule);
  });

  it("preserves numeric-looking equality strings when editing", () => {
    const saved = { eval_config_id: evalId, operator: "eq", value: "0" };
    const defaults = getDefaultTaskValues({ webhook_url: url, webhook_condition: saved, run_type: "continuous" });
    expect(webhookPayload(defaults).webhook_condition.value).toBe("0");
  });

  it.each([
    { webhook_url: "invalid", webhook_condition: rule },
    { webhook_url: url, webhook_condition: { ...rule, value: "" } },
    { webhook_url: url, webhook_condition: { ...rule, value: null } },
    { webhook_url: url, webhook_condition: { ...rule, value: "not a number" } },
    { webhook_url: url, webhook_condition: { ...rule, eval_config_id: "missing" } },
  ])("rejects incomplete or invalid webhook rules", (fields) => {
    expect(NewTaskValidationSchema().safeParse({ ...base, ...fields }).success).toBe(false);
  });
});
