import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  DEMO_FEEDBACK_SUMMARY,
  DEMO_GUARDRAILS,
  DEMO_OVERVIEW,
  DEMO_RULE_STATS,
  MockAgGrid,
  renderPage,
} from "../../__tests__/helpers";
import RulesView from "../RulesView";
import { ADMIN_LEVELS, expectEditable } from "../../../testing/adminOnly";

const { get, post, snack } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  snack: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get, post } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: snack }));
// Rule writes are Owner or Admin only (useGatewayAdmin); these cases act as
// an Admin unless they set another level.
let orgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel }),
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));

let guardrails;
let ruleStats;
let orgConfigFails;
let nodePipeline;

function answer(url) {
  if (url.endsWith("/gateways/gw-1/config/")) {
    return Promise.resolve({
      data: { result: { guardrail_pipeline: nodePipeline } },
    });
  }
  if (url.includes("org-configs/active")) {
    if (orgConfigFails) return Promise.reject(new Error("boom"));
    return Promise.resolve({ data: { result: { guardrails } } });
  }
  if (url.includes("guardrail-rules")) {
    return Promise.resolve({ data: { result: ruleStats } });
  }
  if (url.includes("guardrail-overview")) {
    return Promise.resolve({ data: { result: DEMO_OVERVIEW } });
  }
  if (url.includes("guardrail-feedback/summary")) {
    return Promise.resolve({ data: { result: DEMO_FEEDBACK_SUMMARY } });
  }
  return Promise.reject(new Error(`unexpected GET ${url}`));
}

const renderRules = () => renderPage(<RulesView gatewayId="gw-1" />);
const row = (id) => screen.findByTestId(`row-${id}`);

describe("Guardrails rules", () => {
  beforeEach(() => {
    guardrails = structuredClone(DEMO_GUARDRAILS);
    ruleStats = structuredClone(DEMO_RULE_STATS);
    orgConfigFails = false;
    orgLevel = 8;
    nodePipeline = { enabled: true, fail_open: false, mode: "sequential" };
    get.mockReset();
    post.mockReset();
    snack.mockReset();
    get.mockImplementation(answer);
    post.mockResolvedValue({
      data: { result: { version: 3, gateway_synced: true } },
    });
  });

  it("lists the org's rules with what they did in the last 7 days, and nothing the gateway does not do", async () => {
    renderRules();
    const pii = await row("pii-detection");
    expect(within(pii).getByText("PII Detection")).toBeInTheDocument();
    expect(within(pii).getByText("Block")).toBeInTheDocument();
    // Where the gateway runs the check, from its type.
    expect(within(pii).getByText("Before LLM")).toBeInTheDocument();
    expect(within(pii).getAllByText("24")).toHaveLength(2);
    expect(within(pii).getByText("75%")).toBeInTheDocument();
    expect(
      within(await row("prompt-injection")).getByText("Warn"),
    ).toBeInTheDocument();

    // The 7-day strip: requests, matched, blocked, warned.
    const strip = screen.getByRole("group", { name: "Last 7 days" });
    expect(within(strip).getByText("592")).toBeInTheDocument();
    expect(within(strip).getByText("42")).toBeInTheDocument();
    expect(within(strip).getByText("3 of 4")).toBeInTheDocument();
    // total_requests counts every request log in the window, embeddings and
    // the other endpoints the rules never read included: it is not
    // a count of requests checked.
    expect(within(strip).getByText("Requests")).toBeInTheDocument();
    expect(within(strip).queryByText(/checked/i)).toBeNull();

    // No stage to choose, no latency the gateway never measures, and no
    // After LLM note while every rule reads the request.
    expect(screen.queryByText(/After LLM checks read/)).toBeNull();
    expect(screen.queryByText(/stream/)).toBeNull();
    // The action, not the execution mode stored with the rule.
    expect(screen.queryByText("sync")).toBeNull();
    expect(screen.queryByText(/latency/i)).toBeNull();
    expect(screen.queryByText(/Stage/)).toBeNull();
  });

  it("shows an ambiguous-name refusal and leaves the stored rule enabled", async () => {
    const message = 'Guardrail name "pii-detection" matches 2 stored entries. Replace the rules list via update-config to resolve duplicates.';
    post.mockRejectedValue({ statusCode: 400, code: "guardrail_name_ambiguous", message });
    const user = userEvent.setup();
    renderRules();
    const checkbox = within(await row("pii-detection")).getByRole("checkbox", { name: "PII Detection on" });
    await user.click(checkbox);
    await waitFor(() => expect(snack.mock.calls).toEqual([[message, { variant: "error" }]]));
    expect(checkbox).toBeChecked();
    expect(post.mock.calls[0][0]).toBe("/agentcc/gateways/gw-1/toggle-guardrail/");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps an ambiguous edit open and shows the same server explanation in the toast and drawer", async () => {
    const message = 'Guardrail name "keyword-blocklist" matches 2 stored entries. Replace the rules list via update-config to resolve duplicates.';
    post.mockRejectedValue({ statusCode: 400, code: "guardrail_name_ambiguous", message });
    const user = userEvent.setup();
    renderRules();
    await user.click(within(await row("keyword-blocklist")).getByText("Keyword Blocklist"));
    const drawer = await screen.findByRole("dialog", { name: "Keyword Blocklist" });
    await user.click(within(drawer).getByRole("combobox", { name: /Action/ }));
    await user.click(await screen.findByRole("option", { name: /Warn/ }));
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(snack.mock.calls).toEqual([[message, { variant: "error" }]]));
    expect(await within(drawer).findByText(message)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Keyword Blocklist" })).toBe(drawer);
    expect(post.mock.calls[0][0]).toBe("/agentcc/gateways/gw-1/update-guardrail/");
  });

  it("turns a rule off from its switch without opening it", async () => {
    const user = userEvent.setup();
    renderRules();
    const pii = await row("pii-detection");
    await user.click(
      within(pii).getByRole("checkbox", { name: "PII Detection on" }),
    );
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/toggle-guardrail/",
    );
    expect(post.mock.calls[0][1]).toEqual({
      name: "pii-detection",
      enabled: false,
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens a rule in a drawer and saves its action without losing its settings", async () => {
    const user = userEvent.setup();
    renderRules();
    await user.click(
      within(await row("keyword-blocklist")).getByText("Keyword Blocklist"),
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Keyword Blocklist",
    });
    expect(within(drawer).getByLabelText(/Blocked keywords/)).toHaveValue(
      "project-zeus\ninternal only",
    );
    await user.click(within(drawer).getByRole("combobox", { name: /Action/ }));
    await user.click(await screen.findByRole("option", { name: /Warn/ }));
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/agentcc/gateways/gw-1/update-guardrail/");
    expect(body.name).toBe("keyword-blocklist");
    expect(body.config).toMatchObject({
      action: "warn",
      enabled: true,
      config: { words: ["project-zeus", "internal only"], whole_word: false },
    });
  });

  // A save Django stored but the gateway did not apply (gateway_synced:false):
  // one toast, the warning in the words every gateway config write uses;
  // never a success toast and never "Could not save". A switch flips as
  // stored; an edited rule's drawer stays open and says why.
  const UNAPPLIED_WARNING =
    "Config saved but gateway sync failed. Changes will apply on next gateway restart.";
  const UNAPPLIED = `Saved, but the gateway did not apply it: ${UNAPPLIED_WARNING}`;
  const unapplied = () =>
    post.mockResolvedValue({
      data: {
        status: true,
        result: { gateway_synced: false, gateway_warning: UNAPPLIED_WARNING },
      },
    });

  it("warns once when the gateway did not apply a switched rule, never that it was saved", async () => {
    unapplied();
    const user = userEvent.setup();
    renderRules();
    await user.click(
      within(await row("pii-detection")).getByRole("checkbox", {
        name: "PII Detection on",
      }),
    );
    await waitFor(() => expect(snack).toHaveBeenCalledTimes(1));
    expect(snack.mock.calls).toEqual([[UNAPPLIED, { variant: "warning" }]]);
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/toggle-guardrail/",
    );
  });

  it("keeps an edited rule's drawer open with the warning when the gateway did not apply it", async () => {
    unapplied();
    const user = userEvent.setup();
    renderRules();
    await user.click(
      within(await row("keyword-blocklist")).getByText("Keyword Blocklist"),
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Keyword Blocklist",
    });
    await user.click(within(drawer).getByRole("combobox", { name: /Action/ }));
    await user.click(await screen.findByRole("option", { name: /Warn/ }));
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(snack).toHaveBeenCalledTimes(1));
    expect(snack.mock.calls).toEqual([[UNAPPLIED, { variant: "warning" }]]);
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/update-guardrail/",
    );
    expect(await within(drawer).findByText(UNAPPLIED)).toBeInTheDocument();
    expect(
      screen.getByRole("dialog", { name: "Keyword Blocklist" }),
    ).toBeInTheDocument();
  });

  // api_contracts/gateway/guardrail-check-actions.json: every check blocks,
  // warns or logs; only pii-detection can also mask the request.
  it("offers the actions the gateway takes: mask only on PII detection", async () => {
    const user = userEvent.setup();
    renderRules();
    await user.click(
      within(await row("pii-detection")).getByText("PII Detection"),
    );
    const drawer = await screen.findByRole("dialog", { name: "PII Detection" });
    await user.click(within(drawer).getByRole("combobox", { name: /Action/ }));
    const options = (await screen.findAllByRole("option")).map(
      (option) => option.textContent.match(/^(Block|Warn|Mask|Log)/)?.[1],
    );
    expect(options).toEqual(["Block", "Warn", "Mask", "Log"]);
    await user.keyboard("{Escape}");
    await user.click(within(drawer).getByRole("button", { name: "Cancel" }));

    await user.click(
      within(await row("prompt-injection")).getByText("Prompt Injection"),
    );
    const other = await screen.findByRole("dialog", {
      name: "Prompt Injection",
    });
    await user.click(within(other).getByRole("combobox", { name: /Action/ }));
    const otherOptions = (await screen.findAllByRole("option")).map(
      (option) => option.textContent.match(/^(Block|Warn|Mask|Log)/)?.[1],
    );
    expect(otherOptions).toEqual(["Block", "Warn", "Log"]);
  });

  // The toolbar's one action is the Future AGI primary.
  it("draws Add check as the Future AGI primary button", async () => {
    renderRules();
    const cta = await screen.findByRole("button", { name: "Add check" });
    expect(cta).toHaveClass("MuiButton-containedPrimary");
  });

  it("adds a check from the catalog, asking for the settings it cannot run without", async () => {
    const user = userEvent.setup();
    guardrails = { rules: [] };
    renderRules();
    // An org with no rules sees one empty state with the one action.
    expect(await screen.findByText("No guardrails")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add check" }));
    const picker = await screen.findByRole("dialog", { name: "Add a check" });
    await user.type(within(picker).getByRole("textbox"), "language");
    await user.click(within(picker).getByText("Language Detection"));

    const drawer = await screen.findByRole("dialog", {
      name: "Language Detection",
    });
    const save = within(drawer).getByRole("button", { name: "Add check" });
    // allowed_scripts defaults to latin, so it can be saved as is.
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).toEqual({
      name: "language-detection",
      config: expect.objectContaining({
        enabled: true,
        action: "block",
        config: { allowed_scripts: ["latin"] },
      }),
    });
  });

  // A partner check calls a service outside the gateway: it is listed once
  // the organization's config holds that service's account, and never
  // offered as a new check.
  it("lists a partner check only once its credential is stored", async () => {
    const user = userEvent.setup();
    guardrails = {
      checks: {
        "pii-detection": { enabled: true, action: "block" },
        "lakera-guard": {
          enabled: true,
          action: "block",
          config: { provider: "lakera", api_key: "__encrypted__" },
        },
        "llama-guard": {
          enabled: true,
          action: "block",
          config: { provider: "llama_guard" },
        },
      },
    };
    renderRules();
    expect(await row("lakera-guard")).toBeInTheDocument();
    // Saved-only partners remain visible even without credentials, but must
    // never regain an enable/edit control through the normal partner catalog.
    const storedOnly = await row("llama-guard");
    expect(storedOnly).toBeInTheDocument();
    expect(within(storedOnly).queryByRole("checkbox")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Add check" }));
    const picker = await screen.findByRole("dialog", { name: "Add a check" });
    expect(within(picker).queryByText("Llama Guard")).toBeNull();
  });

  it("does not list an editable partner without its required credential", async () => {
    guardrails = {
      checks: {
        "pii-detection": { enabled: true, action: "block" },
        "lakera-guard": {
          enabled: true,
          action: "block",
          config: { provider: "lakera" },
        },
      },
    };
    renderRules();
    await row("pii-detection");
    expect(screen.queryByTestId("row-lakera-guard")).toBeNull();
  });

  it("shows a rule that cannot run without its settings as needing setup, not as on", async () => {
    guardrails = {
      rules: [{ name: "topic-restriction", enabled: true, action: "block" }],
    };
    renderRules();
    const topic = await row("topic-restriction");
    expect(
      within(topic).getByRole("button", { name: "Set up" }),
    ).toBeInTheDocument();
    expect(within(topic).queryByRole("checkbox")).toBeNull();
  });

  it("shows the gateway's own fail-open and run mode, read-only, and saves the timeout", async () => {
    const user = userEvent.setup();
    guardrails = { ...DEMO_GUARDRAILS, timeout_ms: 5000, fail_open: true };
    renderRules();
    await row("pii-detection");
    const pipeline = screen.getByRole("region", { name: "Pipeline" });
    const mode = within(pipeline).getByRole("group", {
      name: "Fail-open and run mode",
    });
    // The gateway's values (fail_open false), not the org's stored true.
    expect(
      await within(mode).findByText("Sequential · Fail open off"),
    ).toBeInTheDocument();
    expect(
      within(mode).getByText(
        "Set by this gateway, read-only: it runs your checks in turn and stops at the first block, and a check that errors or times out blocks the request. The gateway sets this for every organization.",
      ),
    ).toBeInTheDocument();
    expect(within(pipeline).queryByRole("checkbox")).toBeNull();
    const timeout = within(pipeline).getByLabelText("Check timeout (ms)");
    fireEvent.change(timeout, { target: { value: "3000" } });
    expect(timeout).toHaveValue(3000);
    await user.click(
      within(pipeline).getByRole("button", { name: "Save timeout" }),
    );
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).toEqual({ guardrails: { timeout_ms: 3000 } });
  });

  it("says so when the gateway does not report how it runs the checks", async () => {
    nodePipeline = undefined;
    renderRules();
    await row("pii-detection");
    expect(
      await screen.findByText(
        /this gateway does not report how it runs organization checks/,
      ),
    ).toBeInTheDocument();
  });

  // A gateway with guardrails off runs no check, so the row says only that,
  // never how the checks would run.
  it("says when guardrails are off on this gateway", async () => {
    nodePipeline = { enabled: false, fail_open: false, mode: "sequential" };
    renderRules();
    await row("pii-detection");
    const mode = screen.getByRole("group", { name: "Fail-open and run mode" });
    expect(
      await within(mode).findByText(
        "Set by this gateway, read-only: guardrails are off on this gateway, so no check runs.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/runs your checks in turn/)).toBeNull();
  });

  // With no checks there is no rule list, but the page still says how this
  // gateway runs checks, read-only, in the gateway's own sentence (the one
  // Org config shows).
  it("says how this gateway runs checks when the organization has none", async () => {
    guardrails = { rules: [] };
    renderRules();
    expect(await screen.findByText("No guardrails")).toBeInTheDocument();
    const mode = await screen.findByRole("group", {
      name: "Fail-open and run mode",
    });
    expect(
      await within(mode).findByText(
        "Set by this gateway, read-only: it runs your checks in turn and stops at the first block, and a check that errors or times out blocks the request. The gateway sets this for every organization.",
      ),
    ).toBeInTheDocument();
    // Nothing to time out and nothing to save with no checks.
    expect(screen.queryByLabelText("Check timeout (ms)")).toBeNull();
  });

  it("says no check runs on a gateway with guardrails off and no checks", async () => {
    guardrails = { rules: [] };
    nodePipeline = { enabled: false, fail_open: false, mode: "sequential" };
    renderRules();
    expect(await screen.findByText("No guardrails")).toBeInTheDocument();
    const mode = await screen.findByRole("group", {
      name: "Fail-open and run mode",
    });
    expect(
      await within(mode).findByText(
        "Set by this gateway, read-only: guardrails are off on this gateway, so no check runs.",
      ),
    ).toBeInTheDocument();
  });

  // Tool Permissions is in no catalog the console offers.
  it("does not offer Tool Permissions in Add a check", async () => {
    const user = userEvent.setup();
    renderRules();
    await user.click(await screen.findByRole("button", { name: "Add check" }));
    const picker = await screen.findByRole("dialog", { name: "Add a check" });
    expect(within(picker).queryByText("Tool Permissions")).toBeNull();
  });

  it("keeps the 7-day strip its own height when a phone-width page scrolls", async () => {
    renderRules();
    await row("pii-detection");
    // The page frame's content is a flex column with min-height 0: a child
    // that clips its overflow would shrink to nothing at 390 px.
    expect(screen.getByRole("group", { name: "Last 7 days" })).toHaveStyle({
      flexShrink: "0",
    });
  });

  it("lets the strip's odd last cell fill its row on a phone, with dividers in the divider colour", async () => {
    renderRules();
    await row("pii-detection");
    const strip = screen.getByRole("group", { name: "Last 7 days" });
    const cells = [...strip.children];
    expect(cells).toHaveLength(5);
    // Cells wrap two to a row on a phone and grow into the space left, so the
    // fifth takes the whole last row instead of leaving an empty slot. (The
    // phone-only widths are media queries, which jsdom does not apply; the
    // 390 px screenshots show them.)
    expect(window.getComputedStyle(strip).flexWrap).toBe("wrap");
    cells.forEach((cell) => {
      const style = window.getComputedStyle(cell);
      expect(style.flexGrow).toBe("1");
      expect(style.borderBottomStyle).toBe("solid");
      expect(style.borderBottomColor).toBe(style.borderRightColor);
    });
  });

  // A check on the request reads it on every route that sends one: the card
  // lists them (constants/guardrailRequestRoutes.js).
  it("lists the routes a check on the request reads, and names no subset as unchecked", async () => {
    renderRules();
    await row("pii-detection");
    const pipeline = screen.getByRole("region", { name: "Pipeline" });
    expect(
      within(pipeline).getByText("Requests Before LLM checks read"),
    ).toBeInTheDocument();
    expect(
      within(pipeline).queryByText(/Embeddings, images, audio and speech/),
    ).toBeNull();
    expect(screen.queryByText(/Not enforced on this gateway/)).toBeNull();
  });

  // The gateway refuses an answer an After LLM check blocks with a 403; a
  // stream ends with the guardrail's error.
  it("shows a block on the model's answer as a block, and says what After LLM checks read", async () => {
    const user = userEvent.setup();
    guardrails = {
      rules: [
        ...DEMO_GUARDRAILS.rules,
        {
          mode: "sync",
          name: "data-leakage-prevention",
          stage: "pre",
          action: "block",
          config: {},
          enabled: true,
          threshold: 0.5,
        },
      ],
    };
    ruleStats = {
      rules: [
        ...DEMO_RULE_STATS.rules,
        {
          rule: "data-leakage-prevention",
          trigger_count: 3,
          block_count: 3,
          warn_count: 0,
        },
      ],
    };
    renderRules();
    const leak = await row("data-leakage-prevention");
    expect(within(leak).getByText("Block")).toBeInTheDocument();
    // The stage the gateway runs it at, not the "pre" stored with the rule.
    expect(within(leak).getByText("After LLM")).toBeInTheDocument();
    expect(screen.queryByText(/not enforced/i)).toBeNull();
    const note = screen.getByText(/After LLM checks read/).textContent;
    expect(note).toMatch(
      /on chat, completions, Anthropic Messages, Gemini and Responses requests, streamed or not/,
    );
    expect(note).toMatch(/A stream that an After LLM check in block mode reads is held until the check passes, so its answer starts once it is complete/);
    expect(screen.queryByText(/cannot hold back/)).toBeNull();
    expect(note).toMatch(
      /a blocked stream carries none of the answer and ends with the guardrail's error/,
    );
    expect(note).toMatch(
      /A background Responses request is refused when an After LLM check would block it/,
    );
    expect(screen.queryByText(/withhold/)).toBeNull();

    await user.click(within(leak).getByText("Data Leakage Prevention"));
    const drawer = await screen.findByRole("dialog", {
      name: "Data Leakage Prevention",
    });
    expect(
      within(drawer).getByText(
        "Reads the model's answer. A block refuses it with a 403. A streamed answer is held until a blocking check passes; a blocked stream carries none of the answer and ends with the guardrail's error.",
      ),
    ).toBeInTheDocument();
    expect(
      within(drawer).getByText("Refuse with a 403 guardrail error."),
    ).toBeInTheDocument();
    await user.click(within(drawer).getByRole("combobox", { name: /Action/ }));
    const options = await screen.findAllByRole("option");
    expect(
      options.map((option) => option.getAttribute("aria-disabled") === "true"),
    ).toEqual([false, false, false]);
  });

  it("starts a new check on the model's answer on block, and adds no stage", async () => {
    const user = userEvent.setup();
    renderRules();
    await user.click(await screen.findByRole("button", { name: "Add check" }));
    const picker = await screen.findByRole("dialog", { name: "Add a check" });
    expect(
      within(picker).getAllByText("After LLM · reads the answer"),
    ).toHaveLength(2);
    await user.click(within(picker).getByText("Hallucination Detection"));
    const drawer = await screen.findByRole("dialog", {
      name: "Hallucination Detection",
    });
    expect(
      within(drawer).getByRole("combobox", { name: /Action/ }),
    ).toHaveTextContent("Block");
    await user.click(within(drawer).getByRole("button", { name: "Add check" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const [, body] = post.mock.calls[0];
    expect(body.name).toBe("hallucination-detection");
    expect(body.config).toMatchObject({ action: "block", enabled: true });
    expect(body.config).not.toHaveProperty("stage");
  });

  it.each([
    ["a Member", 3],
    ["a Viewer", 1],
    ["a user whose level is not loaded", null],
  ])(
    "shows every rule write disabled, saying why, for %s",
    async (_, level) => {
      orgLevel = level;
      renderRules();
      const pii = await row("pii-detection");
      const toggle = within(pii).getByRole("checkbox", {
        name: "PII Detection on",
      });
      expect(toggle).toBeDisabled();
      expect(
        toggle.closest('[aria-label="Owner or Admin only"]'),
      ).not.toBeNull();
      const add = screen.getByRole("button", { name: "Add check" });
      expect(add).toBeDisabled();
      expect(add.closest('[aria-label="Owner or Admin only"]')).not.toBeNull();
      expect(
        screen.getByRole("button", { name: "Save timeout" }),
      ).toBeDisabled();
      // A rule opens to be read; its settings, Save and Remove are disabled.
      fireEvent.click(within(pii).getByText("PII Detection"));
      const drawer = await screen.findByRole("dialog", {
        name: "PII Detection",
      });
      for (const name of ["Save", "Remove"]) {
        const control = within(drawer).getByRole("button", { name });
        expect(control).toBeDisabled();
        expect(
          control.closest('[aria-label="Owner or Admin only"]'),
        ).not.toBeNull();
      }
      expect(
        within(drawer).getByRole("combobox", { name: /Action/ }),
      ).toHaveAttribute("aria-disabled", "true");
      expect(post).not.toHaveBeenCalled();
    },
  );

  it.each(ADMIN_LEVELS)(
    "lets %s switch, add, open, save and remove a rule",
    async (_, level) => {
      orgLevel = level;
      renderRules();
      const pii = await row("pii-detection");
      expectEditable(
        within(pii).getByRole("checkbox", { name: "PII Detection on" }),
      );
      expectEditable(screen.getByRole("button", { name: "Add check" }));
      fireEvent.click(within(pii).getByText("PII Detection"));
      const drawer = await screen.findByRole("dialog", {
        name: "PII Detection",
      });
      for (const name of ["Save", "Remove"]) {
        expectEditable(within(drawer).getByRole("button", { name }));
      }
    },
  );

  it("says the rules could not be loaded instead of showing none", async () => {
    orgConfigFails = true;
    renderRules();
    expect(
      await screen.findByText("Guardrails could not be loaded"),
    ).toBeInTheDocument();
    expect(screen.queryByText("No guardrails")).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
