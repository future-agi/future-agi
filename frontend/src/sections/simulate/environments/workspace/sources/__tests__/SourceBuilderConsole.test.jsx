import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import SourceBuilderConsole from "../SourceBuilderConsole";

const mocks = vi.hoisted(() => ({
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
  useCreateSourceReview: () => ({ mutateAsync: mocks.create }),
  useActiveEnvironmentSourceReview: () => ({
    reviewId: null,
    setReviewId: mocks.activate,
  }),
  useSourceCapabilities: () => ({
    isSuccess: true,
    data: { environment_version_id: "version", draft: { digest: "basis" } },
  }),
  sourceSummaryKey: (id) => ["summary", id],
}));
vi.mock("../SourceReviewPanel", () => ({ default: () => null }));
vi.mock("../../../buildEnvironment/console/BuilderConsole", () => ({
  default: ({ canAttach, onSend, turns }) => (
    <>
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
        .flatMap((turn) => turn.steps)
        .map((step) => (
          <div key={step.id}>{step.node}</div>
        ))}
    </>
  ),
}));

describe("Builder chat source files", () => {
  it("retries only failed uploads and opens the actual environment review before sending the message", async () => {
    mocks.upload
      .mockResolvedValueOnce({ id: "doc-a" })
      .mockRejectedValueOnce(new Error("Upload interrupted"))
      .mockResolvedValueOnce({ id: "doc-b" });
    mocks.create.mockResolvedValue({ id: "review-7" });
    const send = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SourceBuilderConsole envId="env" onSend={send} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText("Send files"));
    expect(await screen.findByText("Upload interrupted")).toBeInTheDocument();
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
      document_purposes: {"doc-a": "reference"},
      idempotency_key: expect.any(String),
    });
    expect(mocks.activate).toHaveBeenCalledWith("review-7");
  });
});
