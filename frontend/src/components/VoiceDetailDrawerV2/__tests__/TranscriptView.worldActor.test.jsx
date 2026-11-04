import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { functionCallTranscriptRows } from "src/api/simulate-environments/runDetail";
import TranscriptView from "../TranscriptView";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/sections/common/CellMarkdown", () => ({
  default: ({ text }) => <div>{text}</div>,
}));

describe("world actors in voice transcripts", () => {
  it("names a world actor's action as theirs, not as one of the agent's calls", () => {
    const transcript = [
      { id: "1", speaker_role: "user", content: "Can I get a discount?" },
      ...functionCallTranscriptRows([
        {
          id: "approve-1",
          name: "crm.approve_discount",
          actor_id: "manager",
          actor_name: "Pat Lee",
          refused: true,
          start_time_ms: 2000,
        },
      ]),
    ];
    render(<TranscriptView transcript={transcript} />);

    expect(screen.getByText("Pat Lee (in the world)")).toBeInTheDocument();
    expect(
      screen.getByText(/Refused · crm.approve_discount/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Function call · crm.approve_discount/),
    ).toBeNull();
  });
});
