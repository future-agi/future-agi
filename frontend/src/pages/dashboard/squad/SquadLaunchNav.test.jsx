import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import SquadLaunchNav from "./SquadLaunchNav";

describe("Squad launch navigation", () => {
  it("exposes the requested customer surfaces and routes actions to existing panels", async () => {
    const onSurfaceChange = vi.fn();
    const onNewAgent = vi.fn();
    const onImport = vi.fn();
    render(
      <SquadLaunchNav
        onSurfaceChange={onSurfaceChange}
        onNewAgent={onNewAgent}
        onImport={onImport}
      />,
    );
    for (const label of [
      "Chat",
      "Inbox",
      "Explore",
      "Workspace Agents",
      "Templates",
      "Integrations",
      "Skills",
      "Get Started",
      "New agent",
      "Start from a template",
      "Import from a .zip file",
      "Usage",
      "Settings",
    ]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    await userEvent.click(screen.getByRole("link", { name: "Integrations" }));
    expect(onSurfaceChange).toHaveBeenCalledWith("connectors");
    await userEvent.click(screen.getByRole("link", { name: "New agent" }));
    expect(onNewAgent).toHaveBeenCalledOnce();
    await userEvent.click(
      screen.getByRole("link", { name: "Import from a .zip file" }),
    );
    expect(onImport).toHaveBeenCalledOnce();
  });
});
