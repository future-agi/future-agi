import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

import ManagedModelsCard from "./ManagedModelsCard";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post, get: vi.fn() },
  endpoints: {
    gateway: {
      updateConfig: (id) => `/gateway/${id}/config/update`,
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const STATUS = {
  available: true,
  enabled: true,
  balance_usd: 20,
  markup_percent: 5,
};
const SWITCH = "Future AGI credits";

const renderCard = (props = {}) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          mutationCache: new MutationCache({ onError: () => {} }),
          defaultOptions: { mutations: { retry: false } },
        })
      }
    >
      <ManagedModelsCard
        status={STATUS}
        gatewayId="gw-1"
        canWrite
        onRetry={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );

beforeEach(() => {
  post.mockReset();
});

// The console can turn Future AGI credits off, but it has
// no control that turns them back on. Each surface says so and names the real
// way back, never a switch that is not there.
describe("Future AGI credits turn off one way", () => {
  it("warns before turning off that the console cannot turn them back on", async () => {
    renderCard();
    await userEvent.click(screen.getByLabelText(SWITCH));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(
      "You cannot turn Future AGI credits back on from the console",
    );
    expect(dialog).toHaveTextContent(
      /to turn them on again, contact your Future AGI account team/,
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Cancel" }),
    );
    expect(post).not.toHaveBeenCalled();
  });

  it("tells a member that an Owner or Admin can turn credits off, not on", () => {
    renderCard({ canWrite: false });
    expect(
      screen.getByText(
        /only an organization Owner or Admin can turn off Future AGI credits/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/on or off/)).toBeNull();
  });

  it("gives an Owner or Admin the real next step when credits are off", () => {
    renderCard({ status: { ...STATUS, enabled: false } });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(
      "Future AGI credits are off for this organization.",
    );
    expect(alert).toHaveTextContent(
      "To turn them on, contact your Future AGI account team.",
    );
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("sends a member to an Owner or Admin when credits are off", () => {
    renderCard({ status: { ...STATUS, enabled: false }, canWrite: false });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "To turn them on, ask an organization Owner or Admin to contact your Future AGI account team.",
    );
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});
