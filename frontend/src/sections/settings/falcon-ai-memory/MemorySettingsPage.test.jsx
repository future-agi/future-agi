import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import MemorySettingsPage from "./MemorySettingsPage";
import { getSourceBadge } from "./utils";
import {
  fetchMemories,
  saveMemory,
  deleteMemory,
} from "src/sections/falcon-ai/hooks/useFalconAPI";

// Mock Iconify (canvas-free)
function MockIconify({ icon, ...props }) {
  return <span data-testid="iconify" data-icon={icon} {...props} />;
}
MockIconify.propTypes = { icon: PropTypes.string.isRequired };
vi.mock("src/components/iconify", () => ({ default: MockIconify }));

vi.mock("src/sections/falcon-ai/hooks/useFalconAPI", () => ({
  fetchMemories: vi.fn(),
  saveMemory: vi.fn(),
  deleteMemory: vi.fn(),
}));

const MEMORIES = [
  {
    id: "m1",
    key: "default_model",
    value: "turing_large",
    source: "user",
    updated_at: "2026-06-09T10:00:00Z",
  },
  {
    id: "m2",
    key: "primary_dataset",
    value: "prod-traces-v2",
    source: "agent",
    updated_at: "2026-06-08T09:00:00Z",
  },
  {
    id: "m3",
    key: "workspace_purpose",
    value: "LLM eval pipelines",
    source: "init",
    updated_at: "2026-06-01T08:00:00Z",
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  fetchMemories.mockResolvedValue({ status: true, results: MEMORIES });
  saveMemory.mockResolvedValue({ status: true, result: {} });
  deleteMemory.mockResolvedValue({ status: true });
});

describe("getSourceBadge", () => {
  it("maps the three sources to attribution badges", () => {
    expect(getSourceBadge("user").label).toBe("You");
    expect(getSourceBadge("agent").label).toBe("Falcon");
    expect(getSourceBadge("init").label).toBe("Init");
  });

  it("falls back to the raw source for unknown values", () => {
    expect(getSourceBadge("mystery").label).toBe("mystery");
    expect(getSourceBadge("mystery").color).toBe("default");
  });
});

describe("MemorySettingsPage", () => {
  it("lists memories with key, value, source badge, and updated date", async () => {
    render(<MemorySettingsPage />);

    expect(await screen.findByText("default_model")).toBeInTheDocument();
    expect(screen.getByText("turing_large")).toBeInTheDocument();
    // Source attribution badges — the 5A trust feature.
    expect(screen.getByText("You")).toBeInTheDocument();
    expect(screen.getByText("Falcon")).toBeInTheDocument();
    expect(screen.getByText("Init")).toBeInTheDocument();
    // updated_at column is rendered (formatted, so just assert non-empty cells exist)
    expect(screen.getByText("Updated")).toBeInTheDocument();
  });

  it("shows the empty state when there are no memories", async () => {
    fetchMemories.mockResolvedValue({ status: true, results: [] });
    render(<MemorySettingsPage />);

    expect(await screen.findByText("No memories yet")).toBeInTheDocument();
    expect(
      screen.getByText(/Anything it saves shows up here/i),
    ).toBeInTheDocument();
  });

  it("shows an error alert when loading fails", async () => {
    fetchMemories.mockRejectedValue(new Error("boom"));
    render(<MemorySettingsPage />);

    expect(
      await screen.findByText("Failed to load memories."),
    ).toBeInTheDocument();
  });

  it("edits a memory value and saves with the SAME key (upsert flips source to user)", async () => {
    render(<MemorySettingsPage />);
    await screen.findByText("primary_dataset");

    fireEvent.click(screen.getByLabelText("Edit primary_dataset"));
    const input = screen.getByLabelText("Edit value for primary_dataset");
    fireEvent.change(input, { target: { value: "prod-traces-v3" } });
    fireEvent.click(screen.getByLabelText("Save primary_dataset"));

    await waitFor(() =>
      expect(saveMemory).toHaveBeenCalledWith(
        "primary_dataset",
        "prod-traces-v3",
      ),
    );
    // List is refreshed after a save.
    expect(fetchMemories).toHaveBeenCalledTimes(2);
  });

  it("cancels an edit without saving", async () => {
    render(<MemorySettingsPage />);
    await screen.findByText("primary_dataset");

    fireEvent.click(screen.getByLabelText("Edit primary_dataset"));
    fireEvent.click(screen.getByLabelText("Cancel editing primary_dataset"));

    expect(saveMemory).not.toHaveBeenCalled();
    expect(screen.getByText("prod-traces-v2")).toBeInTheDocument();
  });

  it("deletes a memory by id and removes the row", async () => {
    render(<MemorySettingsPage />);
    await screen.findByText("default_model");

    fireEvent.click(screen.getByLabelText("Delete default_model"));

    await waitFor(() => expect(deleteMemory).toHaveBeenCalledWith("m1"));
    await waitFor(() =>
      expect(screen.queryByText("default_model")).not.toBeInTheDocument(),
    );
  });

  it("adds a new memory via the inline form", async () => {
    render(<MemorySettingsPage />);
    await screen.findByText("default_model");

    fireEvent.click(screen.getByRole("button", { name: /Add Memory/i }));
    fireEvent.change(screen.getByLabelText("Memory key"), {
      target: { value: "eval_score_convention" },
    });
    fireEvent.change(screen.getByLabelText("Memory value"), {
      target: { value: "0-10, higher is better" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(saveMemory).toHaveBeenCalledWith(
        "eval_score_convention",
        "0-10, higher is better",
      ),
    );
  });

  it("warns when adding a key that already exists (overwrite semantics)", async () => {
    render(<MemorySettingsPage />);
    await screen.findByText("default_model");

    fireEvent.click(screen.getByRole("button", { name: /Add Memory/i }));
    fireEvent.change(screen.getByLabelText("Memory key"), {
      target: { value: "default_model" },
    });

    expect(
      screen.getByText(/Existing key — saving will overwrite it/i),
    ).toBeInTheDocument();
  });

  it("surfaces a save failure as a dismissible alert", async () => {
    saveMemory.mockRejectedValue({
      response: { data: { detail: "key too long" } },
    });
    render(<MemorySettingsPage />);
    await screen.findByText("primary_dataset");

    fireEvent.click(screen.getByLabelText("Edit primary_dataset"));
    fireEvent.click(screen.getByLabelText("Save primary_dataset"));

    expect(await screen.findByText("key too long")).toBeInTheDocument();
  });
});
