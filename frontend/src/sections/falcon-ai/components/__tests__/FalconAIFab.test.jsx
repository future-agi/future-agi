import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import FalconAIFab from "../FalconAIFab";
import useFalconStore from "../../store/useFalconStore";

const capability = { allowed: false, isLoading: false, isError: false };
vi.mock("src/hooks/useCapabilities", () => ({
  useFeatureAllowed: () => capability,
}));

vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));

function Location() {
  return <p data-testid="location">{useLocation().pathname}</p>;
}

function renderFab(path = "/dashboard/gateway/overview") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <FalconAIFab />
      <Routes>
        <Route path="*" element={<Location />} />
      </Routes>
    </MemoryRouter>,
  );
}

function pressCmdK() {
  const event = new KeyboardEvent("keydown", {
    key: "k",
    metaKey: true,
    bubbles: true,
    cancelable: true,
  });
  window.dispatchEvent(event);
  return event;
}

describe("FalconAIFab (the Falcon AI Cmd-K launcher)", () => {
  beforeEach(() => {
    capability.allowed = false;
    capability.isLoading = false;
    capability.isError = false;
    useFalconStore.setState({ isSidebarOpen: false });
  });

  it("is hidden when falcon_ai is not allowed (OSS), and Cmd+K is left alone", () => {
    renderFab();
    expect(screen.queryByRole("button", { name: "Open Falcon AI" })).toBeNull();
    const event = pressCmdK();
    expect(event.defaultPrevented).toBe(false);
    expect(useFalconStore.getState().isSidebarOpen).toBe(false);
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/gateway/overview",
    );
  });

  it("is hidden while capabilities load or fail", () => {
    capability.isLoading = true;
    const { unmount } = renderFab();
    expect(screen.queryByRole("button", { name: "Open Falcon AI" })).toBeNull();
    unmount();
    capability.isLoading = false;
    capability.isError = true;
    renderFab();
    expect(screen.queryByRole("button", { name: "Open Falcon AI" })).toBeNull();
  });

  it("shows the launcher when falcon_ai is allowed, and Cmd+K opens the sidebar", () => {
    capability.allowed = true;
    renderFab();
    expect(
      screen.getByRole("button", { name: "Open Falcon AI" }),
    ).toBeInTheDocument();
    const event = pressCmdK();
    expect(event.defaultPrevented).toBe(true);
    expect(useFalconStore.getState().isSidebarOpen).toBe(true);
  });

  it("opens the sidebar on click when allowed", () => {
    capability.allowed = true;
    renderFab();
    fireEvent.click(screen.getByRole("button", { name: "Open Falcon AI" }));
    expect(useFalconStore.getState().isSidebarOpen).toBe(true);
  });
});
