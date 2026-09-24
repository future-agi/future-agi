import { render, screen, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import ReadAudit from "../read-audit/ReadAudit";
import { preflightToReadAudit } from "src/api/simulate-environments/preflightReadAudit";
import {
  useEnvironmentsStore,
  resetEnvironmentsStore,
} from "../../store/useEnvironmentsStore";
import {
  READ_AUDIT_COPY,
  MOCK_SECTION_ISSUES,
} from "../readAudit.constants";

const theme = createTheme({
  palette: palette("light"),
  spacing: (factor) => `${0.25 * factor}rem`,
});

const DRAFT = { kind: "repo", value: "acme/support-bot", ref: "main" };

// A plain happy preflight response — the mock overlay then fills reading /
// questions / mock section gaps (rules, data).
const HAPPY = {
  ready_to_submit: true,
  credentials: {
    scanned_files: 42,
    detected_connectors: ["vapi"],
    requirements: [],
    credential_choices: [],
    probe: [{ provider: "vapi", ok: true }],
  },
  packaging: { notes: [], candidates: [], selected_path: "src/agent.py" },
};

// Happy, but with a blocking packaging finding → a real `tools` section gap that
// survives the retries the mock gaps do not.
const RESPONSE_WITH_FINDING = {
  ...HAPPY,
  packaging: {
    ...HAPPY.packaging,
    candidates: [
      { path: "src/agent.py", findings: [{ code: "NO_ENTRYPOINT", message: "No entrypoint found", blocking: true }] },
    ],
  },
};

// A rejected credential probe → a hard fail (the source cannot be read).
const HARDFAIL = {
  ...HAPPY,
  credentials: { ...HAPPY.credentials, probe: [{ provider: "vapi", ok: false, message: "auth rejected" }] },
};

// Recomputes the audit from the live `retriedSections`, exactly as the page's
// usePreflight does, so per-section / retry-all store writes flow through the
// mapper and back into <ReadAudit> on the next render.
//
// `mock` opts into the designer fixtures. The product mapping carries no
// reading and no questions yet (the backend does not return them), so the
// fixtures are the only way to exercise the fact sections and the question
// stepper — they are test data here, never the default a user would see.
function Harness({ response, mock, onBuild, onBack, onRetryRead }) {
  const retriedSections = useEnvironmentsStore((s) => s.retriedSections);
  const audit = preflightToReadAudit({ response, draft: DRAFT, retriedSections }, { mock });
  return <ReadAudit audit={audit} onBuild={onBuild} onBack={onBack} onRetryRead={onRetryRead} />;
}
Harness.propTypes = {
  response: PropTypes.shape({ ready_to_submit: PropTypes.bool }),
  mock: PropTypes.bool,
  onBuild: PropTypes.func,
  onBack: PropTypes.func,
  onRetryRead: PropTypes.func,
};

function renderAt(ui, route = "/") {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        {ui}
      </ThemeProvider>
    </MemoryRouter>,
  );
}

function renderHarness({ response = HAPPY, route = "/", mock = false, ...handlers } = {}) {
  const props = {
    onBuild: vi.fn(),
    onBack: vi.fn(),
    onRetryRead: vi.fn(),
    ...handlers,
  };
  renderAt(<Harness response={response} mock={mock} {...props} />, route);
  return props;
}

// The fixture-driven variant: an audit that carries reading + questions.
const renderWithFixtures = (opts = {}) => renderHarness({ ...opts, mock: true });

// The StatChip renders <value/><label/> as adjacent Typography with no divider,
// so the value is the label's immediate previous sibling.
const statValue = (label) => screen.getByText(label).previousElementSibling.textContent;

// The band renders before the body, so its "Retry read" is deterministically first.
const bandRetry = () => screen.getAllByRole("button", { name: READ_AUDIT_COPY.retry })[0];

// The "Retry read" inside a section card, found from its gap message.
const sectionRetry = (message) =>
  within(screen.getByText(message).parentElement.parentElement)
    .getByRole("button", { name: READ_AUDIT_COPY.retry });

describe("ReadAudit", () => {
  beforeEach(() => {
    resetEnvironmentsStore();
    vi.clearAllMocks();
  });

  it("renders the title, the warning band and the stat bar", () => {
    renderWithFixtures();
    expect(screen.getByText(READ_AUDIT_COPY.title)).toBeInTheDocument();
    expect(screen.getByText(/Reader completed with 2 gaps/)).toBeInTheDocument();
    expect(screen.getByText(/rules, data could not be read completely/)).toBeInTheDocument();

    expect(statValue("Tools")).toBe("12");
    expect(statValue("Rules")).toBe("—");
    expect(statValue("Data")).toBe("—");
    expect(statValue("Behavior")).toBe("3");
    expect(statValue("Inferred")).toBe("1");
    expect(statValue("Open")).toBe("2");
  });

  it("renders the four section cards and the rules gap", () => {
    renderWithFixtures();
    expect(screen.getByText("Tools it can call")).toBeInTheDocument();
    expect(screen.getByText("Rules it must hold")).toBeInTheDocument();
    expect(screen.getByText("Data it starts with")).toBeInTheDocument();
    expect(screen.getByText("How it behaves")).toBeInTheDocument();
    expect(screen.getByText(MOCK_SECTION_ISSUES.rules.message)).toBeInTheDocument();
  });

  it("per-section retry pokes the store AND re-reads, dropping just that gap", async () => {
    const user = userEvent.setup();
    const { onRetryRead } = renderWithFixtures();
    await user.click(sectionRetry(MOCK_SECTION_ISSUES.rules.message));

    expect(useEnvironmentsStore.getState().retriedSections).toEqual(["rules"]);
    // A real gap needs the refetch, so per-section retry re-reads too.
    expect(onRetryRead).toHaveBeenCalled();
    expect(screen.getByText(/Reader completed with 1 gap/)).toBeInTheDocument();
    expect(screen.getByText(/data could not be read completely/)).toBeInTheDocument();
    // The rules card now shows its five policy facts.
    expect(screen.getAllByText("POLICY.YAML")).toHaveLength(5);
  });

  it("hides the band Retry when the draft is skipped (no re-read possible)", () => {
    // No onRetryRead = a skipped draft whose query is disabled; the band must not
    // offer a retry that would POST a bodyless request.
    renderWithFixtures({ onRetryRead: undefined });
    expect(screen.getByText(/Reader completed with 2 gaps/)).toBeInTheDocument();
    // Only the two section-card retries remain — the band's is gone.
    expect(screen.getAllByRole("button", { name: READ_AUDIT_COPY.retry })).toHaveLength(2);
  });

  it("retry-all clears the band, refetches once and fills the counts", async () => {
    const user = userEvent.setup();
    const { onRetryRead } = renderWithFixtures();
    await user.click(bandRetry());

    expect(screen.queryByText(/Reader completed with/)).not.toBeInTheDocument();
    expect(onRetryRead).toHaveBeenCalledTimes(1);
    expect(statValue("Rules")).toBe("5");
    expect(statValue("Data")).toBe("4");
  });

  it("keeps a real tools gap through both retries", async () => {
    const user = userEvent.setup();
    renderWithFixtures({ response: RESPONSE_WITH_FINDING });
    expect(screen.getByText(/Reader completed with 3 gaps/)).toBeInTheDocument();

    // Per-section retry on the tools card writes to the store, but the real gap
    // is re-derived every render so it survives the write.
    await user.click(sectionRetry("No entrypoint found"));
    expect(useEnvironmentsStore.getState().retriedSections).toContain("tools");
    expect(screen.getByText("No entrypoint found")).toBeInTheDocument();

    // Retry-all drops every mock gap; the real tools gap still stands alone.
    await user.click(bandRetry());
    expect(screen.getByText(/Reader completed with 1 gap/)).toBeInTheDocument();
    expect(screen.getByText("No entrypoint found")).toBeInTheDocument();
    expect(statValue("Tools")).toBe("—");
  });

  it("auto-advances on a choice, gates the build on the last answer, then builds", async () => {
    const user = userEvent.setup();
    const { onBuild } = renderWithFixtures();

    expect(screen.getByText("1/2")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Read-only/ }));
    expect(screen.getByText("2/2")).toBeInTheDocument();

    const build = screen.getByRole("button", { name: /Build the environment/ });
    expect(build).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Yes/ }));
    expect(build).toBeEnabled();
    await user.click(build);

    expect(onBuild).toHaveBeenCalledWith(expect.objectContaining({
      "tool-side-effects": expect.objectContaining({ pick: 0, skipped: false }),
      "policy-enforcement": expect.objectContaining({ pick: 0, skipped: false }),
    }));
  });

  it("skipping keeps the build open until every question is resolved", async () => {
    const user = userEvent.setup();
    renderWithFixtures();

    await user.click(screen.getByRole("button", { name: READ_AUDIT_COPY.skip }));
    expect(statValue("Open")).toBe("1");

    await user.click(screen.getByRole("button", { name: READ_AUDIT_COPY.skip }));
    const build = screen.getByRole("button", { name: /Build the environment/ });
    expect(build).toBeEnabled();
    fireEvent.mouseOver(build.parentElement);
    expect(await screen.findByText(/2 skipped/)).toBeInTheDocument();
  });

  it("shows the hard-fail page and changes source", async () => {
    const user = userEvent.setup();
    const { onBack } = renderHarness({ response: HARDFAIL });

    expect(screen.getByText(READ_AUDIT_COPY.hardfailTitle)).toBeInTheDocument();
    expect(screen.getByText("auth rejected")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: READ_AUDIT_COPY.changeSource }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("?readerStatus=hardfail forces the demo hard-fail, retry cycles to the band", async () => {
    const user = userEvent.setup();
    const { onRetryRead } = renderWithFixtures({ route: "/?readerStatus=hardfail" });

    expect(screen.getByText(READ_AUDIT_COPY.hardfailTitle)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: READ_AUDIT_COPY.retry }));

    expect(screen.getByText(/Reader completed with 2 gaps/)).toBeInTheDocument();
    expect(onRetryRead).not.toHaveBeenCalled();
  });

  it("renders a single column when there are no questions", () => {
    const audit = { ...preflightToReadAudit({ response: HAPPY, draft: DRAFT }), questions: [] };
    renderAt(<ReadAudit audit={audit} onBuild={vi.fn()} onBack={vi.fn()} onRetryRead={vi.fn()} />);
    expect(screen.queryByText(READ_AUDIT_COPY.questionsTitle)).not.toBeInTheDocument();
    expect(screen.getByText(READ_AUDIT_COPY.title)).toBeInTheDocument();
  });

  it("still offers Build when there are no questions to answer", async () => {
    const user = userEvent.setup();
    const onBuild = vi.fn();
    const audit = { ...preflightToReadAudit({ response: HAPPY, draft: DRAFT }), questions: [] };
    renderAt(<ReadAudit audit={audit} onBuild={onBuild} onBack={vi.fn()} onRetryRead={vi.fn()} />);

    const build = screen.getByRole("button", { name: READ_AUDIT_COPY.build });
    expect(build).toBeEnabled();
    await user.click(build);
    expect(onBuild).toHaveBeenCalledWith({});
  });

  it("shows exactly one Build affordance when questions exist", async () => {
    const user = userEvent.setup();
    renderWithFixtures();
    // The questions column owns the CTA; the no-questions footer must not
    // double it up once the stepper reaches its last question.
    expect(screen.queryByRole("button", { name: /Build the environment/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: /Read-only/ }));
    expect(screen.getAllByRole("button", { name: /Build the environment/ })).toHaveLength(1);
  });

  it("a real happy preflight shows no invented facts, gaps or questions", () => {
    renderHarness();
    // Fixture content from the designer overlay must not reach a real user.
    expect(screen.queryByText("verify_identity")).not.toBeInTheDocument();
    expect(screen.queryByText("customers.csv")).not.toBeInTheDocument();
    expect(screen.queryByText(MOCK_SECTION_ISSUES.rules.message)).not.toBeInTheDocument();
    expect(screen.queryByText(READ_AUDIT_COPY.questionsTitle)).not.toBeInTheDocument();
    expect(screen.queryByText(/Reader completed with/)).not.toBeInTheDocument();
    expect(statValue("Tools")).toBe("0");
  });

  it("renders the backend's failing checks with their detail, missing keys and fix", () => {
    renderHarness({
      response: {
        ...HAPPY,
        checks: [
          { id: "source", label: "Source", status: "passed", detail: "Cloned acme/support-bot", missing: [], fix: null },
          {
            id: "credentials_present",
            label: "Credentials present",
            status: "failed",
            detail: "No value supplied for the target provider",
            missing: ["VAPI_API_KEY"],
            fix: "Add the key on the hosted-platform form",
          },
          {
            id: "credentials_valid",
            label: "Credentials valid",
            status: "failed",
            detail: "Not probed",
            missing: [],
            fix: null,
          },
        ],
      },
    });

    expect(screen.getByText(READ_AUDIT_COPY.checksTitle)).toBeInTheDocument();
    // Both failures stand on their own — neither collapses into the other.
    expect(screen.getByText("Credentials present")).toBeInTheDocument();
    expect(screen.getByText("Credentials valid")).toBeInTheDocument();
    expect(screen.getByText(/No value supplied for the target provider/)).toBeInTheDocument();
    expect(screen.getByText(/VAPI_API_KEY/)).toBeInTheDocument();
    expect(screen.getByText(/Add the key on the hosted-platform form/)).toBeInTheDocument();
    // A passed check is not a gap — it is not listed.
    expect(screen.queryByText("Source")).not.toBeInTheDocument();
  });

  it("lists no checks section when every check passed", () => {
    renderHarness({
      response: {
        ...HAPPY,
        checks: [
          { id: "source", label: "Source", status: "passed", detail: "Cloned", missing: [], fix: null },
        ],
      },
    });
    expect(screen.queryByText(READ_AUDIT_COPY.checksTitle)).not.toBeInTheDocument();
  });
});
