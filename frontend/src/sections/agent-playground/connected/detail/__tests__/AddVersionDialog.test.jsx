import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import AddVersionDialog from "../AddVersionDialog";

const mockSnackbar = vi.fn();
const mockMutate = vi.fn();
const mockVersion = { current: {} };

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: mockSnackbar }),
}));

vi.mock("src/api/agents/agentDetail", () => ({
  useAgentVersion: () => mockVersion.current,
  useChangeVersion: () => ({ mutate: mockMutate, isPending: false }),
}));

const AGENT = {
  id: "a1",
  description: "You are a booking assistant.",
  versionCount: 2,
};

const LIVE_AGENT = { ...AGENT, description: "old", activeVersionId: "ver-2" };

const renderDialog = (agent = AGENT) => {
  const onClose = vi.fn();
  render(<AddVersionDialog agent={agent} open onClose={onClose} />);
  return onClose;
};

const save = () => screen.getByRole("button", { name: "Save as v3" });

beforeEach(() => {
  mockSnackbar.mockReset();
  mockMutate.mockReset();
  mockVersion.current = { isLoading: false, isError: false };
});

describe("AddVersionDialog", () => {
  it("starts from the production version's prompt", () => {
    mockVersion.current = {
      isLoading: false,
      isError: false,
      data: { config: { description: "live prompt" } },
    };
    renderDialog(LIVE_AGENT);
    expect(screen.getByTestId("add-version-prompt")).toHaveValue("live prompt");
  });

  it("starts from the agent's prompt when nothing is in production", () => {
    renderDialog();
    expect(screen.getByTestId("add-version-prompt")).toHaveValue(
      "You are a booking assistant.",
    );
  });

  it("shows a spinner while the production version loads", () => {
    mockVersion.current = { isLoading: true, isError: false };
    renderDialog(LIVE_AGENT);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.queryByTestId("add-version-prompt")).not.toBeInTheDocument();
  });

  it("says when the production version couldn't load", () => {
    mockVersion.current = { isLoading: false, isError: true };
    renderDialog(LIVE_AGENT);
    expect(
      screen.getByText(/Couldn't load this agent's current version/),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("add-version-prompt")).not.toBeInTheDocument();
  });

  it("saves the edited prompt as a new version", () => {
    mockMutate.mockImplementation((_body, { onSuccess }) =>
      onSuccess({ version: { version_name_display: "v3" }, up_to_date: false }),
    );
    const onClose = renderDialog();

    fireEvent.change(screen.getByTestId("add-version-prompt"), {
      target: { value: "You are a hotel booking assistant." },
    });
    fireEvent.click(save());

    expect(mockMutate.mock.calls[0][0]).toEqual({
      kind: "manual",
      prompt: "You are a hotel booking assistant.",
    });
    expect(onClose).toHaveBeenCalled();
    expect(mockSnackbar).toHaveBeenCalledWith(
      "v3 created from the new prompt",
      {
        variant: "success",
      },
    );
  });

  it("says the prompt is already a version instead of making a new one", () => {
    mockMutate.mockImplementation((_body, { onSuccess }) =>
      onSuccess({ version: { version_name_display: "v2" }, up_to_date: true }),
    );
    renderDialog();

    fireEvent.click(save());

    expect(mockSnackbar).toHaveBeenCalledWith("v2 already has this prompt", {
      variant: "info",
    });
  });

  it("stays open and says why a save was refused", () => {
    mockMutate.mockImplementation((_body, { onError }) =>
      onError({ response: { data: { detail: "The prompt is too long" } } }),
    );
    const onClose = renderDialog();

    fireEvent.click(save());

    expect(onClose).not.toHaveBeenCalled();
    expect(mockSnackbar).toHaveBeenCalledWith("The prompt is too long", {
      variant: "error",
    });
  });

  it("won't save an empty prompt", () => {
    renderDialog();
    fireEvent.change(screen.getByTestId("add-version-prompt"), {
      target: { value: "   " },
    });
    expect(save()).toBeDisabled();
  });
});
