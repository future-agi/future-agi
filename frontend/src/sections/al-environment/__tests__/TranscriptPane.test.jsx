import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import TranscriptPane from "../TranscriptPane";

describe("TranscriptPane", () => {
  it("invites the reader to start a session when there is none", () => {
    render(<TranscriptPane messages={[]} hasSession={false} />);
    expect(screen.getByText(/no session yet/i)).toBeInTheDocument();
  });

  it("greets an open but silent session the way the harness does", () => {
    render(<TranscriptPane messages={[]} hasSession />);
    expect(screen.queryByText(/no session yet/i)).not.toBeInTheDocument();
    expect(screen.getByText(/which agent would you like to test/i)).toBeInTheDocument();
  });

  it("shows the thinking strip while a turn is in flight", () => {
    render(<TranscriptPane messages={[]} hasSession thinking="reading the agent" />);
    expect(screen.getByText("reading the agent")).toBeInTheDocument();
    expect(screen.getByText("0s")).toBeInTheDocument();
  });

  it("renders prose messages", () => {
    const messages = [{ role: "tester", text: "Reading the agent at /tmp/drive_thru." }];
    render(<TranscriptPane messages={messages} hasSession />);
    expect(screen.getByText(/Reading the agent/)).toBeInTheDocument();
  });

  it("shows a run verdict with its check tally", () => {
    const messages = [
      { role: "verdict", text: "passed", detail: { scenario: "plain_order", passed: true, met: 3, of: 3 } },
    ];
    render(<TranscriptPane messages={messages} hasSession />);
    expect(screen.getByText("plain_order")).toBeInTheDocument();
    expect(screen.getByText("pass")).toBeInTheDocument();
    expect(screen.getByText("3/3 checks")).toBeInTheDocument();
  });

  it("marks a failed scenario as failed", () => {
    const messages = [
      { role: "verdict", text: "failed", detail: { scenario: "unknown_item", passed: false, met: 1, of: 3 } },
    ];
    render(<TranscriptPane messages={messages} hasSession />);
    expect(screen.getByText("fail")).toBeInTheDocument();
  });

  it("names the tool that ran", () => {
    const messages = [{ role: "tester", tool: "save_world", detail: { tables: 3 } }];
    render(<TranscriptPane messages={messages} hasSession />);
    expect(screen.getByText("save_world")).toBeInTheDocument();
  });
});
