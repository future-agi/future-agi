import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import RuntimePreflight from "../RuntimePreflight";

// W10-SIMULATE-OTHER-U01: the preflight body carries `checks`
// ({ id, label, status, detail, missing, fix }) that say what failed and how to
// fix it. The panel must show the failed ones and head the result by what
// failed, not blame credentials for a source it could not read.

const check = (id, label, status, detail, fix = null, missing = []) => ({
  id,
  label,
  status,
  detail,
  missing,
  fix,
});

const passingRest = [
  check(
    "credentials_present",
    "Target credentials",
    "passed",
    "every required credential is referenced",
  ),
  check(
    "credential_files",
    "Credential files",
    "skipped",
    "the source does not require a credential file",
  ),
  check(
    "credentials_valid",
    "Credentials accepted by provider",
    "skipped",
    "no credential values were submitted to verify",
  ),
  check(
    "provider_target",
    "Provider agent reachable",
    "skipped",
    "no hosted provider agent to look up",
  ),
  check(
    "platform_dialer",
    "Outbound dialer",
    "skipped",
    "only a phone target is dialled by the platform",
  ),
];

const emptyCredentials = {
  scanned_files: 0,
  detected_connectors: [],
  requirements: [],
  credential_choices: [],
  probe: [],
};

const SOURCE_FIX =
  "Check the owner/repository spelling; for a private repository, choose Private and install the GitHub App on it";

describe("RuntimePreflight failed checks", () => {
  it("names an unreachable repository and how to fix it instead of blaming credentials", () => {
    const result = {
      ready_to_submit: false,
      state: "failed",
      checks: [
        check(
          "source",
          "Source reachable",
          "failed",
          "repository acme/does-not-exist was not found, or it is private and the GitHub App is not installed on it",
          SOURCE_FIX,
        ),
        ...passingRest,
      ],
      credentials: emptyCredentials,
    };
    render(<RuntimePreflight status="done" result={result} onRun={vi.fn()} />);

    expect(screen.getByText("Source can't be read")).toBeInTheDocument();
    expect(screen.queryByText("Credentials need attention")).toBeNull();
    expect(screen.getByText("Source reachable")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Repository acme/does-not-exist was not found, or it is private and the GitHub App is not installed on it",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(SOURCE_FIX)).toBeInTheDocument();
    // Nothing was scanned: the source could not be read at all.
    expect(screen.queryByText(/source files scanned/)).toBeNull();
    // Checks that passed or were skipped add no rows.
    expect(screen.queryByText("Outbound dialer")).toBeNull();
    expect(screen.queryByText("Target credentials")).toBeNull();
  });

  it("names a source with no Dockerfile on a sandbox deployment", () => {
    const result = {
      ready_to_submit: false,
      state: "failed",
      checks: [
        check("source", "Source reachable", "passed", "4 files scanned"),
        ...passingRest,
        check(
          "packaging",
          "Source is buildable",
          "failed",
          "no Dockerfile or compose file packages the agent",
          "Add a Dockerfile that runs the agent, or a compose file that starts it",
        ),
      ],
      credentials: { ...emptyCredentials, scanned_files: 4 },
    };
    render(<RuntimePreflight status="done" result={result} onRun={vi.fn()} />);

    expect(screen.getByText("Source isn't buildable")).toBeInTheDocument();
    expect(screen.queryByText("Credentials need attention")).toBeNull();
    expect(screen.getByText("Source is buildable")).toBeInTheDocument();
    expect(
      screen.getByText("No Dockerfile or compose file packages the agent"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Add a Dockerfile that runs the agent, or a compose file that starts it",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("4 source files scanned")).toBeInTheDocument();
  });

  it("keeps the credentials heading for a credential failure and shows its fix", () => {
    const result = {
      ready_to_submit: false,
      state: "failed",
      checks: [
        check("source", "Source reachable", "passed", "7 files scanned"),
        check(
          "credentials_present",
          "Target credentials",
          "failed",
          "1 required credential(s) not provided",
          "Add LIVEKIT_API_KEY under target credentials",
          ["LIVEKIT_API_KEY"],
        ),
        ...passingRest.slice(1),
      ],
      credentials: { ...emptyCredentials, scanned_files: 7 },
    };
    render(<RuntimePreflight status="done" result={result} onRun={vi.fn()} />);

    expect(screen.getByText("Credentials need attention")).toBeInTheDocument();
    expect(screen.getByText("Target credentials")).toBeInTheDocument();
    expect(
      screen.getByText("Add LIVEKIT_API_KEY under target credentials"),
    ).toBeInTheDocument();
    expect(screen.getByText("7 source files scanned")).toBeInTheDocument();
  });

  it("says the outbound dialer is the blocker when only it failed", () => {
    const result = {
      ready_to_submit: false,
      state: "failed",
      checks: [
        check(
          "source",
          "Source reachable",
          "skipped",
          "provider targets have no source tree to read",
        ),
        ...passingRest.slice(0, 4),
        check(
          "platform_dialer",
          "Outbound dialer",
          "failed",
          "the platform outbound dialer is not configured on this deployment",
          "Contact your Future AGI administrator: phone targets need the platform SIP trunk and simulator LiveKit credentials configured",
        ),
      ],
      credentials: emptyCredentials,
    };
    render(<RuntimePreflight status="done" result={result} onRun={vi.fn()} />);

    expect(
      screen.getByText("Outbound dialer isn't set up"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Credentials need attention")).toBeNull();
    expect(
      screen.getByText(
        "Contact your Future AGI administrator: phone targets need the platform SIP trunk and simulator LiveKit credentials configured",
      ),
    ).toBeInTheDocument();
  });

  it("shows a fix that repeats the detail only once", () => {
    const result = {
      ready_to_submit: false,
      state: "failed",
      checks: [
        check(
          "source",
          "Source reachable",
          "failed",
          "uploaded source archive could not be opened",
          "uploaded source archive could not be opened",
        ),
        ...passingRest,
      ],
      credentials: emptyCredentials,
    };
    render(<RuntimePreflight status="done" result={result} onRun={vi.fn()} />);

    expect(screen.getByText("Source can't be read")).toBeInTheDocument();
    expect(
      screen.getAllByText(/uploaded source archive could not be opened/i),
    ).toHaveLength(1);
  });

  it("adds no check rows to a ready result", () => {
    const result = {
      ready_to_submit: true,
      state: "connected",
      checks: [
        check("source", "Source reachable", "passed", "7 files scanned"),
        ...passingRest,
      ],
      credentials: { ...emptyCredentials, scanned_files: 7 },
    };
    render(<RuntimePreflight status="done" result={result} onRun={vi.fn()} />);

    expect(screen.getByText("Ready to build")).toBeInTheDocument();
    expect(screen.queryByText("Source reachable")).toBeNull();
    expect(screen.queryByText("Outbound dialer")).toBeNull();
  });
});
