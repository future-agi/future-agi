import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import RuntimePreflight from "../RuntimePreflight";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";

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

  it("renders Run preflight as the theme's filled primary button, like Run simulation", () => {
    render(<RuntimePreflight status="idle" canRun onRun={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Run preflight" })).toHaveClass(
      "MuiButton-containedPrimary",
    );
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

  // Release-lead ruling #7: Future AGI Cloud does not send the LiveKit probe to a
  // customer URL. The backend marks those rows skipped (ok stays true so the run is
  // not blocked); the panel must say "not verified on hosted", never "verified" in
  // green and never "failed" in red.
  it("shows a LiveKit probe Future AGI Cloud did not send as not verified on hosted", () => {
    const notVerified =
      "not verified on Future AGI Cloud: preflight does not contact customer LiveKit URLs; connection problems will show when the run connects.";
    const aliases = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"];
    const result = {
      ...hostedReport,
      ready_to_submit: true,
      credentials: {
        ...hostedReport.credentials,
        requirements: hostedReport.credentials.requirements.map((item) => ({
          ...item,
          status: "configured",
        })),
        probe: [
          {
            provider: "livekit",
            label: "LiveKit",
            aliases,
            ok: true,
            skipped: true,
            message: `LiveKit ${notVerified}`,
          },
          {
            provider: "livekit_target",
            label: "LiveKit agent",
            aliases,
            ok: true,
            skipped: true,
            message: `LiveKit agent 'returns-agent' ${notVerified}`,
          },
          {
            provider: "openai",
            label: "OpenAI",
            aliases: ["OPENAI_API_KEY"],
            ok: true,
            message: "OpenAI accepted OPENAI_API_KEY",
          },
        ],
      },
    };
    render(<RuntimePreflight status="done" result={result} onRun={vi.fn()} />);

    // The run still proceeds.
    expect(screen.getByText("Ready to build")).toBeInTheDocument();
    const livekit = screen.getByText("LiveKit: not verified on hosted");
    const agent = screen.getByText("LiveKit agent: not verified on hosted");
    for (const row of [livekit, agent]) {
      // Neutral, neither the success green nor the failure red.
      expect(row).toHaveStyle({ color: BUILD_TONES.grey });
    }
    expect(screen.queryByText("LiveKit: verified")).toBeNull();
    expect(screen.queryByText("LiveKit agent: verified")).toBeNull();
    expect(screen.queryByText(/: failed$/)).toBeNull();
    expect(screen.getByText(`LiveKit ${notVerified}`)).toBeInTheDocument();
    // A probe that really ran keeps its verdict.
    expect(screen.getByText("OpenAI: verified")).toHaveStyle({
      color: BUILD_TONES.green,
    });
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
