import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import AdmissionBindingsEditor from "./AdmissionBindingsEditor";
import { checkBindings } from "./admissionBindings";

const CODES = /\b(402|429)\b|call_cap_unenforceable|agent_budget_exhausted/;

const KEYS = {
  status: "ready",
  keys: [
    { id: "key_a1", name: "Primary", status: "active" },
    { id: "key_a2", name: "Batch", status: "active" },
  ],
};

// One agent cap, key_a1 bound, key_a2 left unbound: the draft editor shows
// the disclosure, the keys that would be refused and the acknowledgement.
const REPORT = {
  required: true,
  loading: false,
  listUnavailable: false,
  unbound: [{ id: "key_a2", name: "Batch" }],
  uncapped: [],
  unknownKeys: [],
  rolloutConflicts: [],
  needsAck: true,
  signature: "sig",
};

const DRAFT = {
  agents: [{ id: "worker", limit: "5" }],
  key_ids: [],
  bindings: [{ key: "key_a1", agent: "worker" }],
  ack: "",
};

function renderEditor(report = REPORT, draft = DRAFT, keys = KEYS) {
  return render(
    <AdmissionBindingsEditor
      draft={draft}
      onChange={vi.fn()}
      report={report}
      keys={keys}
      onReloadKeys={vi.fn()}
    />,
  );
}

// The text of a node without its code lines.
function proseOf(node) {
  const copy = node.cloneNode(true);
  copy
    .querySelectorAll('[data-testid="admission-code"]')
    .forEach((line) => line.remove());
  return copy.textContent;
}

describe("Admission draft editor copy", () => {
  it("says in plain words that unbound keys are refused while an agent cap exists, with the code under it", () => {
    renderEditor();
    const disclosure = screen.getByTestId("admission-binding-disclosure");
    expect(proseOf(disclosure)).not.toMatch(CODES);
    expect(proseOf(disclosure)).toContain(
      "While any agent cap exists, the gateway refuses requests from every execution key that has no binding below, including keys that have no cap at all.",
    );
    expect(within(disclosure).getByTestId("admission-code")).toHaveTextContent(
      "402 call_cap_unenforceable",
    );
  });

  it("says the same in plain words for a draft with no agent cap, with the code under it", () => {
    renderEditor(
      { ...REPORT, required: false, unbound: [], needsAck: false },
      { ...DRAFT, agents: [], bindings: [] },
    );
    const disclosure = screen.getByTestId("admission-binding-disclosure");
    expect(proseOf(disclosure)).not.toMatch(CODES);
    expect(proseOf(disclosure)).toContain(
      "Once an agent cap exists, the gateway refuses requests from every execution key without a binding.",
    );
    expect(within(disclosure).getByTestId("admission-code")).toHaveTextContent(
      "402 call_cap_unenforceable",
    );
  });

  it("titles the keys that would be refused in plain words, with the code under it", () => {
    renderEditor();
    const unbound = screen.getByTestId("admission-unbound-keys");
    expect(
      within(unbound).getByText(
        "Keys that will be refused until they are bound",
      ),
    ).toBeInTheDocument();
    expect(proseOf(unbound)).not.toMatch(CODES);
    expect(within(unbound).getByTestId("admission-code")).toHaveTextContent(
      "402 call_cap_unenforceable",
    );
  });

  it("labels the acknowledgement in plain words, reachable by its test id and role", () => {
    renderEditor();
    const ack = within(screen.getByTestId("admission-unbound-ack")).getByRole(
      "checkbox",
    );
    expect(ack).toHaveAccessibleName(
      "I understand that 1 key without a binding will be refused once I activate: key_a2.",
    );
  });

  it("labels the acknowledgement in plain words when the key list could not be read", () => {
    renderEditor({ ...REPORT, listUnavailable: true, unbound: [] }, DRAFT, {
      status: "error",
      keys: [],
    });
    const ack = within(screen.getByTestId("admission-unbound-ack")).getByRole(
      "checkbox",
    );
    expect(ack).toHaveAccessibleName(
      "I understand that this organization's key list could not be read, so every key not bound above will be refused once I activate.",
    );
  });

  it("refuses agent caps with no binding in plain words, the code last", () => {
    expect(() => checkBindings([], ["worker"])).toThrow(
      "Agent caps need key bindings: with no binding, the gateway refuses requests from every execution key, including keys that have no cap. Add a key-to-agent binding for each key that should keep working. Code: 402 call_cap_unenforceable.",
    );
  });
});
