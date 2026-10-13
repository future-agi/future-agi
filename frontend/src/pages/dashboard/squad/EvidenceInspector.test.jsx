import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import EvidenceInspector from "./EvidenceInspector";

describe("Omega Squad investigation evidence view", () => {
  it("separates remote observations, interpretation, and unverified isolation", () => {
    render(
      <EvidenceInspector
        runId="run-1"
        evidence={{
          available: true,
          evidence: {
            interpretation: "The remote agent completed the requested review.",
            limitations: ["Provider cleanup evidence was not included."],
          },
        }}
        proofPack={{
          available: true,
          proofPack: { answer: "Review completed.", artifacts: [] },
          verification: { ok: true, isolation: { status: "unknown" } },
        }}
        events={[{ event: "run.completed", id: "7" }]}
        observations={[
          {
            summary: "Remote check completed",
            occurredAt: "2026-10-12T00:00:00.000Z",
          },
        ]}
        streamStatus="live"
      />,
    );

    expect(screen.getByText("Investigation view")).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText("Remote check completed")).toBeInTheDocument();
    expect(
      screen.getByText("The remote agent completed the requested review."),
    ).toBeInTheDocument();
    expect(screen.getByText("Task isolation")).toBeInTheDocument();
    expect(screen.getByText("Stream live")).toBeInTheDocument();
  });

  it("only offers embedded artifact download after proof verification", () => {
    render(
      <EvidenceInspector
        runId="run-2"
        evidence={{ available: true }}
        proofPack={{
          available: true,
          proofPack: {
            artifacts: [
              {
                name: "notes.txt",
                bytes: 5,
                contentType: "text/plain",
                storage: {
                  embedded: true,
                  encoding: "base64",
                  content: "aGVsbG8=",
                },
              },
            ],
          },
          verification: { ok: true, isolation: { status: "verified" } },
        }}
        events={[]}
        observations={[]}
        streamStatus="live"
      />,
    );
    expect(
      screen.getByRole("button", { name: "Download" }),
    ).toBeInTheDocument();
  });
});
