/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { handleError } from "src/utils/queryErrorHandler";
import CustomEvalsPlayground from "../CustomEvalsPlayground";

vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { post: vi.fn() },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/PlaygroundInput/PlaygroundInput", () => ({
  default: ({ fieldTitle, onChange }) => (
    <input
      aria-label={fieldTitle}
      onChange={(e) => onChange({ type: "text", value: e.target.value })}
    />
  ),
}));

const LIMIT =
  "Evaluation rate limit reached. Wait for the usage window to reset before retrying.";

// The app's axios interceptor rejects with the flattened response body.
const refusal = (statusCode, code, message) => ({
  status: false,
  code,
  detail: message,
  message,
  error: message,
  result: message,
  statusCode,
});

const formData = {
  name: "synthetic_politeness",
  description: "",
  criteria: "Is {{text}} polite?",
  config: { model: "turing_large" },
  tags: [],
  choices: [],
  templateType: "Llm",
  output_type: "Pass/Fail",
  checkInternet: false,
  multiChoice: false,
};

// Wire the app-wide handler the way app.jsx does, so a test sees every toast.
function renderPlayground() {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <CustomEvalsPlayground open onClose={vi.fn()} formData={formData} />
    </QueryClientProvider>,
  );
}

async function evaluate() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("text"), "Thanks a lot");
  await user.click(screen.getByRole("button", { name: "Evaluate" }));
  await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Custom eval playground refused by the rate limit", () => {
  it("shows the server's 429 message once", async () => {
    axios.post.mockRejectedValueOnce(refusal(429, "rate_limited", LIMIT));
    renderPlayground();
    await evaluate();

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(LIMIT, {
        variant: "error",
      }),
    );
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
  });

  it.each([
    [400, "validation_error", "output_type: This field is required."],
    [402, "BUDGET_PAUSED", "Evaluation budget is paused."],
  ])(
    "leaves a %i to the app-wide handler: one toast, as before",
    async (statusCode, code, message) => {
      const error = refusal(statusCode, code, message);
      axios.post.mockRejectedValueOnce(error);
      renderPlayground();
      await evaluate();

      await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalledTimes(1));
      const appWide = vi.mocked(enqueueSnackbar).mock.calls[0];
      vi.mocked(enqueueSnackbar).mockClear();
      handleError(error);
      expect(enqueueSnackbar).toHaveBeenCalledWith(...appWide);
    },
  );
});
