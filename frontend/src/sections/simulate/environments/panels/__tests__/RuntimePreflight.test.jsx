import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import RuntimePreflight from "../RuntimePreflight";

const CHECKS_PASS = [
  { id: "source", label: "Source", status: "passed", detail: "Repo reachable", missing: [], fix: null },
  { id: "credentials_present", label: "Credentials present", status: "passed", detail: "", missing: [], fix: null },
  { id: "provider_target", label: "Provider target", status: "skipped", detail: "Not applicable", missing: [], fix: null },
];

const CHECKS_FAIL = [
  { id: "source", label: "Source", status: "passed", detail: "Repo reachable", missing: [], fix: null },
  {
    id: "credentials_valid",
    label: "Credentials valid",
    status: "failed",
    detail: "Probe rejected the key",
    missing: ["VAPI_API_KEY"],
    fix: "Add a valid VAPI_API_KEY.",
  },
];

describe("RuntimePreflight", () => {
  it("idle: shows the trigger, gated until the form is valid", () => {
    const onRun = vi.fn();
    const { rerender } = render(
      <RuntimePreflight status="idle" canRun={false} onRun={onRun} />,
    );
    const btn = screen.getByRole("button", { name: "Run preflight" });
    expect(btn).toBeDisabled();
    expect(
      screen.getByText("We check the source and credentials before building."),
    ).toBeInTheDocument();

    rerender(<RuntimePreflight status="idle" canRun onRun={onRun} />);
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("running: shows the in-flight label and disables the trigger", () => {
    render(<RuntimePreflight status="running" canRun onRun={vi.fn()} />);
    const btn = screen.getByRole("button", { name: /Checking source and credentials/ });
    expect(btn).toBeDisabled();
  });

  it("error: surfaces the message and retries via onRun", () => {
    const onRun = vi.fn();
    render(
      <RuntimePreflight
        status="error"
        onRun={onRun}
        error={{ message: "Network unreachable" }}
      />,
    );
    expect(screen.getByText("Preflight couldn't run")).toBeInTheDocument();
    expect(screen.getByText("Network unreachable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("done + connected: Ready to build, Connected pill, check rows, Re-run", () => {
    const onRun = vi.fn();
    render(
      <RuntimePreflight
        status="done"
        state="connected"
        checks={CHECKS_PASS}
        onRun={onRun}
      />,
    );
    expect(screen.getByText("Ready to build")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    // Every check renders its label + mono id.
    expect(screen.getByText("Source")).toBeInTheDocument();
    expect(screen.getByText("credentials_present")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Re-run" }));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("done + failed: N-to-resolve headline, Blocked pill, missing chip + Fix line", () => {
    render(
      <RuntimePreflight
        status="done"
        state="failed"
        checks={CHECKS_FAIL}
        onRun={vi.fn()}
      />,
    );
    expect(screen.getByText("1 check to resolve")).toBeInTheDocument();
    expect(screen.getByText("Blocked")).toBeInTheDocument();
    expect(screen.getByText("VAPI_API_KEY")).toBeInTheDocument();
    expect(screen.getByText(/Add a valid VAPI_API_KEY/)).toBeInTheDocument();
  });

  it("done with an empty checks array: a tolerant fallback, no crash", () => {
    render(<RuntimePreflight status="done" state="connected" checks={[]} onRun={vi.fn()} />);
    expect(screen.getByText("Preflight returned no checks.")).toBeInTheDocument();
  });

  it("orders the rows by the fixed check sequence regardless of response order", () => {
    const shuffled = [CHECKS_PASS[2], CHECKS_PASS[1], CHECKS_PASS[0]];
    render(
      <RuntimePreflight status="done" state="connected" checks={shuffled} onRun={vi.fn()} />,
    );
    const ids = screen
      .getAllByText(/^(source|credentials_present|provider_target)$/)
      .map((n) => n.textContent);
    expect(ids).toEqual(["source", "credentials_present", "provider_target"]);
  });
});
