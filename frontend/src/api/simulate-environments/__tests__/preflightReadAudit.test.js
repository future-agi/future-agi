import { describe, it, expect } from "vitest";
import {
  preflightToReadAudit,
  MOCK_PREFLIGHT_FAILS,
} from "../preflightReadAudit";
import { agentRefLabel } from "src/sections/simulate/environments/buildEnvironment/helpers/agentRefLabel";
import {
  MOCK_READING,
  MOCK_QUESTIONS,
} from "../_fixtures/preflightFails";

const repoDraft = { kind: "repo", value: "acme/support-bot", ref: "main" };

const happyResponse = (over = {}) => ({
  ready_to_submit: true,
  credentials: {
    scanned_files: 42,
    detected_connectors: ["vapi"],
    requirements: [],
    credential_choices: [],
    probe: [{ provider: "vapi", ok: true }],
    ...(over.credentials || {}),
  },
  packaging: {
    notes: [],
    candidates: [],
    selected_path: "src/agent.py",
    ...(over.packaging || {}),
  },
  ...("ready_to_submit" in over ? { ready_to_submit: over.ready_to_submit } : {}),
  ...(over.checks ? { checks: over.checks } : {}),
});

describe("the default (product) mapping", () => {
  it("is off — a real preflight never shows the designer fixtures", () => {
    expect(MOCK_PREFLIGHT_FAILS).toBe(false);
  });

  it("maps a happy response with no overlay option to empty reading/questions", () => {
    const audit = preflightToReadAudit({ response: happyResponse(), draft: repoDraft });
    expect(audit.reading).toEqual({ tools: [], rules: [], data: [], behavior: [] });
    expect(audit.reading).not.toEqual(MOCK_READING);
    expect(audit.questions).toEqual([]);
    expect(audit.questions).not.toEqual(MOCK_QUESTIONS);
    expect(audit.sectionIssues).toEqual({});
    expect(audit.status).toBe("healthy");
  });
});

describe("preflightToReadAudit — backend checks[]", () => {
  it("carries every non-passing check, with its detail, missing and fix", () => {
    const response = happyResponse({
      checks: [
        { id: "source", label: "Source", status: "passed", detail: "Cloned", missing: [], fix: null },
        {
          id: "credentials_present",
          label: "Credentials present",
          status: "failed",
          detail: "VAPI_API_KEY was not supplied",
          missing: ["VAPI_API_KEY"],
          fix: "Add the key on the hosted-platform form",
        },
        {
          id: "provider_target",
          label: "Provider target",
          status: "skipped",
          detail: "Not probed without a key",
          missing: [],
          fix: null,
        },
      ],
    });
    const audit = preflightToReadAudit({ response, draft: repoDraft });

    expect(audit.checks.map((c) => c.id)).toEqual([
      "source",
      "credentials_present",
      "provider_target",
    ]);
    const failed = audit.checks.find((c) => c.id === "credentials_present");
    expect(failed.label).toBe("Credentials present");
    expect(failed.detail).toBe("VAPI_API_KEY was not supplied");
    expect(failed.missing).toEqual(["VAPI_API_KEY"]);
    expect(failed.fix).toBe("Add the key on the hosted-platform form");
  });

  it("two failing checks are both kept — neither collapses into the other", () => {
    const response = happyResponse({
      checks: [
        { id: "credentials_present", label: "Credentials present", status: "failed", detail: "a", missing: [], fix: null },
        { id: "credentials_valid", label: "Credentials valid", status: "failed", detail: "b", missing: [], fix: null },
      ],
    });
    const audit = preflightToReadAudit({ response, draft: repoDraft });
    expect(audit.checks.filter((c) => c.status === "failed")).toHaveLength(2);
  });

  it("a failing check flips a would-be-healthy audit to warning", () => {
    const response = happyResponse({
      checks: [
        { id: "credentials_valid", label: "Credentials valid", status: "failed", detail: "Rejected", missing: [], fix: null },
      ],
    });
    const audit = preflightToReadAudit({ response, draft: repoDraft });
    expect(audit.status).toBe("warning");
  });

  it("a skipped check alone is not a blocker", () => {
    // `provider_target` is skipped for a repo draft with connector `auto`, and
    // the backend still says ready_to_submit — so a skip must not read as a
    // failure the user has to clear.
    const response = happyResponse({
      checks: [
        { id: "provider_target", label: "Provider target", status: "skipped", detail: "Not probed", missing: [], fix: null },
      ],
    });
    const audit = preflightToReadAudit({ response, draft: repoDraft });
    expect(audit.status).toBe("healthy");
    expect(audit.checks).toHaveLength(1);
  });

  it("all-passed checks stay healthy", () => {
    const response = happyResponse({
      checks: [
        { id: "source", label: "Source", status: "passed", detail: "Cloned", missing: [], fix: null },
      ],
    });
    const audit = preflightToReadAudit({ response, draft: repoDraft });
    expect(audit.status).toBe("healthy");
    expect(audit.checks).toHaveLength(1);
  });

  it("a response with no checks yields an empty array, never undefined", () => {
    const audit = preflightToReadAudit({ response: happyResponse(), draft: repoDraft });
    expect(audit.checks).toEqual([]);
  });

  it("a hardfail carries no checks", () => {
    const audit = preflightToReadAudit({
      error: { response: { data: { detail: "Bad ref" } } },
      draft: repoDraft,
    });
    expect(audit.status).toBe("hardfail");
    expect(audit.checks).toEqual([]);
  });
});

describe("preflightToReadAudit — real branch (mock:false)", () => {
  it("maps a happy response to healthy with no section issues", () => {
    const audit = preflightToReadAudit(
      { response: happyResponse(), draft: repoDraft },
      { mock: false }
    );
    expect(audit.status).toBe("healthy");
    expect(audit.sectionIssues).toEqual({});
    expect(audit.stats.scannedFiles).toBe(42);
    expect(audit.stats.detectedConnectors).toEqual(["vapi"]);
    expect(audit.stats.selectedPath).toBe("src/agent.py");
    expect(audit.stats.readyToSubmit).toBe(true);
    expect(audit.reading.tools).toEqual([]);
    expect(audit.questions).toEqual([]);
    expect(audit.agentRef).toBe("acme/support-bot@main");
  });

  it("ready_to_submit:false alone → warning", () => {
    const audit = preflightToReadAudit(
      { response: happyResponse({ ready_to_submit: false }), draft: repoDraft },
      { mock: false }
    );
    expect(audit.status).toBe("warning");
  });

  it("a blocking packaging finding → tools issue", () => {
    const response = happyResponse({
      packaging: {
        candidates: [
          {
            path: "src/agent.py",
            findings: [
              { code: "NO_ENTRYPOINT", message: "No entrypoint found", blocking: true },
            ],
          },
        ],
      },
    });
    const audit = preflightToReadAudit({ response, draft: repoDraft }, { mock: false });
    expect(audit.sectionIssues.tools.message).toBe("No entrypoint found");
    expect(audit.sectionIssues.tools.hint).toBe("src/agent.py");
    expect(audit.sectionIssues.tools.retryLabel).toBe("Retry read");
    expect(audit.status).toBe("warning");
  });

  it("a missing required credential → behavior issue", () => {
    const response = happyResponse({
      credentials: {
        requirements: [
          {
            environment_name: "VAPI_API_KEY",
            required: true,
            status: "missing",
            purpose: "target provider",
          },
        ],
      },
    });
    const audit = preflightToReadAudit({ response, draft: repoDraft }, { mock: false });
    expect(audit.sectionIssues.behavior.message).toBe("VAPI_API_KEY is missing");
    expect(audit.sectionIssues.behavior.hint).toBe("target provider");
    expect(audit.sectionIssues.behavior.retryLabel).toBeNull();
  });

  it("does not flag a missing credential covered by a credential_choices option", () => {
    const response = happyResponse({
      credentials: {
        requirements: [
          {
            environment_name: "VAPI_API_KEY",
            required: true,
            status: "missing",
            purpose: "target provider",
          },
        ],
        credential_choices: [
          { id: "c1", purpose: "provider", satisfied: false, options: [["VAPI_API_KEY"]] },
        ],
      },
    });
    const audit = preflightToReadAudit({ response, draft: repoDraft }, { mock: false });
    expect(audit.sectionIssues.behavior).toBeUndefined();
    expect(audit.status).toBe("healthy");
  });

  it("a rejected probe → hardfail with the message, no overlay even with mock:true", () => {
    const response = happyResponse({
      credentials: {
        probe: [{ provider: "vapi", ok: false, message: "Vapi rejected the key" }],
      },
    });
    const audit = preflightToReadAudit({ response, draft: repoDraft }, { mock: true });
    expect(audit.status).toBe("hardfail");
    expect(audit.hardfailReason).toContain("Vapi rejected the key");
    expect(audit.reading.tools.length).toBe(0);
    expect(audit.questions.length).toBe(0);
  });

  it("an error → hardfail via errorMessage", () => {
    const audit = preflightToReadAudit(
      { error: { response: { data: { detail: "Bad ref" } } }, draft: repoDraft },
      { mock: true }
    );
    expect(audit.status).toBe("hardfail");
    expect(audit.hardfailReason).toBe("Bad ref");
    expect(audit.reading.tools.length).toBe(0);
  });

  it("a skipped preflight → tools 'Preflight skipped' issue", () => {
    const audit = preflightToReadAudit(
      {
        skipped: "Code upload preflight needs the uploaded archive",
        draft: { kind: "upload", entry: "src/agent.py", files: [] },
      },
      { mock: false }
    );
    expect(audit.sectionIssues.tools.message).toBe("Preflight skipped");
    expect(audit.sectionIssues.tools.hint).toBe(
      "Code upload preflight needs the uploaded archive"
    );
  });
});

describe("preflightToReadAudit — mock overlay (mock:true)", () => {
  it("overlays the failing-check fixtures on a happy response", () => {
    const audit = preflightToReadAudit(
      { response: happyResponse(), draft: repoDraft },
      { mock: true }
    );
    expect(audit.status).toBe("warning");
    expect(Object.keys(audit.sectionIssues).sort()).toEqual(["data", "rules"]);
    expect(audit.reading.tools.length).toBe(12);
    expect(audit.reading).toEqual(MOCK_READING);
    expect(audit.questions.length).toBe(2);
    expect(audit.questions).toEqual(MOCK_QUESTIONS);
    expect(audit.stats.scannedFiles).toBe(42);
  });

  it("retriedSections removes only the named mock gaps", () => {
    const audit = preflightToReadAudit(
      { response: happyResponse(), draft: repoDraft, retriedSections: ["rules"] },
      { mock: true }
    );
    expect(Object.keys(audit.sectionIssues)).toEqual(["data"]);
  });

  it("retrying both mock gaps → healthy, no issues", () => {
    const audit = preflightToReadAudit(
      { response: happyResponse(), draft: repoDraft, retriedSections: ["rules", "data"] },
      { mock: true }
    );
    expect(audit.status).toBe("healthy");
    expect(audit.sectionIssues).toEqual({});
  });

  it("a real blocking finding survives the overlay and retriedSections", () => {
    const response = happyResponse({
      packaging: {
        candidates: [
          {
            path: "src/agent.py",
            findings: [
              { code: "NO_ENTRYPOINT", message: "No entrypoint found", blocking: true },
            ],
          },
        ],
      },
    });
    const audit = preflightToReadAudit(
      { response, draft: repoDraft, retriedSections: ["tools"] },
      { mock: true }
    );
    expect(Object.keys(audit.sectionIssues).sort()).toEqual(["data", "rules", "tools"]);
    expect(audit.sectionIssues.tools.message).toBe("No entrypoint found");
  });
});

describe("agentRefLabel", () => {
  it("repo → value@ref", () => {
    expect(agentRefLabel({ kind: "repo", value: "acme/support-bot", ref: "main" })).toBe(
      "acme/support-bot@main"
    );
  });

  it("repo without a ref → value", () => {
    expect(agentRefLabel({ kind: "repo", value: "acme/support-bot", ref: "" })).toBe(
      "acme/support-bot"
    );
  });

  it("platform → provider · agentId", () => {
    expect(agentRefLabel({ kind: "platform", provider: "vapi", agentId: "asst_1" })).toBe(
      "vapi · asst_1"
    );
  });

  it("upload → entry or first file name", () => {
    expect(agentRefLabel({ kind: "upload", entry: "src/agent.py", files: [] })).toBe(
      "src/agent.py"
    );
    expect(
      agentRefLabel({ kind: "upload", entry: "", files: [{ name: "bot.zip" }] })
    ).toBe("bot.zip");
  });

  it("falls back to 'agent'", () => {
    expect(agentRefLabel(null)).toBe("agent");
    expect(agentRefLabel({ kind: "mystery" })).toBe("agent");
  });
});
