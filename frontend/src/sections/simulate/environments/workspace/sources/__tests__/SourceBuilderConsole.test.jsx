import React from "react";
import { MemoryRouter } from "react-router-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import SourceBuilderConsole from "../SourceBuilderConsole";

const mocks = vi.hoisted(() => ({
  review: vi.fn(),
  activeId: null,
  message: vi.fn(),
  upload: vi.fn(),
  create: vi.fn(),
  activate: vi.fn(),
}));
vi.mock("src/api/harness/harness", () => ({
  harnessIdempotencyKey: () => "request-13",
  sendHarnessConversationMessage: mocks.message,
}));
vi.mock("src/api/simulate-environments/designDocuments", () => ({
  useUploadDesignDocument: () => mocks.upload,
}));
vi.mock("src/api/simulate-environments/sourceImports", () => ({
  useEnvironmentSourceReview: () => ({ data: mocks.review() }),
  useCreateSourceReview: () => ({ mutateAsync: mocks.create }),
  useActiveEnvironmentSourceReview: () => ({
    reviewId: mocks.activeId,
    setReviewId: mocks.activate,
  }),
  useSourceCapabilities: () => ({
    isSuccess: true,
    data: {
      environment_version_id: "version",
      draft: { digest: "basis" },
      upload: mocks.uploadCapability,
    },
  }),
  sourceSummaryKey: (id) => ["summary", id],
}));
vi.mock("../SourceReviewPanel", () => ({ default: () => null }));
vi.mock("../../../buildEnvironment/console/BuilderConsole", () => ({
  default: ({ attachHold, canAttach, onSend, turns }) => (
    <>
      {attachHold && <p>Paperclip held: {attachHold}</p>}
      {canAttach && (
        <button
          onClick={() =>
            onSend("Check refunds", [
              new File(["policy"], "rules.pdf"),
              new File(["code"], "orders.csv"),
            ])
          }
        >
          Send files
        </button>
      )}
      {turns
        .flatMap((turn) => turn.steps || [{ id: turn.id, node: turn.text }])
        .map((step) => (
          <div key={step.id}>{step.node}</div>
        ))}
    </>
  ),
}));

describe("Builder chat source files", () => {
  it("restores the file names and proposed changes card from the attached review on reopening chat", () => {
    mocks.activeId = "review-7";
    mocks.review.mockReturnValue({
      scope: { id: "env" },
      state: "ready",
      preview: { sources: [{ filename: "orders.csv" }] },
    });
    try {
      render(
        <MemoryRouter>
          <QueryClientProvider client={new QueryClient()}>
            <SourceBuilderConsole
              envId="env"
              onSend={vi.fn()}
              turns={[
                {
                  id: "m1",
                  role: "user",
                  text: "Check refunds",
                  sourceReviewId: "review-7",
                },
              ]}
            />
          </QueryClientProvider>
        </MemoryRouter>,
      );
      expect(
        screen.getByText("Check refunds · Attached: orders.csv"),
      ).toBeVisible();
      expect(
        screen.getByRole("region", { name: "Attached files" }),
      ).toHaveTextContent("orders.csv");
      expect(
        screen.getByRole("region", { name: "Proposed changes" }),
      ).toHaveTextContent("Nothing changes until you add them.");
    } finally {
      mocks.activeId = null;
      mocks.review.mockReset();
    }
  });

  it("retries only failed uploads and opens the actual environment review before sending the message", async () => {
    mocks.upload
      .mockResolvedValueOnce({ id: "doc-a" })
      .mockRejectedValueOnce(new Error("Upload interrupted"))
      .mockResolvedValueOnce({ id: "doc-b" });
    mocks.create.mockResolvedValue({ id: "review-7" });
    const send = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <SourceBuilderConsole envId="env" onSend={send} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText("Send files"));
    expect(await screen.findByText("Upload interrupted")).toBeInTheDocument();
    expect(screen.getByText("Attached: rules.pdf, orders.csv")).toBeVisible();
    expect(mocks.create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Retry"));
    await waitFor(() =>
      expect(mocks.message).toHaveBeenCalledWith("env", {
        content: "Check refunds",
        kind: "user_message",
        source_review_id: "review-7",
        client_request_id: "request-13",
      }),
    );
    expect(send).not.toHaveBeenCalled();
    expect(mocks.upload.mock.calls.map(([f]) => f.name)).toEqual([
      "rules.pdf",
      "orders.csv",
      "orders.csv",
    ]);
    expect(mocks.create).toHaveBeenCalledWith({
      scope: {
        kind: "environment",
        id: "env",
        environment_version_id: "version",
        expected_draft: { digest: "basis" },
      },
      document_ids: ["doc-a", "doc-b"],
      document_purposes: { "doc-a": "reference" },
      idempotency_key: expect.any(String),
    });
    expect(mocks.activate).toHaveBeenCalledWith("review-7");
  });

  it("upload off: the paperclip is disabled with the reason", () => {
    mocks.uploadCapability = { enabled: false, reason: "R" };
    try {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter>
            <SourceBuilderConsole envId="env" onSend={vi.fn()} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      expect(screen.getByText("Paperclip held: R")).toBeVisible();
      expect(screen.queryByText("Send files")).toBeNull();
    } finally {
      mocks.uploadCapability = undefined;
    }
  });
});
