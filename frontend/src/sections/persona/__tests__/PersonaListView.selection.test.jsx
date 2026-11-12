/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "src/utils/test-utils";
import PersonaListView from "../PersonaListView";

const api = vi.hoisted(() => ({
  query: null,
  deletePersonas: vi.fn(),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/api/persona/persona", () => ({
  useGetPersonasPaginated: () => api.query,
  useBulkDeletePersonas: () => ({
    mutateAsync: api.deletePersonas,
    isPending: false,
  }),
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/filter", () => ({
  ObjectFilterPanel: () => null,
}));
vi.mock("../PersonaCreateEdit/PersonaCreateEditDrawer", () => ({
  default: () => null,
}));
vi.mock("../PersonaCreateEdit/DuplicatePersonas", () => ({
  default: () => null,
}));
vi.mock("../PersonaInfo/PersonaInfoDrawer", () => ({ default: () => null }));
vi.mock("../components/PersonasColumnsPopover", () => ({
  default: () => null,
}));

// DataTable's public selection API uses stable record IDs and selected rows.
// Exercise the real list component on both sides of that boundary without
// depending on DataGrid virtualization or layout in jsdom.
vi.mock("src/components/data-table", () => ({
  DataTable: ({ data, rowSelection, onRowSelectionChange }) => (
    <div>
      <button
        onClick={() =>
          onRowSelectionChange(
            Object.fromEntries(data.map((persona) => [persona.id, true])),
            data,
          )
        }
      >
        Select all rows
      </button>
      {data.map((persona) => (
        <input
          key={persona.id}
          type="checkbox"
          aria-label={`Select ${persona.name}`}
          checked={Boolean(rowSelection[persona.id])}
          onChange={(event) => {
            const next = { ...rowSelection };
            if (event.target.checked) next[persona.id] = true;
            else delete next[persona.id];
            onRowSelectionChange(next, data.filter((item) => next[item.id]));
          }}
        />
      ))}
    </div>
  ),
  DataTablePagination: () => null,
}));

const personaA = { id: "persona-A", name: "Persona A", isDefault: false };
const personaB = { id: "persona-B", name: "Persona B", isDefault: false };
const copy = { id: "persona-copy", name: "Copy of A", isDefault: false };
const system = { id: "system", name: "Built-in persona", isDefault: true };

const setResults = (results, isFetching = false) => {
  api.query = {
    data: { results, count: results.length },
    isLoading: false,
    isFetching,
  };
};
const checkbox = (persona) =>
  screen.getByRole("checkbox", { name: `Select ${persona.name}` });
const openDelete = () => {
  fireEvent.click(screen.getByRole("button", { name: "Delete", exact: true }));
  return within(screen.getByRole("dialog"));
};

beforeEach(() => {
  setResults([personaA, personaB]);
  api.deletePersonas.mockReset();
  api.deletePersonas.mockResolvedValue({ deleted: 1, failed: [] });
});

describe("H9-C25-001 persona catalog selection", () => {
  it("keeps B selected when a duplicate prepends a row during refresh", () => {
    const { rerender } = render(<PersonaListView />);
    fireEvent.click(checkbox(personaB));

    // The duplicate mutation invalidates the active personas query.
    setResults([personaA, personaB], true);
    rerender(<PersonaListView />);
    expect(checkbox(personaB)).toBeChecked();

    setResults([copy, personaA, personaB]);
    rerender(<PersonaListView />);
    expect(checkbox(personaB)).toBeChecked();
    expect(checkbox(personaA)).not.toBeChecked();
    expect(checkbox(copy)).not.toBeChecked();
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("deletes the originally selected ID after the duplicate refresh", async () => {
    const { rerender } = render(<PersonaListView />);
    fireEvent.click(checkbox(personaB));
    setResults([copy, personaA, personaB]);
    rerender(<PersonaListView />);

    const dialog = openDelete();
    fireEvent.click(dialog.getByRole("button", { name: "Delete", exact: true }));
    await waitFor(() =>
      expect(api.deletePersonas).toHaveBeenCalledWith([personaB.id]),
    );
    await waitFor(() => expect(checkbox(personaB)).not.toBeChecked());
    expect(api.deletePersonas).toHaveBeenCalledTimes(1);
  });

  it("drops unavailable selections instead of retargeting or restoring them", () => {
    const { rerender } = render(<PersonaListView />);
    fireEvent.click(checkbox(personaB));

    setResults([personaA, copy]);
    rerender(<PersonaListView />);
    expect(checkbox(copy)).not.toBeChecked();
    expect(screen.queryByText("1 selected")).not.toBeInTheDocument();

    setResults([personaA, personaB]);
    rerender(<PersonaListView />);
    expect(checkbox(personaB)).not.toBeChecked();
  });

  it("freezes the displayed names and delete IDs when confirmation opens", async () => {
    const { rerender } = render(<PersonaListView />);
    fireEvent.click(checkbox(personaB));
    const dialog = openDelete();
    expect(dialog.getByText(personaB.name)).toBeInTheDocument();

    // Even if B disappears from the current page, confirmation must still
    // describe and submit the same explicit target, never the replacement.
    setResults([copy, personaA]);
    rerender(<PersonaListView />);
    expect(dialog.getByText(personaB.name)).toBeInTheDocument();
    expect(dialog.queryByText(personaA.name)).not.toBeInTheDocument();
    expect(dialog.queryByText(copy.name)).not.toBeInTheDocument();
    fireEvent.click(dialog.getByRole("button", { name: "Delete", exact: true }));
    await waitFor(() =>
      expect(api.deletePersonas).toHaveBeenCalledWith([personaB.id]),
    );
  });

  it("preserves select-all and deselection while excluding system delete targets", async () => {
    setResults([personaA, personaB, system]);
    const { rerender } = render(<PersonaListView />);
    fireEvent.click(screen.getByRole("button", { name: "Select all rows" }));
    fireEvent.click(checkbox(personaA));

    setResults([copy, system, personaA, personaB]);
    rerender(<PersonaListView />);
    expect(checkbox(personaB)).toBeChecked();
    expect(checkbox(system)).toBeChecked();
    expect(checkbox(personaA)).not.toBeChecked();
    expect(checkbox(copy)).not.toBeChecked();

    const dialog = openDelete();
    expect(dialog.getByText(personaB.name)).toBeInTheDocument();
    expect(dialog.queryByText(system.name)).not.toBeInTheDocument();
    expect(dialog.getByText(/1 Future AGI built-in persona will be skipped/))
      .toBeInTheDocument();
    fireEvent.click(dialog.getByRole("button", { name: "Delete", exact: true }));
    await waitFor(() =>
      expect(api.deletePersonas).toHaveBeenCalledWith([personaB.id]),
    );
  });

  it("keeps picker selection externally controlled across pages", () => {
    const onToggleSelect = vi.fn();
    const props = {
      isSelectable: true,
      selectedPersonas: [personaB, system],
      onToggleSelect,
    };
    const { rerender } = render(<PersonaListView {...props} />);
    expect(checkbox(personaB)).toBeChecked();
    fireEvent.click(checkbox(personaA));
    expect(onToggleSelect).toHaveBeenCalledWith(personaA, true);
    expect(onToggleSelect).toHaveBeenCalledTimes(1);

    setResults([copy]);
    rerender(<PersonaListView {...props} />);
    setResults([system, personaB]);
    rerender(<PersonaListView {...props} />);
    expect(checkbox(system)).toBeChecked();
    expect(checkbox(personaB)).toBeChecked();
    fireEvent.click(checkbox(personaB));
    expect(onToggleSelect).toHaveBeenLastCalledWith(personaB, false);
    expect(onToggleSelect).toHaveBeenCalledTimes(2);
  });
});
