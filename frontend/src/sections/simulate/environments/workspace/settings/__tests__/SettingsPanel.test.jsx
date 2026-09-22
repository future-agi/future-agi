import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import SettingsPanel from "../SettingsPanel";

const env = { id: "env-1" };
// A scenarios list so the seeded version history derives real counts.
const envState = { scenarios: [{ id: "s1" }, { id: "s2" }] };

// SettingsPanel now uses a react-query mutation (§8 rename), so it needs a client.
const withClient = (ui) => (
  <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
);

function renderPanel(overrides = {}) {
  const patch = vi.fn();
  render(withClient(<SettingsPanel env={env} envState={envState} patch={patch} {...overrides} />));
  return { patch };
}

describe("SettingsPanel — New version flow", () => {
  it("opens the change-kind picker, enables Create on a selection, and patches a new version", () => {
    const { patch } = renderPanel();

    // Picker is closed until New version is clicked.
    expect(screen.queryByText("What changed in the world?")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /New version/ }));
    expect(screen.getByText("What changed in the world?")).toBeInTheDocument();

    // Create is disabled until at least one change kind is ticked.
    const create = screen.getByRole("button", { name: /^Create v/ });
    expect(create).toBeDisabled();

    fireEvent.click(screen.getByText("Reseeded the world"));
    fireEvent.click(screen.getByText("Rules changed"));
    expect(create).toBeEnabled();

    fireEvent.click(create);

    expect(patch).toHaveBeenCalledTimes(1);
    const arg = patch.mock.calls[0][0];
    expect(Array.isArray(arg.envVersions)).toBe(true);
    // Seeded history was reversed to oldest-first before appending.
    expect(arg.envVersions[0].label).toBe("v1");
    const minted = arg.envVersions[arg.envVersions.length - 1];
    expect(minted.label).toBe("v4");
    expect(minted.changed).toEqual(expect.arrayContaining(["seed", "rules"]));
    expect(arg.activeEnvVersion).toBe(minted.label);
  });

  it("disables the New version control when locked", () => {
    renderPanel({ locked: true });

    expect(screen.getByRole("button", { name: /New version/ })).toBeDisabled();
  });
});
