import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render, screen } from "src/utils/test-utils";
import FactSection from "../read-audit/FactSection";
import SectionIssue from "../read-audit/SectionIssue";
import ReaderStatusBand from "../read-audit/ReaderStatusBand";
import StatChip from "../read-audit/StatChip";
import HardFailPage from "../read-audit/HardFailPage";
import { ORIGIN_ID } from "../provenance.constants";
import { MOCK_SECTION_ISSUES, READ_AUDIT_COPY } from "../readAudit.constants";

const FACTS = [
  { name: "verify_identity", origin: ORIGIN_ID.CONFIG, note: "sync" },
  { name: "return-window rule", origin: ORIGIN_ID.POLICY },
  { name: "lookup_order", origin: ORIGIN_ID.INFERRED, warning: "guessed from the prompt" },
];

describe("FactSection", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders one mono name + origin chip per fact and the count pill", () => {
    render(<FactSection icon="solar:code-square-linear" title="Tools it can call" count={FACTS.length} facts={FACTS} />);
    expect(screen.getByText("verify_identity")).toBeInTheDocument();
    expect(screen.getByText("return-window rule")).toBeInTheDocument();
    expect(screen.getByText("lookup_order")).toBeInTheDocument();
    expect(screen.getByText("CONFIG")).toBeInTheDocument();
    expect(screen.getByText("POLICY.YAML")).toBeInTheDocument();
    expect(screen.getByText("INFERRED")).toBeInTheDocument();
    expect(screen.getByText(String(FACTS.length))).toBeInTheDocument();
  });

  it("renders the issue block and no fact rows when an issue is set", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const issue = MOCK_SECTION_ISSUES.rules;
    render(<FactSection icon="solar:shield-check-linear" title="Rules it must hold" count={0} facts={[]} issue={issue} onRetry={onRetry} />);
    expect(screen.getByText(issue.message)).toBeInTheDocument();
    expect(screen.getByText(issue.hint)).toBeInTheDocument();
    expect(screen.queryByText("verify_identity")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: issue.retryLabel }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders the empty copy when there are no facts", () => {
    render(<FactSection icon="solar:database-linear" title="Data it starts with" count={0} facts={[]} />);
    expect(screen.getByText(READ_AUDIT_COPY.empty)).toBeInTheDocument();
  });

  it("shows a fact's warning tooltip on hover", async () => {
    const user = userEvent.setup();
    const warning = "guessed from the prompt";
    render(<FactSection icon="solar:playlist-linear" title="How it behaves" count={1} facts={[{ name: "reroute", origin: ORIGIN_ID.INFERRED, warning }]} />);
    await user.hover(screen.getByLabelText(warning));
    expect(await screen.findByText(warning)).toBeInTheDocument();
  });
});

describe("SectionIssue", () => {
  it("renders no button when the issue has no retryLabel", () => {
    const issue = { ...MOCK_SECTION_ISSUES.rules, retryLabel: null };
    render(<SectionIssue issue={issue} onRetry={vi.fn()} />);
    expect(screen.getByText(issue.message)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("ReaderStatusBand", () => {
  it("renders the plural gap count and the issue list", async () => {
    const user = userEvent.setup();
    const onRetryAll = vi.fn();
    render(<ReaderStatusBand issueCount={2} issues={{ rules: {}, data: {} }} onRetryAll={onRetryAll} />);
    expect(screen.getByText(/Reader completed with 2 gaps/)).toBeInTheDocument();
    expect(screen.getByText(/rules, data could not be read completely/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry read" }));
    expect(onRetryAll).toHaveBeenCalledTimes(1);
  });

  it("uses the singular gap for a single issue", () => {
    render(<ReaderStatusBand issueCount={1} issues={{ rules: {} }} onRetryAll={vi.fn()} />);
    expect(screen.getByText(/Reader completed with 1 gap/)).toBeInTheDocument();
    expect(screen.queryByText(/1 gaps/)).not.toBeInTheDocument();
  });
});

describe("StatChip", () => {
  it("renders the value for an amber tone", () => {
    render(<StatChip label="Rules" value="—" tone="amber" />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("Rules")).toBeInTheDocument();
  });
});

describe("HardFailPage", () => {
  const setup = (overrides = {}) => {
    const props = {
      agentRef: "acme/support-bot@main",
      reason: "No response from the agent source.",
      onRetry: vi.fn(),
      onChangeSource: vi.fn(),
      onContinueWithDefaults: vi.fn(),
      ...overrides,
    };
    render(<HardFailPage {...props} />);
    return props;
  };

  it("renders the title, reason and the two actions", async () => {
    const user = userEvent.setup();
    const props = setup();
    expect(screen.getByText(READ_AUDIT_COPY.hardfailTitle)).toBeInTheDocument();
    expect(screen.getByText(props.reason)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: READ_AUDIT_COPY.retry }));
    expect(props.onRetry).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: READ_AUDIT_COPY.changeSource }));
    expect(props.onChangeSource).toHaveBeenCalledTimes(1);
  });

  it("calls onContinueWithDefaults from the continue link", async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.click(screen.getByRole("button", { name: /Continue without reading/ }));
    expect(props.onContinueWithDefaults).toHaveBeenCalledTimes(1);
  });

  it("labels the back button 'Back'", () => {
    setup();
    expect(screen.getByRole("button", { name: READ_AUDIT_COPY.back })).toBeInTheDocument();
  });
});
