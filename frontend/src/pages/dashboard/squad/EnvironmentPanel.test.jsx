import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { EnvironmentPanel } from "./OmegaSquadPage";

describe("Omega Squad environment panel", () => {
  it("creates a pinned remote environment and a fenced revision without local values", async () => {
    const onCreate = vi.fn().mockResolvedValue({ available: true });
    const onRevise = vi.fn().mockResolvedValue({ available: true });
    const props = {
      result: {
        available: true,
        environments: [
          {
            id: "env-1",
            name: "Production",
            status: "paused",
            revision: 2,
            sandboxKind: "container",
            pinnedImageDigest: `sha256:${"a".repeat(64)}`,
            resourcePreset: "small",
          },
        ],
      },
      revisionsById: {},
      revisionsLoadingId: "",
      onInspectRevisions: vi.fn(),
      onControl: vi.fn(),
      onCreate,
      onRevise,
      busyId: "",
    };
    render(<EnvironmentPanel {...props} />);

    await userEvent.click(
      screen.getByRole("button", { name: "Create remote environment" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Name" }),
      "Staging",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Pinned image digest" }),
      `sha256:${"b".repeat(64)}`,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Create environment" }),
    );
    await waitFor(() =>
      expect(onCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Staging",
          sandboxKind: "container",
          pinnedImageDigest: `sha256:${"b".repeat(64)}`,
          browserOrigins: [],
          credentialBindingIds: [],
        }),
      ),
    );

    await userEvent.click(screen.getByRole("button", { name: "New revision" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Create revision" }),
    );
    await waitFor(() =>
      expect(onRevise).toHaveBeenCalledWith(
        "env-1",
        expect.objectContaining({ baseRevision: 2, sandboxKind: "container" }),
      ),
    );
    expect(screen.queryByText(/secret|token/i)).not.toBeInTheDocument();
  });
});
