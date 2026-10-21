import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";

import VoiceLeftPanel from "../VoiceLeftPanel";

// A simulate call with an execution id: the case that shows Checklist and Graph.
const data = {
  module: "simulate",
  call_execution_id: "call-1",
  id: "call-1",
  status: "completed",
  transcript: [],
};

describe("VoiceLeftPanel path tabs", () => {
  it("shows Checklist and Graph for a simulate call by default", () => {
    render(<VoiceLeftPanel data={data} />);
    expect(screen.getByText("Checklist")).toBeInTheDocument();
    expect(screen.getByText("Graph")).toBeInTheDocument();
  });

  it("hides them when the host asks", () => {
    render(<VoiceLeftPanel data={data} hidePathTabs />);
    expect(screen.getByText("Transcript")).toBeInTheDocument();
    expect(screen.queryByText("Checklist")).not.toBeInTheDocument();
    expect(screen.queryByText("Graph")).not.toBeInTheDocument();
  });
});
