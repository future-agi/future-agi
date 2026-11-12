import React from "react";
import { expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import useProductionImportRefresh from "../useProductionImportRefresh";
import { notifyProductionImport } from "../productionImportEvents";

it("refreshes only the mounted destination and removes its listener on close", () => {
  const refresh = vi.fn();
  function Listener() {
    useProductionImportRefresh(refresh);
    return null;
  }
  const { unmount } = render(
    <MemoryRouter initialEntries={["/datasets/owned"]}>
      <Routes>
        <Route path="/datasets/:dataset" element={<Listener />} />
      </Routes>
    </MemoryRouter>,
  );
  act(() => notifyProductionImport("other"));
  expect(refresh).not.toHaveBeenCalled();
  act(() => notifyProductionImport("owned"));
  expect(refresh).toHaveBeenCalledOnce();
  unmount();
  act(() => notifyProductionImport("owned"));
  expect(refresh).toHaveBeenCalledOnce();
});
