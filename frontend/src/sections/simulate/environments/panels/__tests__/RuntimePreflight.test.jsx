import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import RuntimePreflight from "../RuntimePreflight";

const hostedReport = {
  ready_to_submit: false,
  credentials: {
    scanned_files: 7,
    detected_connectors: ["livekit"],
    requirements: [
      { environment_name: "GOOGLE_APPLICATION_CREDENTIALS_JSON", purpose: "Agent service-account credential file", kind: "file", required: true, status: "missing" },
      { environment_name: "LIVEKIT_API_KEY", purpose: "target_provider", required: true, status: "configured" },
      { environment_name: "RETELL_API_KEY", purpose: "target_provider", required: false, status: "optional" },
    ],
    credential_choices: [],
    probe: [{ provider: "livekit", label: "LiveKit", aliases: ["LIVEKIT_API_KEY"], ok: false, message: "Credential rejected" }],
  },
};

describe("RuntimePreflight", () => {
  it("only allows a valid form to start preflight", () => {
    const onRun = vi.fn();
    const { rerender } = render(<RuntimePreflight status="idle" canRun={false} onRun={onRun} />);
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeDisabled();
    rerender(<RuntimePreflight status="idle" canRun onRun={onRun} />);
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    expect(onRun).toHaveBeenCalledTimes(1);
    rerender(<RuntimePreflight status="running" canRun onRun={onRun} />);
    expect(screen.getByRole("button", { name: /Checking source and credentials/ })).toBeDisabled();
  });

  it("shows the server error and allows a retry", () => {
    const onRun = vi.fn();
    render(<RuntimePreflight status="error" onRun={onRun} error={{ response: { data: { detail: "Source archive unavailable" } } }} />);
    expect(screen.getByText("Source archive unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("shows required credential files and failed live probes from the hosted response", () => {
    render(<RuntimePreflight status="done" result={hostedReport} onRun={vi.fn()} />);
    expect(screen.getByText("Credentials need attention")).toBeInTheDocument();
    expect(screen.getByText("GOOGLE_APPLICATION_CREDENTIALS_JSON")).toBeInTheDocument();
    expect(screen.getByText("Agent service-account credential file · Credential file")).toBeInTheDocument();
    expect(screen.getByText("Credential rejected")).toBeInTheDocument();
    expect(screen.getByText("7 source files scanned · Detected: livekit")).toBeInTheDocument();
    expect(screen.queryByText("RETELL_API_KEY")).toBeNull();
  });

  it("shows a ready uploaded credential and supports re-running", () => {
    const onRun = vi.fn();
    const result = {
      ...hostedReport,
      ready_to_submit: true,
      credentials: {
        ...hostedReport.credentials,
        requirements: hostedReport.credentials.requirements.map((item) => ({ ...item, status: "configured" })),
        probe: [],
      },
    };
    render(<RuntimePreflight status="done" result={result} onRun={onRun} />);
    expect(screen.getByText("Ready to build")).toBeInTheDocument();
    expect(screen.getByText("GOOGLE_APPLICATION_CREDENTIALS_JSON")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Re-run" }));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("explains the alternative credential sets in an ambiguous source", () => {
    const result = {
      ...hostedReport,
      credentials: {
        ...hostedReport.credentials,
        credential_choices: [{ id: "target_provider", satisfied: false, options: [["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"], ["RETELL_API_KEY"]] }],
      },
    };
    render(<RuntimePreflight status="done" result={result} onRun={vi.fn()} />);
    expect(screen.getByText("Provide one credential set: LIVEKIT_URL + LIVEKIT_API_KEY + LIVEKIT_API_SECRET or RETELL_API_KEY")).toBeInTheDocument();
  });
});
