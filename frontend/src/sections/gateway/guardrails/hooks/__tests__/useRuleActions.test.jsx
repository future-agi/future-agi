import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

import { catalogEntry } from "../../catalog";
import { normalizeGuardrails } from "../../catalog/rule-model";
import { useRuleActions } from "../useRuleActions";

const { post, remove, snack } = vi.hoisted(() => ({
  post: vi.fn(), remove: vi.fn(), snack: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, post, delete: remove } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: snack }));

const RULES = {
  rules: [
    {
      name: "pii-detection",
      stage: "pre",
      mode: "sync",
      action: "block",
      threshold: 0.5,
      enabled: true,
      config: { entities: ["email"] },
    },
    {
      name: "lakera-guard",
      action: "warn",
      threshold: 0.7,
      enabled: true,
      config: { provider: "lakera", api_key: "__encrypted__" },
      _policy: "policy-1",
    },
  ],
};

function wrapper({ children }) {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
wrapper.propTypes = { children: PropTypes.node };

const setup = (saved) =>
  renderHook(() => useRuleActions({ gatewayId: "gw-1", saved }), { wrapper })
    .result;

const lastToast = () => snack.mock.calls.at(-1);

describe("useRuleActions", () => {
  beforeEach(() => {
    post.mockReset();
    remove.mockReset();
    remove.mockResolvedValue({
      data: { result: { version: 9, gateway_synced: true } },
    });
    snack.mockReset();
    post.mockResolvedValue({
      data: { result: { version: 9, gateway_synced: true } },
    });
  });

  it("saves one rule through update-guardrail, keeping its credential policy", async () => {
    const actions = setup(RULES);
    const checks = normalizeGuardrails(RULES).checks;
    await act(() =>
      actions.current.saveRule({
        catalogName: "lakera-guard",
        meta: catalogEntry("lakera-guard"),
        check: checks["lakera-guard"],
        form: {
          enabled: true,
          action: "block",
          threshold: 0.9,
          fields: { api_key: "", endpoint: "", categories: [] },
        },
      }),
    );
    expect(post).toHaveBeenCalledTimes(1);
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/agentcc/gateways/gw-1/update-guardrail/");
    expect(body).toEqual({
      name: "lakera-guard",
      config: expect.objectContaining({
        action: "block",
        threshold: 0.9,
        _policy: "policy-1",
        config: { provider: "lakera", api_key: "__encrypted__" },
      }),
    });
    expect(lastToast()).toEqual(["Lakera Guard saved", { variant: "success" }]);
  });

  it("says so when the gateway has not picked the change up", async () => {
    post.mockResolvedValue({
      data: {
        result: {
          gateway_synced: false,
          gateway_warning: "Config saved but gateway sync failed.",
        },
      },
    });
    const actions = setup(RULES);
    await act(() =>
      actions.current.toggleRule(
        {
          id: "pii-detection",
          ruleName: "pii-detection",
          label: "PII Detection",
        },
        false,
      ),
    );
    expect(post.mock.calls[0]).toEqual([
      "/agentcc/gateways/gw-1/toggle-guardrail/",
      { name: "pii-detection", enabled: false },
      expect.any(Object),
    ]);
    expect(lastToast()).toEqual([
      "Saved, but the gateway did not apply it: Config saved but gateway sync failed.",
      { variant: "warning" },
    ]);
  });

  // An edit Django stored but the gateway did not apply: one toast, the
  // warning; never "saved", never "Could not save".
  it("says so when the gateway has not applied an edited rule, and never that it was saved", async () => {
    post.mockResolvedValue({
      data: {
        result: {
          gateway_synced: false,
          gateway_warning: "Config saved but gateway sync failed.",
        },
      },
    });
    const actions = setup(RULES);
    const checks = normalizeGuardrails(RULES).checks;
    // It rejects (the drawer stays open and shows why) after its one warning.
    await act(() =>
      expect(
        actions.current.saveRule({
          catalogName: "pii-detection",
          meta: catalogEntry("pii-detection"),
          check: checks["pii-detection"],
          form: { enabled: true, action: "warn", threshold: 0.5, fields: {} },
        }),
      ).rejects.toThrow(
        "Saved, but the gateway did not apply it: Config saved but gateway sync failed.",
      ),
    );
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/update-guardrail/",
    );
    expect(snack.mock.calls).toEqual([
      [
        "Saved, but the gateway did not apply it: Config saved but gateway sync failed.",
        { variant: "warning" },
      ],
    ]);
  });

  // toggle-guardrail switches a check where the section stores it: a
  // checks list in the policy that wrote it, a checks map in the map.
  it("switches a check of a section stored as a checks list by its stored name", async () => {
    const saved = {
      checks: [
        {
          name: "pii-detector",
          enabled: true,
          action: "block",
          threshold: 0.6,
          config: {},
          _policy: "policy-pii",
        },
      ],
    };
    const actions = setup(saved);
    await act(() =>
      actions.current.toggleRule(
        {
          id: "pii-detection",
          ruleName: "pii-detector",
          label: "PII Detection",
        },
        false,
      ),
    );
    expect(post.mock.calls[0]).toEqual([
      "/agentcc/gateways/gw-1/toggle-guardrail/",
      { name: "pii-detector", enabled: false },
      expect.any(Object),
    ]);
  });

  it("edits a check of a checks map in the map: only the keys the form changed", async () => {
    const saved = {
      checks: {
        "pii-detection": {
          enabled: true,
          action: "mask",
          confidence_threshold: 0.6,
        },
      },
    };
    const actions = setup(saved);
    await act(() =>
      actions.current.saveRule({
        catalogName: "pii-detection",
        meta: catalogEntry("pii-detection"),
        check: saved.checks["pii-detection"],
        form: { enabled: true, action: "mask", threshold: 0.4, fields: {} },
      }),
    );
    expect(post.mock.calls[0]).toEqual([
      "/agentcc/gateways/gw-1/update-guardrail/",
      { name: "pii-detection", config: { confidence_threshold: 0.4 } },
      expect.any(Object),
    ]);
  });

  it("sends nothing for a checks-map edit that changes nothing, and says so", async () => {
    const saved = {
      checks: { "pii-detection": { enabled: true, action: "mask" } },
    };
    const actions = setup(saved);
    await act(() =>
      actions.current.saveRule({
        catalogName: "pii-detection",
        meta: catalogEntry("pii-detection"),
        check: saved.checks["pii-detection"],
        form: { enabled: true, action: "mask", threshold: 0, fields: {} },
      }),
    );
    expect(post).not.toHaveBeenCalled();
    expect(lastToast()).toEqual(["No changes to save", { variant: "info" }]);
  });

  it("reports each warning a write comes back with", async () => {
    post.mockResolvedValue({
      data: {
        result: {
          gateway_synced: true,
          warnings: [{ message: "Check timeout capped at 30000 ms." }],
        },
      },
    });
    const actions = setup(RULES);
    await act(() => actions.current.saveTimeout(90000));
    expect(lastToast()).toEqual([
      "Check timeout capped at 30000 ms.",
      { variant: "warning" },
    ]);
  });

  it("removes a rule by saving the section without it", async () => {
    const actions = setup(RULES);
    await act(() =>
      actions.current.removeRule({
        id: "pii-detection",
        ruleName: "pii-detection",
        label: "PII Detection",
      }),
    );
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/agentcc/gateways/gw-1/update-config/");
    // A guardrails-only patch: nothing else in the org config is sent.
    expect(Object.keys(body)).toEqual(["guardrails"]);
    expect(body.guardrails.rules.map((rule) => rule.name)).toEqual([
      "lakera-guard",
    ]);
    expect(body.guardrails.rules[0]._policy).toBe("policy-1");
    expect(lastToast()).toEqual([
      "PII Detection removed",
      { variant: "success" },
    ]);
  });

  // Removing a policy-sync check keeps the list and every other entry's
  // exact stored fields, including its encrypted credential policy.
  it("removes only the selected check from a stored checks list", async () => {
    const saved = {
      enabled: true,
      checks: [
        {
          name: "pii-detector",
          enabled: true,
          action: "block",
          threshold: 0.6,
          config: { entities: ["EMAIL_ADDRESS"] },
          _policy: "policy-pii",
        },
        {
          name: "openai-moderation",
          enabled: true,
          action: "warn",
          threshold: 0.9,
          config: { api_key: "__encrypted__" },
          _policy: "policy-mod",
        },
      ],
    };
    const actions = setup(saved);
    await act(() =>
      actions.current.removeRule({
        id: "pii-detection",
        ruleName: "pii-detector",
        label: "PII Detection",
      }),
    );
    // Address only this stored check. The server, not a stale projection,
    // preserves the sibling and removes the selected check from its policies.
    expect(remove.mock.calls).toEqual([[
      "/agentcc/gateways/gw-1/update-guardrail/",
      { data: { name: "pii-detector" }, timeout: 30000 },
    ]]);
    expect(post).not.toHaveBeenCalled();
    expect(saved.checks).toHaveLength(2);
    expect(saved.checks[1]._policy).toBe("policy-mod");
    expect(lastToast()).toEqual(["PII Detection removed", { variant: "success" }]);
  });

  it("does not claim a policy-backed removal applied when the push failed", async () => {
    remove.mockResolvedValue({ data: { result: {
      gateway_synced: false,
      gateway_warning: "Config saved but gateway sync failed.",
    } } });
    const actions = setup({ checks: [{ name: "topic-restriction", _policy: "policy-1" }] });
    await act(() => expect(actions.current.removeRule({
      id: "topic-restriction", ruleName: "topic-restriction", label: "Topic Restriction",
    })).rejects.toThrow("Saved, but the gateway did not apply it"));
    expect(snack.mock.calls).toEqual([[
      "Saved, but the gateway did not apply it: Config saved but gateway sync failed.",
      { variant: "warning" },
    ]]);
    expect(post).not.toHaveBeenCalled();
  });

  it("reports a rejected policy removal without a success toast or projection write", async () => {
    remove.mockRejectedValue({ message: "Permission denied", statusCode: 403 });
    const actions = setup({ checks: [{ name: "topic-restriction", _policy: "policy-1" }] });
    await act(() => expect(actions.current.removeRule({
      id: "topic-restriction", ruleName: "topic-restriction", label: "Topic Restriction",
    })).rejects.toBeTruthy());
    expect(post).not.toHaveBeenCalled();
    expect(snack).toHaveBeenCalledTimes(1);
    expect(lastToast()[1]).toEqual({ variant: "error" });
  });

  it("saves the check timeout on its own", async () => {
    const actions = setup(RULES);
    await act(() => actions.current.saveTimeout(3000));
    expect(post.mock.calls[0][0]).toBe("/agentcc/gateways/gw-1/update-config/");
    expect(post.mock.calls[0][1]).toEqual({ guardrails: { timeout_ms: 3000 } });
  });

  it("shows the server's reason when a save is refused", async () => {
    post.mockRejectedValue({
      message: "guardrail_policy_not_in_org",
      statusCode: 400,
    });
    const actions = setup(RULES);
    await act(async () => {
      await expect(
        actions.current.toggleRule(
          {
            id: "pii-detection",
            ruleName: "pii-detection",
            label: "PII Detection",
          },
          true,
        ),
      ).rejects.toBeTruthy();
    });
    expect(lastToast()[1]).toEqual({ variant: "error" });
    expect(snack.mock.calls.some(([, o]) => o.variant === "success")).toBe(
      false,
    );
  });
});
