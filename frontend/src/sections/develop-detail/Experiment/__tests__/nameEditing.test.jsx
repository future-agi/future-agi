import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ui, mountDrawer } from "./createAdmissionDrawer.fixture";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: vi.fn() },
  endpoints: {
    develop: { experiment: { create: () => "/model-hub/experiments/v2/" } },
  },
}));
beforeEach(() => {
  ui.emptyDefaults = true;
  ui.store.currentStep = 0;
  ui.suggestion = {};
  ui.columns = [];
  ui.datasetEvals = [];
  ui.sampleRows = [];
  ui.nameAvailable = true;
});
afterEach(() => {
  cleanup();
  ui.emptyDefaults = false;
  ui.store.currentStep = 3;
  ui.suggestion = {};
});

describe("experiment name editing", () => {
  it("does not insert a late suggestion while the focused name is being replaced", async () => {
    const { rerender, view } = mountDrawer();
    const name = screen.getByRole("textbox", { name: "Experiment name" });
    fireEvent.focus(name);
    fireEvent.change(name, { target: { value: "" } });
    ui.suggestion = { suggestedName: "Arrived during typing" };
    await act(async () => rerender(view()));
    expect(name).toHaveValue("");
    fireEvent.change(name, { target: { value: "My graph experiment" } });
    expect(name).toHaveValue("My graph experiment");
  });

  it("still supplies a late suggestion before the name is focused", async () => {
    const { rerender, view } = mountDrawer();
    ui.suggestion = { suggestedName: "Untouched default" };
    await act(async () => rerender(view()));
    await waitFor(() =>
      expect(
        screen.getByRole("textbox", { name: "Experiment name" }),
      ).toHaveValue("Untouched default"),
    );
  });
});
