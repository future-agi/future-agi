import React, { StrictMode } from "react";
import { m } from "framer-motion";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MotionLazy } from "./motion-lazy";

const features = vi.hoisted(() => {
  let resolve!: () => void;
  const ready = new Promise<void>((done) => {
    resolve = done;
  });
  return { ready, resolve: () => resolve(), load: vi.fn(), loaded: vi.fn() };
});

vi.mock("./motion-features", async () => {
  features.load();
  await features.ready;
  const bundle = await vi.importActual("./motion-features");
  features.loaded();
  return bundle;
});

afterEach(cleanup);

describe("MotionLazy", () => {
  it("keeps children mounted and interactive while features load, then animates", async () => {
    const onComplete = vi.fn();
    const view = () => (
      <StrictMode>
        <MotionLazy>
          <input aria-label="Draft" defaultValue="" />
          <m.div
            data-testid="animated"
            initial={{ opacity: 0.5 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0 }}
            onAnimationComplete={onComplete}
          >
            Animated content
          </m.div>
        </MotionLazy>
      </StrictMode>
    );
    const { container, rerender } = render(view());
    const input = screen.getByRole("textbox", { name: "Draft" });
    const animated = screen.getByTestId("animated");
    const wrapper = container.firstElementChild;

    expect(wrapper).toHaveStyle({ height: "100%" });
    expect(animated).toBeVisible();
    expect(animated).toHaveStyle({ opacity: "0.5" });
    fireEvent.change(input, { target: { value: "Keep this draft" } });
    await waitFor(() => expect(features.load).toHaveBeenCalledOnce());
    expect(features.loaded).not.toHaveBeenCalled();

    await act(async () => {
      features.resolve();
      await features.ready;
    });
    await waitFor(() => expect(features.loaded).toHaveBeenCalledOnce());
    await waitFor(() => expect(onComplete).toHaveBeenCalled());
    expect(animated).toHaveStyle({ opacity: "1" });
    expect(container.firstElementChild).toBe(wrapper);
    expect(screen.getByRole("textbox", { name: "Draft" })).toBe(input);
    expect(input).toHaveValue("Keep this draft");

    rerender(view());
    expect(features.load).toHaveBeenCalledOnce();
    expect(container.firstElementChild).toBe(wrapper);
  });
});
