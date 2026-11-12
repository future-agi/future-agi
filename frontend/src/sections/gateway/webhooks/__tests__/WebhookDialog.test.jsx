import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import CreateEditWebhookDialog from "../CreateEditWebhookDialog";

function renderDialog(props = {}) {
  const onSubmit = vi.fn();
  render(
    <CreateEditWebhookDialog
      open
      onClose={vi.fn()}
      onSubmit={onSubmit}
      {...props}
    />,
  );
  return onSubmit;
}

describe("Create webhook dialog", () => {
  // The gateway sends every event the console offers: request logging sends
  // budget.exceeded, and a finished batch job sends batch.completed once. A
  // new webhook offers all five, and an edited one shows each stored event
  // as a chosen option.
  it("offers every event this gateway sends on a new webhook", () => {
    renderDialog();
    expect(
      screen
        .getAllByRole("checkbox")
        .map((box) => box.closest("label")?.textContent)
        .filter((text) => /\./.test(text || "")),
    ).toEqual([
      expect.stringContaining("request.completed"),
      expect.stringContaining("error.occurred"),
      expect.stringContaining("guardrail.triggered"),
      expect.stringContaining("budget.exceeded"),
      expect.stringContaining("batch.completed"),
    ]);
    expect(
      screen.getByRole("checkbox", { name: /Budget exceeded/ }),
    ).not.toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: /Batch completed/ }),
    ).not.toBeChecked();
    expect(screen.queryByText("Not enforced on this gateway")).toBeNull();
    expect(screen.queryByTestId("webhook-unsent-events")).toBeNull();
  });

  it("shows stored budget.exceeded and batch.completed as chosen, and keeps them on save", async () => {
    const onSubmit = renderDialog({
      webhook: {
        id: "wh-2",
        name: "ops",
        url: "https://hooks.example.com/ops",
        events: ["error.occurred", "budget.exceeded", "batch.completed"],
        is_active: true,
        headers: {},
      },
    });
    expect(
      screen.getByRole("checkbox", { name: /Budget exceeded/ }),
    ).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: /Batch completed/ }),
    ).toBeChecked();
    expect(screen.queryByTestId("webhook-unsent-events")).toBeNull();
    expect(screen.queryByText(/never sends/)).toBeNull();

    await userEvent.click(
      screen.getByRole("checkbox", { name: /Request completed/ }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSubmit.mock.lastCall[0].events).toEqual([
      "error.occurred",
      "budget.exceeded",
      "batch.completed",
      "request.completed",
    ]);
  });

  it("does not warn that a webhook with only budget.exceeded is never called", () => {
    renderDialog({
      webhook: {
        id: "wh-3",
        name: "budget",
        url: "https://hooks.example.com/budget",
        events: ["budget.exceeded"],
        is_active: true,
        headers: {},
      },
    });
    expect(
      screen.getByRole("checkbox", { name: /Budget exceeded/ }),
    ).toBeChecked();
    expect(screen.queryByText(/never called/)).not.toBeInTheDocument();
  });

  // RL-917: the event is always offered. Existing subscriptions are preserved
  // unless explicitly unchecked, including while guardrails are off.
  it.each([true, false])(
    "offers a new guardrail subscription while off=%s",
    async (guardrailsOff) => {
      const onSubmit = renderDialog({ guardrailsOff });
      const checkbox = screen.getByRole("checkbox", {
        name: /Guardrail triggered/,
      });
      expect(checkbox).toBeEnabled();
      expect(
        screen.getAllByText(
          "guardrail.triggered · A guardrail flagged or blocked a request. Only requests handled while guardrails are on produce this event.",
        ),
      ).toHaveLength(1);
      await userEvent.type(screen.getByLabelText(/Name/), "guards");
      await userEvent.type(
        screen.getByLabelText(/^URL/),
        "https://hooks.example.com/guards",
      );
      await userEvent.click(checkbox);
      await userEvent.click(screen.getByRole("button", { name: "Create" }));
      expect(onSubmit.mock.lastCall[0].events).toEqual(["guardrail.triggered"]);
    },
  );
  it("keeps a subscribed Guardrail triggered event on save while guardrails are off", async () => {
    const onSubmit = renderDialog({
      guardrailsOff: true,
      webhook: {
        id: "wh-4",
        name: "guards",
        url: "https://hooks.example.com/guards",
        events: ["error.occurred", "guardrail.triggered"],
        is_active: true,
        headers: {},
      },
    });
    expect(
      screen.getByRole("checkbox", { name: /Guardrail triggered/ }),
    ).toBeChecked();
    expect(
      screen.getAllByText(
        "guardrail.triggered · A guardrail flagged or blocked a request. Only requests handled while guardrails are on produce this event.",
      ),
    ).toHaveLength(1);

    await userEvent.click(
      screen.getByRole("checkbox", { name: /Request completed/ }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSubmit.mock.lastCall[0].events).toEqual([
      "error.occurred",
      "guardrail.triggered",
      "request.completed",
    ]);
  });

  it("lets an existing webhook unsubscribe from guardrails while the node is off", async () => {
    const onSubmit = renderDialog({
      guardrailsOff: true,
      webhook: {
        id: "wh-off",
        name: "guards",
        url: "https://hooks.example.com/guards",
        events: ["error.occurred", "guardrail.triggered"],
        is_active: true,
        headers: {},
      },
    });
    await userEvent.click(
      screen.getByRole("checkbox", { name: /Guardrail triggered/ }),
    );
    expect(
      screen.getByRole("checkbox", { name: /Guardrail triggered/ }),
    ).not.toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSubmit.mock.lastCall[0].events).toEqual(["error.occurred"]);
  });

  it("explains the signature the secret produces", () => {
    renderDialog();
    expect(screen.getByText(/X-Agentcc-Signature/)).toBeInTheDocument();
  });

  it("needs an http(s) URL before it creates", async () => {
    const onSubmit = renderDialog();
    await userEvent.type(screen.getByLabelText(/Name/), "ops");
    await userEvent.type(screen.getByLabelText(/^URL/), "hooks.example.com");
    await userEvent.click(
      screen.getByRole("checkbox", { name: /Error occurred/ }),
    );
    const create = screen.getByRole("button", { name: "Create" });
    expect(create).toBeDisabled();
    expect(screen.getByText(/Start with https:\/\//)).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText(/^URL/));
    await userEvent.type(
      screen.getByLabelText(/^URL/),
      "https://hooks.example.com/ops",
    );
    await userEvent.click(create);
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "ops",
        url: "https://hooks.example.com/ops",
        events: ["error.occurred"],
        is_active: true,
      }),
    );
  });

  it("can pause and resume a webhook when editing it", async () => {
    const onSubmit = renderDialog({
      webhook: {
        id: "wh-1",
        name: "ops",
        url: "https://hooks.example.com/ops",
        events: ["error.occurred"],
        is_active: true,
        headers: {},
      },
    });
    const active = screen.getByRole("checkbox", { name: "Active" });
    expect(active).toBeChecked();
    await userEvent.click(active);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ id: "wh-1", is_active: false }),
    );
  });
});
