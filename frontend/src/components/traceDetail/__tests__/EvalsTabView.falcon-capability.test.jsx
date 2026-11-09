import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import EvalsTabView from "../EvalsTabView";
import { openFixWithFalcon } from "src/sections/falcon-ai/helpers/openFixWithFalcon";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
const h = vi.hoisted(() => ({
  answer: { allowed: true, isLoading: false, isError: false },
}));
vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: { FALCON_AI: "falcon_ai" },
  useFeatureAllowed: () => h.answer,
}));
const ev = {
  id: "e1",
  eval_name: "Failed accuracy",
  score: 0,
  explanation: "Needs correction",
  spanId: "s1",
};
beforeEach(() => {
  h.answer = { allowed: true, isLoading: false, isError: false };
  useFalconStore.setState({ isSidebarOpen: false, pendingPrompt: null });
});
describe("routed Fix with Falcon capability", () => {
  it.each([
    { allowed: false, isLoading: false, isError: false },
    { allowed: false, isLoading: true, isError: false },
    { allowed: true, isLoading: false, isError: true },
  ])("hides summary and expanded controls for %j", (answer) => {
    h.answer = answer;
    const fix = vi.fn();
    render(
      <EvalsTabView
        evals={[ev]}
        onFixWithFalcon={fix}
        showErrorLocalization={false}
      />,
    );
    fireEvent.click(screen.getByText(ev.eval_name));
    expect(
      screen.queryByRole("button", { name: "Fix with Falcon" }),
    ).toBeNull();
    expect(fix).not.toHaveBeenCalled();
    expect(useFalconStore.getState().isSidebarOpen).toBe(false);
  });
  it("offers no placeholder action when its caller supplied no handler", () => {
    render(<EvalsTabView evals={[ev]} showErrorLocalization={false} />);
    fireEvent.click(screen.getByText(ev.eval_name));
    expect(
      screen.queryByRole("button", { name: "Fix with Falcon" }),
    ).toBeNull();
  });
  it("summary and expanded actions open the actual Falcon store and supply the intended prompt", () => {
    const fix = vi.fn(({ level, ev: selected, failingEvals }) =>
      openFixWithFalcon({
        level,
        context: { eval: selected?.eval_name || failingEvals[0].eval_name },
      }),
    );
    render(
      <EvalsTabView
        evals={[ev]}
        onFixWithFalcon={fix}
        showErrorLocalization={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Fix with Falcon" }));
    expect(fix).toHaveBeenLastCalledWith({
      level: "span",
      failingEvals: [ev],
      allEvals: [ev],
    });
    expect(useFalconStore.getState()).toMatchObject({
      isSidebarOpen: true,
      pendingPrompt: expect.stringContaining("/fix-with-falcon"),
    });
    expect(useFalconStore.getState().pendingPrompt).toContain(ev.eval_name);
    fireEvent.click(screen.getByText(ev.eval_name));
    fireEvent.click(
      screen.getAllByRole("button", { name: "Fix with Falcon" })[1],
    );
    expect(fix).toHaveBeenLastCalledWith({ level: "eval", ev });
    expect(useFalconStore.getState().pendingPrompt).toContain(
      "Fix this failing evaluation.",
    );
  });
});
