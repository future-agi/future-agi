import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

import {
  callLimitConfig,
  canStartEndToEndRun,
  parseProviderDynamicVariables,
} from "./HarnessCreate";

const createHarnessJob = vi.fn();
const preflightHarnessJob = vi.fn();

vi.mock("src/api/harness/harness", () => ({
  createHarnessJob: (...args) => createHarnessJob(...args),
  preflightHarnessJob: (...args) => preflightHarnessJob(...args),
  listHarnessJobs: vi.fn(async () => ({ jobs: [] })),
  storeHarnessSecretValues: vi.fn(async () => ({ secret_refs: {} })),
  uploadHarnessSecretFile: vi.fn(),
  uploadHarnessSource: vi.fn(),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

const { default: HarnessCreate } = await import("./HarnessCreate");

describe("parseProviderDynamicVariables", () => {
  it("accepts an object of safe scalar values", () => {
    expect(
      parseProviderDynamicVariables(
        '{"customer_name":"Jane","balance":124,"verified":true}',
      ),
    ).toEqual({ customer_name: "Jane", balance: 124, verified: true });
  });

  it("rejects invalid JSON and nested values before submission", () => {
    expect(() => parseProviderDynamicVariables("not-json")).toThrow(
      "valid JSON",
    );
    expect(() =>
      parseProviderDynamicVariables('{"customer":{"name":"Jane"}}'),
    ).toThrow("string, number, or boolean");
  });
});

describe("canStartEndToEndRun", () => {
  it("allows a sourced run without requiring a prior manual preflight", () => {
    expect(
      canStartEndToEndRun({
        hasSource: true,
        submitting: false,
        checking: false,
        uploadingSecretFile: false,
      }),
    ).toBe(true);
  });

  it.each([
    ["has no source", { hasSource: false }],
    ["is already submitting", { submitting: true }],
    ["is checking manually", { checking: true }],
    ["is uploading a credential", { uploadingSecretFile: true }],
  ])("blocks while the form %s", (_label, override) => {
    expect(
      canStartEndToEndRun({
        hasSource: true,
        submitting: false,
        checking: false,
        uploadingSecretFile: false,
        ...override,
      }),
    ).toBe(false);
  });
});

describe("callLimitConfig", () => {
  it("omits the call limit unless a positive number was typed", () => {
    expect(callLimitConfig("")).toEqual({});
    expect(callLimitConfig("   ")).toEqual({});
    expect(callLimitConfig("0")).toEqual({});
    expect(callLimitConfig(undefined)).toEqual({});
    expect(callLimitConfig("600")).toEqual({ voice_call_timeout_seconds: 600 });
    expect(callLimitConfig(900)).toEqual({ voice_call_timeout_seconds: 900 });
  });
});

// The helper above is only half the wiring. These drive the rendered page the way a person does,
// because the defect this guards against is the field being present and reaching nothing.
describe("the call limit field on the page", () => {
  const openForm = async () => {
    render(
      <HelmetProvider>
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <HarnessCreate />
        </QueryClientProvider>
      </HelmetProvider>,
    );
    await userEvent.click(screen.getByRole("radio", { name: /GitHub repository/i }));
    await userEvent.type(
      screen.getByLabelText(/Repository URL/i),
      "https://github.com/future-agi/example-agent",
    );
  };

  const submittedConfig = () => createHarnessJob.mock.calls[0][0].agent.config;

  beforeEach(() => {
    createHarnessJob.mockReset();
    preflightHarnessJob.mockReset();
    createHarnessJob.mockResolvedValue({ job: { job_id: "job-1" } });
    preflightHarnessJob.mockResolvedValue({ ready_to_submit: true });
  });

  it("sends what was typed into the field", async () => {
    await openForm();
    await userEvent.type(screen.getByLabelText(/Call limit \(seconds\)/i), "600");
    await userEvent.click(screen.getByRole("button", { name: /Run end to end/i }));

    await vi.waitFor(() => expect(createHarnessJob).toHaveBeenCalled());
    expect(submittedConfig().voice_call_timeout_seconds).toBe(600);
  });

  it("advertises the bounds a call limit is meant to stay inside", async () => {
    await openForm();
    const field = screen.getByLabelText(/Call limit \(seconds\)/i);

    expect(field).toHaveAttribute("min", "30");
    expect(field).toHaveAttribute("max", "3600");
  });

  it("sends no limit at all when the field is left alone", async () => {
    await openForm();
    await userEvent.click(screen.getByRole("button", { name: /Run end to end/i }));

    await vi.waitFor(() => expect(createHarnessJob).toHaveBeenCalled());
    expect(submittedConfig()).not.toHaveProperty("voice_call_timeout_seconds");
  });
});
