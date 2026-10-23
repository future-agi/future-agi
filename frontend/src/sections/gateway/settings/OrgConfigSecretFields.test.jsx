import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import CacheConfigTab from "./CacheConfigTab";
import A2AConfigTab from "./A2AConfigTab";
import AuditConfigTab from "./AuditConfigTab";

// What every org-config read returns for a stored secret: the mask,
// never the value. The Org Config editor's tabs show it as dots with Replace
// and Remove, never as an editable value, so a save sends the mask or a
// blank (keep), a newly typed value (replace) or null (remove).
const MASK = "__encrypted__0123456789abcdef";

function expectNoMaskShown(container) {
  for (const input of container.querySelectorAll("input, textarea")) {
    expect(input.value).not.toContain("__encrypted__");
  }
  expect(container.textContent).not.toContain("__encrypted__");
}

function expectStoredSecretField(label) {
  const input = screen.getByLabelText(label);
  expect(input).toHaveAttribute("readonly");
  expect(input.value).toMatch(/•+/);
}

describe("CacheConfigTab with stored secrets", () => {
  it.each([
    ["redis", "redis", { address: "r:6379", password: MASK }, "Password"],
    [
      "s3",
      "s3",
      { bucket: "b", access_key_id: "AKIA", secret_access_key: MASK },
      "Secret Access Key",
    ],
    [
      "azure-blob",
      "azure_blob",
      { container: "c", connection_string: MASK },
      "Connection String",
    ],
    [
      "azure-blob",
      "azure_blob",
      { container: "c", sas_token: MASK },
      "SAS Token (alternative)",
    ],
  ])(
    "%s shows %s's stored secret as dots with Replace and Remove",
    async (backend, key, backendConfig, label) => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      const field = Object.keys(backendConfig).find(
        (k) => backendConfig[k] === MASK,
      );
      const cache = { enabled: true, backend, [key]: backendConfig };
      const { container } = render(
        <CacheConfigTab cache={cache} onChange={onChange} />,
      );
      await user.click(screen.getByText("L1 Backend (Exact Match)"));

      expectStoredSecretField(label);
      expectNoMaskShown(container);

      await user.click(screen.getByRole("button", { name: /replace/i }));
      expect(onChange).toHaveBeenLastCalledWith({
        ...cache,
        [key]: { ...backendConfig, [field]: "" },
      });
      await user.click(screen.getByRole("button", { name: /remove/i }));
      expect(onChange).toHaveBeenLastCalledWith({
        ...cache,
        [key]: { ...backendConfig, [field]: null },
      });
    },
  );

  it.each(["qdrant", "weaviate", "pinecone"])(
    "the semantic cache's %s API key reads as dots with Replace and Remove",
    async (backend) => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      const cache = {
        enabled: true,
        semantic: {
          enabled: true,
          backend,
          [backend]: { url: "https://v", api_key: MASK },
        },
      };
      const { container } = render(
        <CacheConfigTab cache={cache} onChange={onChange} />,
      );
      await user.click(screen.getByText("L2 Semantic Cache"));

      expectStoredSecretField("API Key");
      expectNoMaskShown(container);

      await user.click(screen.getByRole("button", { name: /replace/i }));
      expect(onChange).toHaveBeenLastCalledWith({
        ...cache,
        semantic: {
          ...cache.semantic,
          [backend]: { url: "https://v", api_key: "" },
        },
      });
    },
  );

  it("a field with nothing stored stays an editable password input", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <CacheConfigTab
        cache={{ enabled: true, backend: "redis", redis: { address: "r" } }}
        onChange={onChange}
      />,
    );
    await user.click(screen.getByText("L1 Backend (Exact Match)"));

    const input = screen.getByLabelText("Password");
    expect(input).toHaveAttribute("type", "password");
    expect(input).not.toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: /replace/i })).toBeNull();
  });
});

describe("A2AConfigTab with a stored agent token", () => {
  it.each(["bearer", "api_key"])(
    "a %s agent's token reads as dots; Replace sends blank, Remove null",
    async (type) => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      const agent = { url: "https://a/x", auth: { type, token: MASK } };
      const a2a = { enabled: true, agents: { billing: agent } };
      const { container } = render(
        <A2AConfigTab a2a={a2a} onChange={onChange} />,
      );

      expectStoredSecretField("Token");
      expectNoMaskShown(container);

      await user.click(screen.getByRole("button", { name: /replace/i }));
      expect(onChange).toHaveBeenLastCalledWith({
        ...a2a,
        agents: { billing: { ...agent, auth: { type, token: "" } } },
      });
      await user.click(screen.getByRole("button", { name: /remove/i }));
      expect(onChange).toHaveBeenLastCalledWith({
        ...a2a,
        agents: { billing: { ...agent, auth: { type, token: null } } },
      });
    },
  );
});

describe("AuditConfigTab with a stored sink header", () => {
  it("shows a stored header's name, never its mask", () => {
    const audit = {
      enabled: true,
      sinks: [
        {
          type: "webhook",
          url: "https://siem/in",
          headers: { Authorization: MASK, "X-Team": "ops" },
        },
      ],
    };
    const { container } = render(
      <AuditConfigTab audit={audit} onChange={vi.fn()} />,
    );

    expect(screen.getByText(/^Authorization: •+$/)).toBeInTheDocument();
    expect(screen.getByText("X-Team: ops")).toBeInTheDocument();
    expectNoMaskShown(container);
  });
});
