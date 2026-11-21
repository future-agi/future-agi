import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";

// The list and the create form are stubbed: these tests cover how the drawer
// carries the selection between them, not the table or the form fields.
vi.mock("../PersonaListView", () => ({
  default: ({ selectedPersonas, onToggleSelect, onCreatePersona }) => (
    <div data-testid="persona-list">
      <p>{`Checked: ${selectedPersonas.map((p) => p.id).join(",")}`}</p>
      <button
        type="button"
        onClick={() =>
          onToggleSelect({ id: "p2", name: "Billing caller" }, true)
        }
      >
        Select Billing caller
      </button>
      <button
        type="button"
        onClick={() =>
          onToggleSelect({ id: "p1", name: "Angry customer" }, false)
        }
      >
        Unselect Angry customer
      </button>
      <button type="button" onClick={onCreatePersona}>
        Create persona
      </button>
    </div>
  ),
}));

// The real form calls onSuccess with the axios response of
// POST /simulate/api/personas/, whose body is { status, result: persona }.
vi.mock("../PersonaCreateEdit/PersonaCreateEditForm", () => ({
  default: ({ onSuccess, onCancel }) => (
    <div data-testid="persona-create-form">
      <button
        type="button"
        onClick={() =>
          onSuccess?.({
            data: {
              status: true,
              result: {
                id: "p9",
                name: "New caller",
                simulation_type: "voice",
                is_default: false,
              },
            },
          })
        }
      >
        Save new persona
      </button>
      <button
        type="button"
        onClick={() =>
          onSuccess?.({
            data: {
              status: true,
              result: { id: "p1", name: "Angry customer" },
            },
          })
        }
      >
        Save existing persona
      </button>
      <button
        type="button"
        onClick={() => onSuccess?.({ data: { status: true, result: {} } })}
      >
        Save without id
      </button>
      <button type="button" onClick={onCancel}>
        Discard
      </button>
    </div>
  ),
}));

const { default: PersonaDrawer } = await import("../PersonaDrawer");

const angryCustomer = { id: "p1", name: "Angry customer" };

const renderDrawer = (props = {}) => {
  const handlers = { onClose: vi.fn(), onAddPersonas: vi.fn() };
  const view = render(
    <PersonaDrawer
      open
      personaCreateEditType="voice"
      preSelectedPersonas={[angryCustomer]}
      {...handlers}
      {...props}
    />,
  );
  return { ...view, ...handlers };
};

// Opens the inline create form and waits until the list has unmounted, as it
// has by the time a user finishes filling in the form.
const openCreateForm = async () => {
  fireEvent.click(screen.getByRole("button", { name: "Create persona" }));
  await screen.findByTestId("persona-create-form");
  await waitFor(() =>
    expect(screen.queryByTestId("persona-list")).not.toBeInTheDocument(),
  );
};

const backToList = async (buttonName) => {
  fireEvent.click(screen.getByRole("button", { name: buttonName }));
  await screen.findByTestId("persona-list");
};

describe("PersonaDrawer inline create", () => {
  it("selects a persona created inline when the drawer returns to the list", async () => {
    const { onAddPersonas } = renderDrawer();
    expect(screen.getByText("Personas selected (1)")).toBeInTheDocument();

    await openCreateForm();
    await backToList("Save new persona");

    expect(
      await screen.findByText("Personas selected (2)"),
    ).toBeInTheDocument();
    expect(screen.getByText("Checked: p1,p9")).toBeInTheDocument();

    const add = screen.getByRole("button", { name: "Add" });
    expect(add).toBeEnabled();
    fireEvent.click(add);

    expect(onAddPersonas).toHaveBeenCalledTimes(1);
    expect(onAddPersonas).toHaveBeenCalledWith([
      angryCustomer,
      expect.objectContaining({
        id: "p9",
        name: "New caller",
        simulationType: "voice",
        isDefault: false,
      }),
    ]);
  });

  it("keeps personas picked before opening the create form", async () => {
    renderDrawer();
    fireEvent.click(
      screen.getByRole("button", { name: "Select Billing caller" }),
    );
    expect(screen.getByText("Personas selected (2)")).toBeInTheDocument();

    await openCreateForm();
    await backToList("Discard");

    expect(
      await screen.findByText("Personas selected (2)"),
    ).toBeInTheDocument();
    expect(screen.getByText("Checked: p1,p2")).toBeInTheDocument();
  });

  it("does not add a created persona twice when it is already selected", async () => {
    const { onAddPersonas } = renderDrawer();

    await openCreateForm();
    await backToList("Save existing persona");

    expect(screen.getByText("Personas selected (1)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onAddPersonas).toHaveBeenCalledWith([angryCustomer]);
  });

  it("closes the create form without adding anything when the response has no id", async () => {
    const { onAddPersonas } = renderDrawer();

    await openCreateForm();
    await backToList("Save without id");

    expect(screen.getByText("Personas selected (1)")).toBeInTheDocument();
    expect(screen.getByText("Checked: p1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onAddPersonas).toHaveBeenCalledWith([angryCustomer]);
  });

  it("still removes a persona when it is unchecked in the list", () => {
    renderDrawer();

    fireEvent.click(
      screen.getByRole("button", { name: "Unselect Angry customer" }),
    );

    expect(screen.getByText("Personas selected (0)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
  });

  it("starts from the scenario's current personas each time it opens", async () => {
    const handlers = { onClose: vi.fn(), onAddPersonas: vi.fn() };
    const { rerender } = render(
      <PersonaDrawer
        open
        personaCreateEditType="voice"
        preSelectedPersonas={[angryCustomer]}
        {...handlers}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Select Billing caller" }),
    );
    expect(screen.getByText("Personas selected (2)")).toBeInTheDocument();

    // Cancel without adding, then reopen after the scenario's personas changed.
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(handlers.onClose).toHaveBeenCalledTimes(1);
    rerender(
      <PersonaDrawer
        open={false}
        personaCreateEditType="voice"
        preSelectedPersonas={[angryCustomer]}
        {...handlers}
      />,
    );
    await waitFor(() =>
      expect(screen.queryByTestId("persona-list")).not.toBeInTheDocument(),
    );

    const supportCaller = { id: "p3", name: "Support caller" };
    rerender(
      <PersonaDrawer
        open
        personaCreateEditType="voice"
        preSelectedPersonas={[supportCaller]}
        {...handlers}
      />,
    );

    expect(
      await screen.findByText("Personas selected (1)"),
    ).toBeInTheDocument();
    expect(screen.getByText("Checked: p3")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(handlers.onAddPersonas).toHaveBeenCalledWith([supportCaller]);
  });
});
