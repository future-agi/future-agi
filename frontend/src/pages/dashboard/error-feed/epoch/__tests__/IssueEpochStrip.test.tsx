import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";

import {
  HELD_BACK_KEYS,
  SOURCE_RUN_ID,
  fixtureBrief,
  outsideAttemptFix,
} from "src/api/simulate-environments/__tests__/fixtures/codingAgent";
import { fixtureFixRun } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";
import type { AgentFixRunDetailApi } from "src/generated/api-contracts/api.schemas";

// The issue job's summary of its epoch (`summary`, no run named) or the epoch's own read.
type IssueEpoch = Partial<AgentFixRunDetailApi> & { summary?: boolean };

const caps = vi.hoisted(() => ({ codingAgent: true }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("src/hooks/useCapabilities")>();
  return {
    ...actual,
    useCapabilities: () => ({
      data: { coding_agent_attempts_enabled: caps.codingAgent },
      isLoading: false,
      isError: false,
    }),
    useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
  };
});

// The issue's epoch: the job's summary of it (no run named) or the epoch's own read.
const issue = vi.hoisted(() => ({ epoch: null as IssueEpoch | null }));
vi.mock("src/api/errorFeed/epochFromIssue", () => ({
  useIssueEpoch: () => ({
    status: {
      jobId: "job-1",
      phase: "launched",
      ending: null,
      epoch: issue.epoch,
    },
    known: true,
  }),
  useStopIssueEpoch: () => ({ mutate: vi.fn(), isPending: false }),
}));

const api = vi.hoisted(() => ({
  get: vi.fn<(url: string, config?: unknown) => Promise<{ data: unknown }>>(),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal<typeof import("src/utils/axios")>();
  api.get.mockImplementation((url) =>
    url === actual.endpoints.agentFixRuns.codingAgentBrief()
      ? Promise.resolve({ data: fixtureBrief() })
      : Promise.reject(new Error(`unexpected GET ${url}`)),
  );
  return { ...actual, default: { ...actual.default, get: api.get } };
});

const clipboard = vi.hoisted(() => ({
  copy: vi.fn(async (_text: unknown) => true),
}));
vi.mock("src/utils/copyToClipboard", () => ({
  copyToClipboard: clipboard.copy,
}));

const { default: IssueEpochStrip } = await import("../IssueEpochStrip");

const fullRead = () =>
  outsideAttemptFix(structuredClone(fixtureFixRun("fix-ready-archive")));

function renderStrip() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SnackbarProvider>
        <MemoryRouter>
          <IssueEpochStrip
            clusterId="cl-1"
            projectId="p-1"
            onRunAgain={vi.fn()}
          />
        </MemoryRouter>
      </SnackbarProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  caps.codingAgent = true;
  issue.epoch = fullRead();
  api.get.mockClear();
  clipboard.copy.mockClear();
});

describe("Copy for coding agent on the issue's epoch strip", () => {
  it("is not there while the setting is off", () => {
    caps.codingAgent = false;
    renderStrip();
    expect(screen.getByTestId("issue-epoch-strip")).toBeInTheDocument();
    expect(
      screen.queryByTestId("issue-copy-for-coding-agent"),
    ).not.toBeInTheDocument();
  });

  it("waits for the epoch's own read, which names its run", () => {
    issue.epoch = { ...fullRead(), source_run_id: undefined, summary: true };
    renderStrip();
    expect(
      screen.queryByTestId("issue-copy-for-coding-agent"),
    ).not.toBeInTheDocument();
  });

  it("copies the epoch's run's brief and shows Copied", async () => {
    const user = userEvent.setup();
    renderStrip();
    await user.click(screen.getByTestId("issue-copy-for-coding-agent"));
    await waitFor(() => expect(clipboard.copy).toHaveBeenCalledTimes(1));
    expect(api.get).toHaveBeenCalledWith(expect.any(String), {
      params: { source_run_id: SOURCE_RUN_ID },
    });
    const copied = String(clipboard.copy.mock.calls[0][0]);
    expect(copied.endsWith(fixtureBrief().instructions)).toBe(true);
    for (const key of HELD_BACK_KEYS) expect(copied).not.toContain(key);
    expect(
      await screen.findByTestId("issue-copy-for-coding-agent"),
    ).toHaveTextContent("Copied");
  });
});
