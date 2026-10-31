import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useConnectWindow } from "../connectWindow";

const post = (data, origin = window.location.origin) => {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data, origin }));
  });
};

describe("useConnectWindow", () => {
  let openSpy;
  beforeEach(() => {
    openSpy = vi
      .spyOn(window, "open")
      .mockReturnValue({ closed: false, focus: vi.fn(), close: vi.fn() });
  });
  afterEach(() => openSpy.mockRestore());

  it("hands the provider window's reply to onResult", () => {
    const onResult = vi.fn();
    const { result } = renderHook(() => useConnectWindow(onResult));
    act(() => result.current.open("/integrations-connect/github-install"));
    post({ type: "fagi-proto-connect", provider: "github", outcome: "allow" });
    expect(onResult).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "github", outcome: "allow" }),
    );
  });

  it("ignores messages of another type or from another origin", () => {
    const onResult = vi.fn();
    const { result } = renderHook(() => useConnectWindow(onResult));
    act(() => result.current.open("/integrations-connect/github-install"));
    post({ type: "something-else", provider: "github", outcome: "allow" });
    post(
      { type: "fagi-proto-connect", provider: "github", outcome: "allow" },
      "https://evil.example",
    );
    expect(onResult).not.toHaveBeenCalled();
  });

  it("reports a blocked popup", () => {
    openSpy.mockReturnValue(null);
    const { result } = renderHook(() => useConnectWindow(vi.fn()));
    act(() => result.current.open("/integrations-connect/github-install"));
    expect(result.current.status).toBe("blocked");
  });
});
