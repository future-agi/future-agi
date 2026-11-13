import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RecordingAttachmentPlayer from "../RecordingAttachmentPlayer";
import { resolveAttachments } from "../client";
import { mixedManifest, occurrenceId, parent, projectId } from "./fixtures";

vi.mock("../client", () => ({ resolveAttachments: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

// Shapes emitted by tracer.services.attachments.view_enrichment.enrich_voice_detail.
describe("I04 recording_attachment states", () => {
  it.each([
    [{ state: "rejected" }, "Recording unavailable"],
    [
      { occurrence_id: occurrenceId, parent, state: "waiting" },
      "Waiting for recording completion",
    ],
    [
      { occurrence_id: occurrenceId, parent, state: "pending" },
      "Recording is not ready yet",
    ],
    [
      { occurrence_id: occurrenceId, parent, state: "pending_upload" },
      "Uploading recording",
    ],
    [
      { occurrence_id: occurrenceId, parent, state: "validating" },
      "Validating recording",
    ],
    [
      { occurrence_id: occurrenceId, parent, state: "failed" },
      "Recording could not be prepared",
    ],
    [
      { occurrence_id: occurrenceId, parent, state: "ready" },
      "Recording unavailable",
    ],
    [
      {
        occurrence_id: occurrenceId,
        parent,
        state: "ready",
        manifest: { ...mixedManifest, layout: "bogus" },
      },
      "Recording unavailable",
    ],
    [
      {
        occurrence_id: occurrenceId,
        parent,
        state: "ready",
        manifest: mixedManifest,
        url: "https://provider.example/r.wav",
      },
      "Recording unavailable",
    ],
  ])("%j shows a coarse state and never resolves", (attachment, text) => {
    render(
      <RecordingAttachmentPlayer
        projectId={projectId}
        attachment={attachment}
      />,
    );
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(resolveAttachments).not.toHaveBeenCalled();
  });
  it("plays a ready projection through a playable grant for its own occurrence", async () => {
    resolveAttachments.mockResolvedValue([
      {
        occurrence_id: occurrenceId,
        status: "ready",
        gateway_path: `/tracer/v1/attachment-media/${occurrenceId}/g`,
        expires_at: "2099-01-01T00:00:00+00:00",
      },
    ]);
    render(
      <RecordingAttachmentPlayer
        projectId={projectId}
        attachment={{
          occurrence_id: occurrenceId,
          parent,
          state: "ready",
          manifest: mixedManifest,
        }}
      />,
    );
    await screen.findByRole("button", { name: "Play recording" });
    expect(resolveAttachments.mock.calls[0][1]).toEqual([
      { occurrence_id: occurrenceId, parent, representation: "playable" },
    ]);
    expect(
      screen.queryByText(/Pipecat|LiveKit|Framework recording/),
    ).toBeNull();
  });
});
