// SRC-S1b-HTTP-FE-02: Re-run with changes appends one new HTTP variant to an
// HTTP experiment. Authored RED against e0d9585e, where the door listed only
// prompt variants, so an HTTP experiment had no append at all.
// r2 (FE-08): an experiment with HTTP runs offers only its HTTP sources; the
// server refuses a prompt variant there, so it is never the default.
// r3 (FE-12, FE-13, FE-14): an unknown outcome is a warning recovered with
// Check again; a 403 refusal shows its reason; the editor states how many
// requests the new variant sends (one per experiment row).
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

const state = vi.hoisted(() => ({
  append: vi.fn(),
  get: vi.fn(),
  rows: vi.fn(),
}));
vi.mock("src/launch/launchSwitch", () => ({ isSurfaceShown: () => true }));
vi.mock("src/api/develop/experiment-append", async (original) => ({
  ...(await original()),
  appendVariantSupported: () => true,
  appendExperimentVariant: (...args) => state.append(...args),
}));
// The experiment detail read goes to state.get, its rows read to state.rows.
vi.mock("src/utils/axios", () => ({
  default: {
    get: (url, ...rest) =>
      String(url).endsWith("/rows/")
        ? state.rows(url, ...rest)
        : state.get(url, ...rest),
  },
  endpoints: {
    develop: {
      experiment: {
        getExperimentDetails: (id) => `/model-hub/experiments/v2/${id}/`,
        experimentDetail: (id) => `/model-hub/experiments/v2/${id}/rows/`,
      },
    },
  },
}));
vi.mock("src/api/develop/prompt", () => ({
  usePromptExecutions: () => ({ data: { pages: [{ results: [] }] } }),
  usePromptVersions: () => ({ data: { pages: [{ results: [] }] } }),
}));
/* eslint-disable react/prop-types */
vi.mock("src/components/custom-model-dropdown/CustomModelDropdown", () => ({
  default: ({ value }) => <span>{value}</span>,
}));
/* eslint-enable react/prop-types */

import ClassicRerunAction from "../ClassicRerunAction";
import {
  appendHttpVariantBody,
  httpVariantDraftOf,
} from "src/api/develop/experiment-append";
import {
  HTTP_RESEND_COPY,
  httpVariantCountLine,
} from "src/sections/common/Runs/httpResendCopy";

const COLUMN = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e52";
const httpSource = {
  id: "http-source",
  order: 0,
  target_type: "http",
  name: "Orders API",
  http: {
    url: "https://orders.example/v1/reply",
    method: "POST",
    headers: [{ name: "Authorization", secret_ref: "ORDERS_TOKEN" }],
    body_template: '{"q":"{{question}}"}',
    output_path: "$.reply",
    streaming: false,
    audio: "text",
    timeout_seconds: 15,
  },
  variable_mapping: { question: COLUMN, fixed: { kind: "constant", value: "" } },
};
const promptSource = {
  id: "prompt-config",
  name: "Prompt variant",
  model: "model-a",
  prompt_id: "prompt-a",
  prompt_version: "version-a",
  prompt_version_name: "v1",
};
const detailRead = (configs = {}) => ({
  data: {
    status: true,
    result: {
      status: "Completed",
      experiment_type: "llm",
      prompt_configs: [],
      agent_configs: [],
      http_configs: [httpSource],
      runs: [],
      runs_total: 0,
      runs_has_more: false,
      runs_next: null,
      ...configs,
    },
  },
});
// The experiment's rows read: its total is how many rows it holds.
const rowsRead = (total) => ({
  data: { status: true, result: { metadata: { total_rows: total } } },
});
const UNCOUNTED =
  "The new variant sends one request to its endpoint for each row of this experiment.";
const FORBIDDEN = {
  status: false,
  detail: "Workspace write permission is required.",
  statusCode: 403,
};

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ClassicRerunAction experimentId="experiment-a" />
    </QueryClientProvider>,
  );
}

async function openDialog() {
  mount();
  fireEvent.click(
    await screen.findByRole("button", { name: "Re-run with changes" }),
  );
  return screen.getByRole("dialog");
}

beforeEach(() => {
  state.append.mockReset().mockResolvedValue({});
  state.get.mockReset().mockResolvedValue(detailRead());
  state.rows.mockReset().mockResolvedValue(rowsRead(8000));
});
afterEach(() => cleanup());

describe("Re-run with changes for an HTTP experiment", () => {
  it("offers the HTTP source and its settings, and needs a change", async () => {
    const dialog = await openDialog();
    expect(within(dialog).getByLabelText("URL").value).toBe(httpSource.http.url);
    expect(within(dialog).getByLabelText("Body template").value).toBe(
      httpSource.http.body_template,
    );
    expect(within(dialog).getByLabelText("Output path").value).toBe("$.reply");
    expect(
      within(dialog).getByText(
        "Headers and variable mapping are copied from the original variant.",
      ),
    ).toBeTruthy();
    // The same endpoint may already have processed these rows.
    expect(
      within(dialog).getByText(/the endpoint may already have processed those requests/),
    ).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Run new variant" }).disabled).toBe(true);
  });

  it("appends one new HTTP variant with the original headers, timeout and mapping", async () => {
    const before = JSON.stringify(httpSource);
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("Output path"), {
      target: { value: "$.answer" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Run new variant" }));
    await waitFor(() => expect(state.append).toHaveBeenCalledOnce());
    expect(state.append).toHaveBeenCalledWith("experiment-a", {
      append_only: true,
      prompt_config: [{
        target_type: "http",
        name: "Orders API variant",
        http: { ...httpSource.http, output_path: "$.answer" },
        variable_mapping: httpSource.variable_mapping,
      }],
    }, { idempotencyKey: expect.stringMatching(/^fa-http-/) });
    const [, body] = state.append.mock.calls[0];
    expect(body.prompt_config[0]).not.toHaveProperty("id");
    expect(body.prompt_config[0]).not.toHaveProperty("prompt_id");
    expect(JSON.stringify(httpSource)).toBe(before);
  });

  it("does not warn about the earlier requests for a different endpoint", async () => {
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("URL"), {
      target: { value: "https://orders-v2.example/v1/reply" },
    });
    expect(
      within(dialog).queryByText(/the endpoint may already have processed those requests/),
    ).toBeNull();
    expect(within(dialog).getByRole("button", { name: "Run new variant" }).disabled).toBe(false);
  });

  it("SRC-S1b-HTTP-FE-12 warns of an unknown outcome; Check again repeats the identical submission and key", async () => {
    state.append.mockRejectedValueOnce({ statusCode: 503, detail: "Admission unconfirmed" });
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("Output path"), {
      target: { value: "$.answer" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Run new variant" }));
    const again = await within(dialog).findByRole("button", { name: "Check again" });
    // The same words and recovery as the Data tab: it may have started.
    const help = within(dialog).getByText(HTTP_RESEND_COPY.checkAgainHelp);
    expect(help.closest(".MuiAlert-root").className).toContain("MuiAlert-standardWarning");
    expect(within(dialog).queryByText(/Could not start the new variant/)).toBeNull();
    expect(within(dialog).queryByRole("button", { name: "Retry submission" })).toBeNull();
    expect(within(dialog).getByLabelText("Output path").disabled).toBe(true);
    fireEvent.click(again);
    await waitFor(() => expect(state.append).toHaveBeenCalledTimes(2));
    expect(state.append.mock.calls[1]).toEqual(state.append.mock.calls[0]);
    expect(state.append.mock.calls[0][2].idempotencyKey).toMatch(/^fa-http-/);
  });

  it("SRC-S1b-HTTP-FE-13 says why a 403 refused the variant (its reason is only in detail)", async () => {
    state.append.mockRejectedValueOnce(FORBIDDEN);
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("Output path"), {
      target: { value: "$.answer" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Run new variant" }));
    const reason = await within(dialog).findByText("Workspace write permission is required.");
    expect(reason.closest(".MuiAlert-root").className).toContain("MuiAlert-standardError");
    expect(within(dialog).queryByText(/Could not start the new variant/)).toBeNull();
    // A definite refusal: nothing to check, the draft can change again.
    expect(within(dialog).queryByRole("button", { name: "Check again" })).toBeNull();
    await waitFor(() =>
      expect(within(dialog).getByLabelText("Output path").disabled).toBe(false),
    );
  });

  it("SRC-S1b-HTTP-FE-14 says how many requests the new variant sends, one for each experiment row", async () => {
    const dialog = await openDialog();
    expect(
      await within(dialog).findByText(
        "The new variant sends 8,000 requests to its endpoint, one for each of the experiment's 8,000 rows.",
      ),
    ).toBeTruthy();
    expect(state.rows).toHaveBeenCalledWith("/model-hub/experiments/v2/experiment-a/rows/", {
      params: { current_page_index: 0, page_size: 1 },
    });
    expect(within(dialog).queryByText(UNCOUNTED)).toBeNull();
  });

  it("SRC-S1b-HTTP-FE-14 still says each row gets a request when the rows cannot be counted", async () => {
    state.rows.mockRejectedValue({ message: "Network Error" });
    const dialog = await openDialog();
    await waitFor(() => expect(state.rows).toHaveBeenCalled());
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
    expect(within(dialog).getByText(UNCOUNTED)).toBeTruthy();
    expect(within(dialog).queryByText(/requests to its endpoint, one for each/)).toBeNull();
  });

  it("SRC-S1b-HTTP-FE-14 states the variant's requests in plain words (pure)", () => {
    expect(httpVariantCountLine(1)).toBe(
      "The new variant sends 1 request to its endpoint, for the experiment's one row.",
    );
    expect(httpVariantCountLine(2500)).toBe(
      "The new variant sends 2,500 requests to its endpoint, one for each of the experiment's 2,500 rows.",
    );
    expect(httpVariantCountLine(undefined)).toBe(UNCOUNTED);
    expect(httpVariantCountLine(0)).toBe(UNCOUNTED);
    expect(httpVariantCountLine(1.5)).toBe(UNCOUNTED);
  });

  it("gives a new submission a new key", async () => {
    state.append.mockRejectedValueOnce({ statusCode: 400, detail: "Use an output path beginning with $." });
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("Output path"), {
      target: { value: "$.answer" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Run new variant" }));
    await waitFor(() => expect(state.append).toHaveBeenCalledOnce());
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Run new variant" }).disabled).toBe(false),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Run new variant" }));
    await waitFor(() => expect(state.append).toHaveBeenCalledTimes(2));
    expect(state.append.mock.calls[1][2].idempotencyKey).not.toBe(
      state.append.mock.calls[0][2].idempotencyKey,
    );
  });

  it("lets the draft change again after a definite refusal", async () => {
    state.append.mockRejectedValueOnce({ statusCode: 400, detail: "Use an output path beginning with $." });
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("Output path"), {
      target: { value: "$.answer" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Run new variant" }));
    await waitFor(() => expect(state.append).toHaveBeenCalledOnce());
    await waitFor(() =>
      expect(within(dialog).getByLabelText("Output path").disabled).toBe(false),
    );
    expect(within(dialog).queryByRole("button", { name: "Check again" })).toBeNull();
  });

  it("SRC-S1b-HTTP-FE-08 offers only the HTTP source in an experiment with HTTP and prompt runs", async () => {
    state.get.mockResolvedValue(detailRead({ prompt_configs: [promptSource] }));
    const dialog = await openDialog();
    // The HTTP editor opens by default; the prompt variant is not offered.
    expect(within(dialog).getByLabelText("URL").value).toBe(httpSource.http.url);
    expect(within(dialog).queryByText("Published version")).toBeNull();
    expect(within(dialog).queryByText("Prompt variant")).toBeNull();
    fireEvent.change(within(dialog).getByLabelText("Output path"), {
      target: { value: "$.answer" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Run new variant" }));
    await waitFor(() => expect(state.append).toHaveBeenCalledOnce());
    const [, body, options] = state.append.mock.calls[0];
    expect(body.prompt_config).toHaveLength(1);
    expect(body.prompt_config[0].target_type).toBe("http");
    expect(body.prompt_config[0]).not.toHaveProperty("prompt_id");
    expect(options.idempotencyKey).toMatch(/^fa-http-/);
  });

  it("SRC-S1b-HTTP-FE-08 keeps Re-run with changes visible, disabled with the reason, when the HTTP runs cannot be copied", async () => {
    state.get.mockResolvedValue(detailRead({
      prompt_configs: [promptSource],
      http_configs: [{ ...httpSource, id: undefined }],
    }));
    mount();
    const button = await screen.findByRole("button", { name: "Re-run with changes" });
    expect(button).toBeDisabled();
    expect(screen.getByText(HTTP_RESEND_COPY.rerunNothingToCopy)).toBeTruthy();
  });

  it("lists the prompt runs of a mixed experiment, disabled with the reason", async () => {
    state.get.mockResolvedValue(detailRead({ prompt_configs: [promptSource] }));
    const dialog = await openDialog();
    // The first select in the dialog is Original variant.
    fireEvent.mouseDown(within(dialog).getAllByRole("combobox")[0]);
    const option = await screen.findByRole("option", {
      name: `Prompt variant: ${HTTP_RESEND_COPY.rerunPromptBlocked}`,
    });
    expect(option.getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("option", { name: "Orders API" })).toBeTruthy();
  });

  it("SRC-S1b-HTTP-FE-08 Edit's reason sends an added HTTP run here and other changes to a new experiment", () => {
    expect(HTTP_RESEND_COPY.editHttpOnly).toBe(
      "Edit changes the settings of this experiment's HTTP runs only. To add an HTTP run, use Re-run with changes. To remove a run, or to add or change a prompt or agent run, create a new experiment.",
    );
  });

  it("keeps the prompt editor for an experiment without HTTP runs", async () => {
    state.get.mockResolvedValue(detailRead({ prompt_configs: [promptSource], http_configs: [] }));
    const dialog = await openDialog();
    expect(within(dialog).queryByLabelText("URL")).toBeNull();
    expect(within(dialog).getAllByText("Published version").length).toBeGreaterThan(0);
  });

  it("sends no body for a GET variant", () => {
    const body = appendHttpVariantBody(httpSource, {
      ...httpVariantDraftOf(httpSource),
      method: "GET",
    });
    expect(body.prompt_config[0].http).toMatchObject({ method: "GET", body_template: "" });
  });
});
