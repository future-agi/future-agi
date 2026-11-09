// The Falcon AI sidebar below md on Settings (the compact shell): a docked
// 420 px column pushed a 390 px page 30 px sideways and clipped its close
// button (verifier walk on 007d90498). As an overlay it opens over the page,
// never wider than the screen, with its close button inside it.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";

import FalconAISidebar from "../FalconAISidebar";
import useFalconStore from "../store/useFalconStore";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../hooks/useFalconSocket", () => ({
  default: () => ({
    sendChat: vi.fn(),
    sendStop: vi.fn(),
    sendFeedback: vi.fn(),
  }),
}));
vi.mock("../hooks/useFalconContext", () => ({
  useFalconContext: () => ({ page: "settings" }),
}));
vi.mock("../hooks/useFalconAPI", () => ({
  createConversation: vi.fn(),
  listSkills: vi.fn(() => Promise.resolve([])),
  fetchConnectors: vi.fn(() => Promise.resolve([])),
  fetchConversations: vi.fn(() => Promise.resolve({ results: [] })),
  getConversation: vi.fn(),
}));
vi.mock("../components/MessageList", () => ({ default: () => null }));
vi.mock("../components/ChatInput", () => ({ default: () => null }));
vi.mock("../components/SkillPicker", () => ({ default: () => null }));
vi.mock("../components/SkillEditorDialog", () => ({ default: () => null }));

const paper = () =>
  /** @type {HTMLElement} */ (document.querySelector(".MuiDrawer-paper"));

describe("FalconAISidebar as an overlay (compact Settings shell)", () => {
  beforeEach(() => {
    useFalconStore.setState({ isSidebarOpen: true, messages: [] });
  });

  it("opens in a drawer capped at the screen width, with its close button inside", () => {
    render(<FalconAISidebar variant="overlay" />);
    const drawer = paper();
    expect(drawer).not.toBeNull();
    expect(getComputedStyle(drawer).maxWidth).toBe("100vw");
    expect(getComputedStyle(drawer).width).toBe("420px");

    const panel = screen.getByTestId("falcon-ai-sidebar");
    expect(drawer).toContainElement(panel);
    // The docked 420 px floor is what pushed the page sideways.
    expect(getComputedStyle(panel).minWidth).not.toBe("420px");

    const close = within(drawer).getByTitle("Close");
    fireEvent.click(close);
    expect(useFalconStore.getState().isSidebarOpen).toBe(false);
  });

  it("stays a docked 420 px column by default (md and wider, other sections)", () => {
    render(<FalconAISidebar />);
    expect(paper()).toBeNull();
    const panel = screen.getByTestId("falcon-ai-sidebar");
    expect(getComputedStyle(panel).minWidth).toBe("420px");
  });

  it("renders nothing while closed", () => {
    useFalconStore.setState({ isSidebarOpen: false });
    render(<FalconAISidebar variant="overlay" />);
    expect(paper()).toBeNull();
    expect(screen.queryByTestId("falcon-ai-sidebar")).toBeNull();
  });
});
