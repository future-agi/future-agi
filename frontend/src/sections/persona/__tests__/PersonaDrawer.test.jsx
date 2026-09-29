import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import PersonaDrawer from "../PersonaDrawer";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

let listViewProps = null;
vi.mock("../PersonaListView", () => ({
  default: (props) => {
    listViewProps = props;
    return (
      <div data-testid="persona-list-view">
        <button type="button" onClick={() => props.onCreatePersona?.()}>
          Create new persona
        </button>
        {["existing-1", "existing-2"].map((id) => {
          const isSelected = props.selectedPersonas?.some((p) => p.id === id);
          return (
            <button
              key={id}
              type="button"
              onClick={() =>
                props.onToggleSelect?.({ id, name: id }, !isSelected)
              }
            >
              toggle-{id} ({isSelected ? "selected" : "unselected"})
            </button>
          );
        })}
      </div>
    );
  },
}));

let createFormOnSuccess = null;
vi.mock("../PersonaCreateEdit/PersonaCreateEditForm", () => ({
  default: (props) => {
    createFormOnSuccess = props.onSuccess;
    return <div data-testid="persona-create-form">Create form</div>;
  },
}));

describe("PersonaDrawer", () => {
  beforeEach(() => {
    listViewProps = null;
    createFormOnSuccess = null;
  });

  it("auto-selects a persona created inline and shows it in the selected count", async () => {
    const user = userEvent.setup();
    const onAddPersonas = vi.fn();

    render(
      <PersonaDrawer
        open
        onClose={vi.fn()}
        onAddPersonas={onAddPersonas}
        personaCreateEditType="chat"
        preSelectedPersonas={[]}
      />,
    );

    expect(screen.getByText("Personas selected (0)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /create new persona/i }));
    await waitFor(() => {
      expect(screen.getByTestId("persona-create-form")).toBeInTheDocument();
    });

    expect(createFormOnSuccess).toBeTypeOf("function");

    act(() => {
      createFormOnSuccess({ data: { id: "new-persona", name: "New Persona" } });
    });

    await waitFor(() => {
      expect(screen.getByTestId("persona-list-view")).toBeInTheDocument();
    });

    expect(screen.getByText("Personas selected (1)")).toBeInTheDocument();
    expect(listViewProps.selectedPersonas).toEqual([
      { id: "new-persona", name: "New Persona" },
    ]);

    await user.click(screen.getByRole("button", { name: /^add$/i }));
    expect(onAddPersonas).toHaveBeenCalledWith([
      { id: "new-persona", name: "New Persona" },
    ]);
  });

  it("preserves pre-existing selections across the create round-trip", async () => {
    const user = userEvent.setup();

    render(
      <PersonaDrawer
        open
        onClose={vi.fn()}
        onAddPersonas={vi.fn()}
        personaCreateEditType="chat"
        preSelectedPersonas={[{ id: "existing-1", name: "existing-1" }]}
      />,
    );

    expect(screen.getByText("Personas selected (1)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /create new persona/i }));
    await waitFor(() => {
      expect(createFormOnSuccess).toBeTypeOf("function");
    });

    act(() => {
      createFormOnSuccess({ data: { id: "new-persona", name: "New Persona" } });
    });

    await waitFor(() => {
      expect(screen.getByText("Personas selected (2)")).toBeInTheDocument();
    });

    expect(listViewProps.selectedPersonas.map((p) => p.id)).toEqual([
      "existing-1",
      "new-persona",
    ]);
  });

  it("does not add a duplicate entry if the created persona is already selected", async () => {
    const user = userEvent.setup();

    render(
      <PersonaDrawer
        open
        onClose={vi.fn()}
        onAddPersonas={vi.fn()}
        personaCreateEditType="chat"
        preSelectedPersonas={[{ id: "new-persona", name: "Already there" }]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /create new persona/i }));
    await waitFor(() => {
      expect(createFormOnSuccess).toBeTypeOf("function");
    });

    act(() => {
      createFormOnSuccess({ data: { id: "new-persona", name: "New Persona" } });
    });

    await waitFor(() => {
      expect(screen.getByTestId("persona-list-view")).toBeInTheDocument();
    });

    expect(screen.getByText("Personas selected (1)")).toBeInTheDocument();
  });

  it("closes the create panel cleanly without crashing when the response has no usable id", async () => {
    const user = userEvent.setup();

    render(
      <PersonaDrawer
        open
        onClose={vi.fn()}
        onAddPersonas={vi.fn()}
        personaCreateEditType="chat"
        preSelectedPersonas={[]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /create new persona/i }));
    await waitFor(() => {
      expect(createFormOnSuccess).toBeTypeOf("function");
    });

    expect(() => {
      act(() => {
        createFormOnSuccess({ data: null });
      });
    }).not.toThrow();

    await waitFor(() => {
      expect(screen.getByTestId("persona-list-view")).toBeInTheDocument();
    });

    expect(screen.getByText("Personas selected (0)")).toBeInTheDocument();
  });

  it("still supports manual toggle add/remove of existing personas", async () => {
    const user = userEvent.setup();

    render(
      <PersonaDrawer
        open
        onClose={vi.fn()}
        onAddPersonas={vi.fn()}
        personaCreateEditType="chat"
        preSelectedPersonas={[]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /toggle-existing-1/i }));
    expect(screen.getByText("Personas selected (1)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /toggle-existing-1/i }));
    expect(screen.getByText("Personas selected (0)")).toBeInTheDocument();
  });
});
