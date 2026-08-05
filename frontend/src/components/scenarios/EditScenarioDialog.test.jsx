import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import EditScenarioDialog from "./EditScenarioDialog";

// ---------------------------------------------------------------------------
// Mock axios so that handleEdit's duplicate check and PUT call are controlled.
// ---------------------------------------------------------------------------
const mockGet = vi.fn();
const mockPut = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
    put: (...args) => mockPut(...args),
  },
  endpoints: {
    scenarios: {
      list: "/simulate/scenarios/",
      edit: (id) => `/simulate/scenarios/${id}/edit/`,
    },
  },
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid={`iconify-${icon}`} {...props} />
  ),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const DEFAULT_SCENARIO = {
  id: "scenario-1",
  name: "Original Name",
  description: "Original description",
};

const renderDialog = (overrides = {}) => {
  const props = {
    open: true,
    onClose: vi.fn(),
    onEditSuccess: vi.fn(),
    scenario: DEFAULT_SCENARIO,
    ...overrides,
  };
  return render(<EditScenarioDialog {...props} />);
};

/**
 * Helper: get the scenario name textbox and the save button.
 * MUI TextField labels differ from plain HTML labels so we target by role.
 */
const getNameInput = () =>
  screen.getByRole("textbox", { name: /scenario name/i });
const getDescriptionInput = () =>
  screen.getByRole("textbox", { name: /description/i });
const getSaveButton = () =>
  screen.getByRole("button", { name: /save changes/i });

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("EditScenarioDialog — name uniqueness", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPut.mockReset();
  });

  // -----------------------------------------------------------------------
  // Duplicate name detection
  // -----------------------------------------------------------------------

  it("shows an error when another scenario has the same name", async () => {
    mockGet.mockResolvedValueOnce({
      data: {
        results: [{ name: "Duplicate Name", id: "scenario-2" }],
      },
    });

    renderDialog();

    const nameInput = getNameInput();
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "Duplicate Name");

    await userEvent.click(getSaveButton());

    await waitFor(() => {
      expect(
        screen.getByText(
          "A scenario with this name already exists. Please choose another name.",
        ),
      ).toBeInTheDocument();
    });

    // PUT should not be called
    expect(mockPut).not.toHaveBeenCalled();
  });

  it("allows edit when the name is unchanged (same as current)", async () => {
    mockPut.mockResolvedValueOnce({ data: { message: "updated" } });

    const onEditSuccess = vi.fn();
    renderDialog({ onEditSuccess });

    // Change the description so the save button becomes enabled
    const descInput = getDescriptionInput();
    await userEvent.clear(descInput);
    await userEvent.type(descInput, "New description");
    await userEvent.click(getSaveButton());

    await waitFor(() => {
      expect(onEditSuccess).toHaveBeenCalled();
    });

    // No duplicate GET was made (name didn't change)
    expect(mockGet).not.toHaveBeenCalled();
    expect(mockPut).toHaveBeenCalledTimes(1);
  });

  it("allows edit when no scenario with the same name exists", async () => {
    mockGet.mockResolvedValueOnce({
      data: { results: [] },
    });
    mockPut.mockResolvedValueOnce({ data: { message: "updated" } });

    const onEditSuccess = vi.fn();
    renderDialog({ onEditSuccess });

    const nameInput = getNameInput();
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "Unique Name");
    await userEvent.click(getSaveButton());

    await waitFor(() => {
      expect(onEditSuccess).toHaveBeenCalled();
    });

    expect(mockGet).toHaveBeenCalledTimes(1);
    expect(mockPut).toHaveBeenCalledTimes(1);
  });

  it("excludes the current scenario from duplicate detection", async () => {
    // The API returns the current scenario itself — this should not block edit
    mockGet.mockResolvedValueOnce({
      data: {
        results: [
          { name: "Updated Name", id: "scenario-1" }, // same ID as current
        ],
      },
    });
    mockPut.mockResolvedValueOnce({ data: { message: "updated" } });

    const onEditSuccess = vi.fn();
    renderDialog({ onEditSuccess });

    const nameInput = getNameInput();
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "Updated Name");
    await userEvent.click(getSaveButton());

    await waitFor(() => {
      expect(onEditSuccess).toHaveBeenCalled();
    });
  });

  // -----------------------------------------------------------------------
  // Graceful degradation
  // -----------------------------------------------------------------------

  it("allows edit when the duplicate-check API fails", async () => {
    mockGet.mockRejectedValueOnce(new Error("Network Error"));
    mockPut.mockResolvedValueOnce({ data: { message: "updated" } });

    const onEditSuccess = vi.fn();
    renderDialog({ onEditSuccess });

    const nameInput = getNameInput();
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "New Name");
    await userEvent.click(getSaveButton());

    await waitFor(() => {
      expect(onEditSuccess).toHaveBeenCalled();
    });

    expect(mockPut).toHaveBeenCalledTimes(1);
  });

  it("allows edit when the API returns null data", async () => {
    mockGet.mockResolvedValueOnce({ data: null });
    mockPut.mockResolvedValueOnce({ data: { message: "updated" } });

    const onEditSuccess = vi.fn();
    renderDialog({ onEditSuccess });

    const nameInput = getNameInput();
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "New Name");
    await userEvent.click(getSaveButton());

    await waitFor(() => {
      expect(onEditSuccess).toHaveBeenCalled();
    });
  });

  it("allows edit when the API returns no results field", async () => {
    mockGet.mockResolvedValueOnce({ data: {} });
    mockPut.mockResolvedValueOnce({ data: { message: "updated" } });

    const onEditSuccess = vi.fn();
    renderDialog({ onEditSuccess });

    const nameInput = getNameInput();
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "New Name");
    await userEvent.click(getSaveButton());

    await waitFor(() => {
      expect(onEditSuccess).toHaveBeenCalled();
    });
  });

  // -----------------------------------------------------------------------
  // Basic (unchanged) validation
  // -----------------------------------------------------------------------

  it("blocks submission when the name is empty", async () => {
    renderDialog();

    await userEvent.clear(getNameInput());

    expect(getSaveButton()).toBeDisabled();
    expect(mockPut).not.toHaveBeenCalled();
  });
});
