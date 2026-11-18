import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  fireEvent,
  screen,
  waitFor,
} from "src/utils/test-utils";

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  storeHarnessSecretValues: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
  uploadHarnessSource: vi.fn(),
}));

const { preflightHarnessJob, storeHarnessSecretValues } = await import(
  "src/api/harness/harness"
);
const { default: RepoForm } = await import("../forms/RepoForm");

const render = (ui) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return baseRender(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
};
const type = (name, value) =>
  fireEvent.change(screen.getByRole("textbox", { name }), {
    target: { value },
  });
const runPreflight = () =>
  fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
const footerHint = (text) => screen.getByText(text);

beforeEach(() => {
  preflightHarnessJob.mockReset();
  preflightHarnessJob.mockResolvedValue({ ready_to_submit: false });
  storeHarnessSecretValues.mockReset();
  storeHarnessSecretValues.mockResolvedValue({ secret_refs: { KEY: "ref-1" } });
});

describe("GitHub", () => {
  it("reads a pasted repository link", async () => {
    render(<RepoForm />);
    expect(footerHint("Paste the repository link")).toBeInTheDocument();
    type("Repository link", "https://github.com/acme/agent");
    runPreflight();
    await waitFor(() => expect(preflightHarnessJob).toHaveBeenCalled());
    expect(JSON.stringify(preflightHarnessJob.mock.calls[0][0])).toContain(
      "acme/agent",
    );
  });

  it("asks a private repository for its GitHub App installation", () => {
    render(<RepoForm />);
    fireEvent.click(screen.getByRole("button", { name: "Private" }));
    expect(
      screen.getByRole("textbox", { name: "GitHub App installation ID" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("radio", { name: "Use an access token" }),
    ).toBeEnabled();
  });
});
