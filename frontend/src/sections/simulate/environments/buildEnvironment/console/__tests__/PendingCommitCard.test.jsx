import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import axios from "src/utils/axios";

vi.mock("src/utils/axios", () => ({ default: { post: vi.fn() } }));

const { default: PendingCommitCard } = await import("../PendingCommitCard");

const COMMIT = {
  commitSha: "abc1234def5678",
  ref: "main",
  repository: "acme/agent",
};
const ENDPOINT = "/simulate/api/harness-environments/env-1/pending-commit/";

const renderCard = (commit = COMMIT) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      }
    >
      <PendingCommitCard envId="env-1" commit={commit} />
    </QueryClientProvider>,
  );

describe("PendingCommitCard", () => {
  it("names the commit and its branch", () => {
    renderCard();
    expect(
      screen.getByRole("region", { name: "New commit abc1234 on main" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Sync it as a new agent version?"),
    ).toBeInTheDocument();
  });

  it("names the default branch when the commit has no ref", () => {
    renderCard({ ...COMMIT, ref: null });
    expect(
      screen.getByText("New commit abc1234 on the default branch"),
    ).toBeInTheDocument();
  });

  it.each([
    ["Sync", "sync"],
    ["Skip", "skip"],
  ])("answers %s", async (label, answer) => {
    axios.post.mockResolvedValue({ data: { agent_version: null } });
    renderCard();

    fireEvent.click(screen.getByRole("button", { name: label }));

    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(ENDPOINT, { answer }),
    );
  });

  it("says why an answer failed", async () => {
    axios.post.mockImplementation(() =>
      Promise.reject(
        Object.assign(new Error("Request failed with status code 409"), {
          response: {
            data: {
              error: "environment_busy",
              message: "The environment is being built; sync once it finishes",
            },
          },
        }),
      ),
    );
    renderCard();

    fireEvent.click(screen.getByRole("button", { name: "Sync" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The environment is being built; sync once it finishes",
    );
  });
});
