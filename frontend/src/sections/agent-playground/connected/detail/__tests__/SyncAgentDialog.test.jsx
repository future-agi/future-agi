import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import SyncAgentDialog from "../SyncAgentDialog";

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

const PHONE_AGENT = {
  id: "a1",
  source: "phone",
  activeVersionId: "ver-2",
  versionCount: 2,
};

const LIVEKIT_AGENT = { ...PHONE_AGENT, source: "livekit" };

const CONFIG = {
  description: "You answer refill calls.",
  contact_number: "+14155550100",
  livekit_agent_name: "front-desk",
  target_speaks_first: true,
};

const loaded = (config = CONFIG) => {
  mockVersion.current = {
    isLoading: false,
    isError: false,
    data: { config },
  };
};

const renderDialog = (agent = PHONE_AGENT) => {
  const onClose = vi.fn();
  render(<SyncAgentDialog agent={agent} onClose={onClose} />);
  return onClose;
};

const save = () => screen.getByRole("button", { name: "Save as v3" });

beforeEach(() => {
  mockSnackbar.mockReset();
  mockMutate.mockReset();
  loaded();
});

describe("SyncAgentDialog", () => {
  it("shows a spinner while the version loads", () => {
    mockVersion.current = { isLoading: true, isError: false };
    renderDialog();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.queryByTestId("sync-agent-prompt")).not.toBeInTheDocument();
  });

  it("says when the version couldn't load", () => {
    mockVersion.current = { isLoading: false, isError: true };
    renderDialog();
    expect(
      screen.getByText(/Couldn't load this agent's current version/),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("sync-agent-prompt")).not.toBeInTheDocument();
  });

  it("fills a phone agent's prompt, number and who speaks first", () => {
    renderDialog();
    expect(screen.getByTestId("sync-agent-prompt")).toHaveValue(
      "You answer refill calls.",
    );
    expect(screen.getByLabelText(/Phone number/)).toHaveValue("+14155550100");
    expect(screen.getByLabelText("Agent speaks first")).toBeChecked();
    expect(screen.queryByLabelText(/LiveKit agent name/)).toBeNull();
  });

  it("won't save until something changes", () => {
    renderDialog();
    expect(save()).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Agent speaks first"));
    expect(save()).toBeEnabled();
  });

  it("shows why a number is wrong after leaving the field and won't save it", () => {
    renderDialog();
    const phone = screen.getByTestId("sync-agent-phone");
    fireEvent.change(phone, { target: { value: "+1415" } });
    expect(
      screen.queryByText("Enter exactly 10 digits after +1"),
    ).not.toBeInTheDocument();
    fireEvent.blur(phone);
    expect(
      screen.getByText("Enter exactly 10 digits after +1"),
    ).toBeInTheDocument();
    expect(save()).toBeDisabled();
  });

  it("saves only what changed", () => {
    mockMutate.mockImplementation((_body, { onSuccess }) =>
      onSuccess({ version: { version_name_display: "v3" }, up_to_date: false }),
    );
    const onClose = renderDialog();

    fireEvent.change(screen.getByTestId("sync-agent-phone"), {
      target: { value: " +14155550199 " },
    });
    fireEvent.click(screen.getByLabelText("Agent speaks first"));
    fireEvent.click(save());

    expect(mockMutate).toHaveBeenCalledTimes(1);
    expect(mockMutate.mock.calls[0][0]).toEqual({
      kind: "manual",
      settings: { phone_number: "+14155550199", target_speaks_first: false },
    });
    expect(onClose).toHaveBeenCalled();
    expect(mockSnackbar).toHaveBeenCalledWith(
      "v3 created; environments using this agent will offer a rebuild",
      { variant: "success" },
    );
  });

  it("asks a LiveKit agent for its worker name, not a number", () => {
    renderDialog(LIVEKIT_AGENT);
    expect(screen.getByLabelText(/LiveKit agent name/)).toHaveValue(
      "front-desk",
    );
    expect(screen.queryByLabelText(/Phone number/)).toBeNull();
  });
});
