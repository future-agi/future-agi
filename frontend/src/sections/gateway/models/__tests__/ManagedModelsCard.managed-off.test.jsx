import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import ManagedModelsCard from "../ManagedModelsCard";

vi.mock("../hooks/useManagedModels", () => ({
  useSetManagedModelsEnabled: () => ({ mutate: vi.fn(), isPending: false }),
}));

const status = {
  available: true,
  enabled: false,
  balance_usd: 25,
  markup_percent: 5,
};

describe("F2 organization-disabled managed card", () => {
  it.each([false, undefined])(
    "states the off state without credit controls when enabled is %s",
    (enabled) => {
      render(
        <ManagedModelsCard
          status={{ ...status, enabled }}
          canWrite
          gatewayId="default"
        />,
      );
      expect(
        screen.getByText("Future AGI credits are off for this organization."),
      ).toBeInTheDocument();
      expect(
        screen.queryByText(/one key for every model|Call credit-eligible/),
      ).toBeNull();
      expect(screen.queryByText("Credit balance")).toBeNull();
      expect(screen.queryByRole("checkbox")).toBeNull();
    },
  );

  it("offers eligible models, not every model, to an enabled organization", () => {
    render(
      <ManagedModelsCard
        status={{ ...status, enabled: true }}
        canWrite
        gatewayId="default"
      />,
    );
    expect(screen.getByText("Future AGI credits")).toBeInTheDocument();
    expect(
      screen.getByText(/Call credit-eligible catalog models/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/one key for every model/)).toBeNull();
    expect(screen.getByRole("checkbox")).toBeChecked();
  });
});
