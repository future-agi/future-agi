import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";

import {
  fixtureLaunchOptions,
  resetFixRunFixtures,
} from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

vi.mock("src/utils/axios", async (importOriginal) => {
  const { withFakeFixRunsApi } = await import(
    "src/api/simulate-environments/__tests__/fixtures/fixRunsApi"
  );
  return withFakeFixRunsApi(await importOriginal());
});

// Fix my agent is on, so the environment's fix list answers.
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
}));

const navigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => navigate,
}));

// The real launch-options hook over the fake API unless a test pins it.
const hooks = vi.hoisted(() => ({ options: null }));
vi.mock("src/api/simulate-environments/fixRuns", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useFixLaunchOptions: (id, opts) =>
      hooks.options ?? actual.useFixLaunchOptions(id, opts),
  };
});

const axios = (await import("src/utils/axios")).default;
const { default: LaunchFixDialog } = await import("../LaunchFixDialog");

const REFUSED = "github_private_unavailable";
const SENTENCE =
  "Private GitHub repositories are not available on Future AGI cloud. Upload the code to keep using this environment.";

const onClose = vi.fn();
function renderDialog() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SnackbarProvider>
        <MemoryRouter>
          <LaunchFixDialog
            open
            envId="env-1"
            sourceRunId="te-run-6"
            onClose={onClose}
          />
        </MemoryRouter>
      </SnackbarProvider>
    </QueryClientProvider>,
  );
}

const startButton = () =>
  screen.getByRole("button", { name: "Start improvement" });

beforeEach(() => {
  resetFixRunFixtures();
  hooks.options = null;
  navigate.mockReset();
  onClose.mockReset();
  axios.post.mockClear();
});

describe("LaunchFixDialog for an agent whose private GitHub repository can't be read here", () => {
  it("says why before Start, keeps Start off and links nowhere else", async () => {
    hooks.options = {
      options: {
        ...fixtureLaunchOptions("te-run-6"),
        blocked: { code: REFUSED, message: SENTENCE },
      },
      isLoading: false,
      error: null,
    };
    renderDialog();
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    expect(startButton()).toBeDisabled();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByText(/contact support/i)).toBeNull();
  });

  it("shows the sentence when the start itself is refused, and opens nothing", async () => {
    axios.post.mockImplementationOnce(() =>
      Promise.reject({
        statusCode: 422,
        error: REFUSED,
        message: SENTENCE,
        retryable: false,
      }),
    );
    const user = userEvent.setup();
    renderDialog();
    await user.click(
      await screen.findByRole("button", { name: "Start improvement" }),
    );
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    expect(screen.queryByText("Could not start the improvement")).toBeNull();
    expect(axios.post.mock.calls.at(-1)[0]).toBe(
      "/simulate/api/agent-fix-runs/",
    );
    expect(navigate).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
