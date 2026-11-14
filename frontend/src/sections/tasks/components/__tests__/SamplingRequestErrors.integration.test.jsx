import React from "react";
import { useForm } from "react-hook-form";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { handleError } from "src/utils/queryErrorHandler";
import SamplingPolicyEditor from "../SamplingPolicyEditor";
import SamplingAuditPanel from "../SamplingAuditPanel";
import { defaultSamplingPolicy } from "../samplingPolicyForm";
import { useSamplingPreview } from "../samplingPreviewSummary";
import { samplingRequestError } from "../samplingRequestError";

vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

const normalize = axios.interceptors.response.handlers.find(
  (h) => h.rejected,
).rejected;
const closedPolicy = defaultSamplingPolicy();
function Editor() {
  const { control, getValues } = useForm({
    defaultValues: {
      samplingPolicy: closedPolicy,
      project: "00000000-0000-4000-8000-000000000901",
      evalsDetails: [],
      rowType: "spans",
      runType: "continuous",
      filters: [],
    },
  });
  const [summary] = useSamplingPreview(control);
  return (
    <>
      <SamplingPolicyEditor control={control} getValues={getValues} />
      <output aria-label="Sampling summary">
        {summary?.text || "No counts"}
      </output>
    </>
  );
}
const cases = [
  {
    name: "400 policy",
    status: 400,
    data: { result: "The sampling policy requires a closed quota window." },
    message: "The sampling policy requires a closed quota window.",
  },
  {
    name: "400 other",
    status: 400,
    data: { detail: "Choose a project." },
    message: "Choose a project.",
  },
  {
    name: "404",
    status: 404,
    data: { result: "The project was not found." },
    message: "The project was not found.",
  },
  {
    name: "409",
    status: 409,
    data: { result: "The cohort changed. Please retry." },
    message: "The cohort changed. Please retry.",
  },
  {
    name: "422 policy",
    status: 422,
    data: { message: "The sampling policy window must be closed." },
    message: "The sampling policy window must be closed.",
  },
  {
    name: "422 other",
    status: 422,
    data: { result: "Choose a project." },
    message: "Choose a project.",
  },
  {
    name: "429 wait",
    status: 429,
    headers: { "retry-after": "14" },
    wait: "Try again in 14 seconds.",
  },
  {
    name: "429 no wait",
    status: 429,
    wait: "Please wait before trying again.",
  },
  {
    name: "429 invalid wait",
    status: 429,
    headers: { "retry-after": "<script>14</script>" },
    wait: "Please wait before trying again.",
  },
  { name: "500", status: 500 },
  ...[502, 503, 504].map((status) => ({
    name: String(status),
    status,
    unavailable: true,
  })),
  {
    name: "timeout",
    code: "ECONNABORTED",
    messageRaw: "timeout of 1000ms exceeded",
    unavailable: true,
  },
  {
    name: "network",
    code: "ERR_NETWORK",
    messageRaw: "Network Error",
    unavailable: true,
  },
  {
    name: "connection",
    code: "ECONNREFUSED",
    messageRaw: "connection refused",
    unavailable: true,
  },
  { name: "no response", request: {}, unavailable: true },
  { name: "unknown object" },
  { name: "unknown body", status: 500, data: "<html>Internal error</html>" },
  {
    name: "404 misleading policy advice",
    status: 404,
    data: { result: "Check the policy and close quota windows." },
  },
  {
    name: "409 misleading policy advice",
    status: 409,
    data: { result: "Check the policy and close quota windows." },
  },
];
const failure = (testCase) =>
  normalize({
    config: { url: "/test-sampling-request", method: "post" },
    code: testCase.code,
    message: testCase.messageRaw,
    request: testCase.request,
    ...(testCase.status && {
      response: {
        status: testCase.status,
        data: testCase.data || {
          result: "Check the policy and close quota windows.",
        },
        headers: testCase.headers || {},
      },
    }),
  });

afterEach(() => {
  vi.restoreAllMocks();
});

it("clears stale counts, planner and shared summary on a 503 without changing the policy, then retries", async () => {
  const response = {
    data: {
      result: {
        strata: [
          {
            stratum: '["default",[]]',
            eligible_units: 2,
            selected_units: 1,
            expected_units: 1,
            selected_rows: 4,
          },
        ],
        evaluator_count: 1,
        population_note: "Closed quota",
        cost_note: "Pricing unavailable",
      },
    },
  };
  const request = vi
    .spyOn(axios, "post")
    .mockResolvedValueOnce(response)
    .mockImplementationOnce(() => failure({ status: 503 }))
    .mockResolvedValueOnce(response);
  render(<Editor />);
  const preview = screen.getByRole("button", {
    name: "Preview sample",
  });
  await userEvent.click(preview);
  expect(await screen.findByLabelText("Sampling summary")).toHaveTextContent(
    "Preview: 1 of 2 sessions; 4 spans × 1 evaluator = 4 runs",
  );
  expect(screen.getByLabelText("Sampling preview summary")).toHaveTextContent(
    "1 conversations · 4 spans evaluated",
  );
  await userEvent.click(preview);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Sampling preview is temporarily unavailable. Please try again in a moment.",
  );
  expect(screen.getByLabelText("Sampling summary")).toHaveTextContent(
    "No counts",
  );
  expect(
    screen.queryByLabelText("Sampling preview summary"),
  ).not.toBeInTheDocument();
  await userEvent.click(preview);
  await waitFor(() =>
    expect(screen.getByLabelText("Sampling summary")).toHaveTextContent(
      "Preview: 1 of 2 sessions",
    ),
  );
  expect(request.mock.calls.map((call) => call[1].sampling_policy)).toEqual(
    Array(3).fill(request.mock.calls[0][1].sampling_policy),
  );
});

it.each([null, undefined, "unknown", {}, { response: { data: {} } }])(
  "handles unknown raw error shapes without prescribing policy edits: %j",
  (error) => {
    for (const surface of ["preview", "audit"])
      expect(samplingRequestError(error, surface, "Please retry.")).toBe(
        "Please retry.",
      );
  },
);

describe.each(["preview", "audit"])("real normalized %s errors", (surface) => {
  it.each(cases)(
    "$name gives honest recovery guidance and keeps retry",
    async (testCase) => {
      const method = surface === "preview" ? "post" : "get";
      const request = vi
        .spyOn(axios, method)
        .mockImplementation(() => failure(testCase));
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
        queryCache: new QueryCache({ onError: handleError }),
      });
      const view = render(
        <QueryClientProvider client={client}>
          {surface === "preview" ? (
            <Editor />
          ) : (
            <SamplingAuditPanel taskId="task-errors" />
          )}
        </QueryClientProvider>,
      );
      if (surface === "preview")
        await userEvent.click(
          screen.getByRole("button", { name: "Preview sample" }),
        );
      const fallback =
        surface === "preview"
          ? "Sampling preview could not be loaded. Please try again."
          : "The sampling audit could not be loaded.";
      const expected = testCase.wait
        ? `Too many ${surface} requests. ${testCase.wait}`
        : testCase.unavailable
          ? `Sampling ${surface} is temporarily unavailable. Please try again in a moment.`
          : testCase.message || fallback;
      expect(await screen.findByRole("alert")).toHaveTextContent(expected);
      if (
        !testCase.name.includes("policy") ||
        testCase.status === 404 ||
        testCase.status === 409
      )
        expect(screen.getByRole("alert")).not.toHaveTextContent(
          /check.*policy|close quota windows/i,
        );
      const retry = screen.getByRole("button", {
        name: surface === "preview" ? "Preview sample" : "Retry",
      });
      expect(retry).toBeEnabled();
      await userEvent.click(retry);
      await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
      if (surface === "preview")
        expect(request.mock.calls[1][1].sampling_policy).toEqual(
          request.mock.calls[0][1].sampling_policy,
        );
      view.unmount();
      client.clear();
    },
  );
});
