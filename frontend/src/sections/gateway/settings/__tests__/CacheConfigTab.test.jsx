import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import CacheConfigTab from "../CacheConfigTab";

// The gateway applies an organization's cache on/off and default TTL, and
// nothing else (its own cache serves every org). The tab offers those two;
// a stored backend, its connection, size limits, semantic and edge settings
// are never shown and never dropped.

const STORED = {
  enabled: true,
  default_ttl: "10m",
  max_entries: 20000,
  backend: "redis",
  redis: {
    address: "cache.internal:6379",
    mode: "single",
    password: "hunter2-not-for-screens",
    tls: true,
  },
  semantic: {
    enabled: true,
    threshold: 0.9,
    backend: "qdrant",
    qdrant: { url: "http://qdrant:6333", api_key: "qd-secret-value" },
  },
  edge: { enabled: true, default_ttl: 300, require_opt_in: true },
};

function renderTab(cache = STORED, props = {}) {
  const onChange = vi.fn();
  render(<CacheConfigTab cache={cache} onChange={onChange} {...props} />);
  return onChange;
}

describe("Org config > Cache", () => {
  it("edits on/off and the default TTL, keeping every other stored key", async () => {
    const user = userEvent.setup();
    const onChange = renderTab();
    await user.click(
      screen.getByRole("checkbox", { name: "Enable Response Cache" }),
    );
    expect(onChange).toHaveBeenLastCalledWith({ ...STORED, enabled: false });
    const ttl = screen.getByRole("textbox", { name: "Default TTL" });
    await user.type(ttl, "s");
    expect(onChange).toHaveBeenLastCalledWith({
      ...STORED,
      default_ttl: "10ms",
    });
  });

  it("offers no control and shows no value the gateway does not read", () => {
    renderTab();
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    const text = document.body.textContent;
    for (const stored of [
      "20,000",
      "cache.internal:6379",
      "hunter2-not-for-screens",
      "qd-secret-value",
      "http://qdrant:6333",
    ]) {
      expect(text).not.toContain(stored);
    }
  });

  it("disables both controls when the gateway does not run response caching", () => {
    renderTab(STORED, { disabled: true });
    expect(
      screen.getByRole("checkbox", { name: "Enable Response Cache" }),
    ).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Default TTL" })).toBeDisabled();
  });

  it("shows the TTL field only while the cache is on", () => {
    renderTab({ enabled: false, default_ttl: "5m" });
    expect(screen.queryByRole("textbox", { name: "Default TTL" })).toBeNull();
  });
});
