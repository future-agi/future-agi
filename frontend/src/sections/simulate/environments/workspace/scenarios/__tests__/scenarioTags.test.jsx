import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "src/utils/test-utils";
import { enqueueSnackbar } from "notistack";
import BulkTagMenu from "../BulkTagMenu";
import ScenarioTagCell from "../ScenarioTagCell";
import { SCENARIO_TAGS_COPY as C } from "../scenarios.constants";

const setTags = { mutate: vi.fn(), isPending: false };
const changeTags = { mutate: vi.fn(), isPending: false };
let known = { data: [], isLoading: false, isError: false };

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/api/simulate-environments/scenarioTags", () => ({
  useEnvironmentTags: () => ({
    ...known,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useSetScenarioTags: () => setTags,
  useChangeScenarioTags: () => changeTags,
}));

function FakePicker({ tags, onChange, disabled }) {
  return (
    <div>
      <span data-testid="shown-tags">{tags.join(",")}</span>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onChange([...tags, "billing"])}
      >
        add billing
      </button>
    </div>
  );
}
FakePicker.propTypes = {
  tags: PropTypes.arrayOf(PropTypes.string),
  onChange: PropTypes.func,
  disabled: PropTypes.bool,
};

function FakeChip({ tag }) {
  return <span>{tag}</span>;
}
FakeChip.propTypes = { tag: PropTypes.string };

vi.mock("src/components/tag-picker", () => ({
  default: (props) => <FakePicker {...props} />,
  TagChip: (props) => <FakeChip {...props} />,
}));

beforeEach(() => {
  vi.clearAllMocks();
  known = { data: [], isLoading: false, isError: false };
});

describe("ScenarioTagCell", () => {
  it("saves the scenario's full tag list", async () => {
    render(<ScenarioTagCell envId="e1" scenarioKey="s1" tags={["vip"]} />);
    await userEvent.click(screen.getByText("add billing"));
    expect(setTags.mutate).toHaveBeenCalledWith(
      { scenarioKey: "s1", tags: ["vip", "billing"] },
      expect.any(Object),
    );
    expect(screen.getByTestId("shown-tags")).toHaveTextContent("vip,billing");
  });

  it("puts the old tags back and says so when the save fails", async () => {
    setTags.mutate.mockImplementationOnce((_body, { onError }) => onError());
    render(<ScenarioTagCell envId="e1" scenarioKey="s1" tags={["vip"]} />);
    await userEvent.click(screen.getByText("add billing"));
    expect(screen.getByTestId("shown-tags")).toHaveTextContent(/^vip$/);
    expect(enqueueSnackbar).toHaveBeenCalledWith(C.failed, {
      variant: "error",
    });
  });

  it("cannot be edited without a scenario key or while locked", () => {
    const { rerender } = render(
      <ScenarioTagCell envId="e1" scenarioKey="" tags={[]} />,
    );
    expect(screen.getByText("add billing")).toBeDisabled();
    rerender(<ScenarioTagCell envId="e1" scenarioKey="s1" tags={[]} locked />);
    expect(screen.getByText("add billing")).toBeDisabled();
  });
});

describe("BulkTagMenu", () => {
  const anchor = document.createElement("div");
  const renderMenu = (resolveKeys, onDone) =>
    render(
      <BulkTagMenu
        envId="e1"
        anchorEl={anchor}
        onClose={vi.fn()}
        count={2}
        resolveKeys={resolveKeys}
        onDone={onDone}
      />,
    );

  it("calls onDone after a change succeeds, and not after one fails", async () => {
    known = { data: ["vip"], isLoading: false, isError: false };
    const onDone = vi.fn();
    changeTags.mutate.mockImplementationOnce((_body, { onError }) => onError());
    renderMenu(() => Promise.resolve(["s1"]), onDone);
    await userEvent.click(screen.getByRole("button", { name: C.add }));
    await waitFor(() => expect(changeTags.mutate).toHaveBeenCalledTimes(1));
    expect(onDone).not.toHaveBeenCalled();

    changeTags.mutate.mockImplementationOnce((_body, { onSuccess }) =>
      onSuccess(),
    );
    await userEvent.click(screen.getByRole("button", { name: C.add }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  });

  it("adds a known tag to every selected scenario that has a key", async () => {
    known = { data: ["vip", "refunds"], isLoading: false, isError: false };
    renderMenu(() => Promise.resolve(["s1", null, "s2"]));
    await userEvent.click(screen.getAllByRole("button", { name: C.add })[1]);
    await waitFor(() =>
      expect(changeTags.mutate).toHaveBeenCalledWith(
        { scenarioKeys: ["s1", "s2"], add: ["refunds"], remove: [] },
        expect.any(Object),
      ),
    );
  });

  it("removes a known tag", async () => {
    known = { data: ["vip"], isLoading: false, isError: false };
    renderMenu(() => Promise.resolve(["s1"]));
    await userEvent.click(screen.getByRole("button", { name: C.remove }));
    await waitFor(() =>
      expect(changeTags.mutate).toHaveBeenCalledWith(
        { scenarioKeys: ["s1"], add: [], remove: ["vip"] },
        expect.any(Object),
      ),
    );
  });

  it("adds a new tag typed into the box on Enter", async () => {
    renderMenu(() => Promise.resolve(["s1"]));
    await userEvent.type(screen.getByPlaceholderText(C.newTag), " urgent {enter}");
    await waitFor(() =>
      expect(changeTags.mutate).toHaveBeenCalledWith(
        { scenarioKeys: ["s1"], add: ["urgent"], remove: [] },
        expect.any(Object),
      ),
    );
  });

  it("says the change failed when the selection cannot be read", async () => {
    known = { data: ["vip"], isLoading: false, isError: false };
    renderMenu(() => Promise.reject(new Error("no keys")));
    await userEvent.click(screen.getByRole("button", { name: C.add }));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(C.failed, {
        variant: "error",
      }),
    );
    expect(changeTags.mutate).not.toHaveBeenCalled();
  });

  it("changes nothing when no selected scenario has a key", async () => {
    known = { data: ["vip"], isLoading: false, isError: false };
    renderMenu(() => Promise.resolve([null]));
    await userEvent.click(screen.getByRole("button", { name: C.add }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(changeTags.mutate).not.toHaveBeenCalled();
  });

  it("tells a failed tag load from an environment with no tags", () => {
    known = { data: [], isLoading: false, isError: true };
    renderMenu(() => Promise.resolve([]));
    expect(screen.getByText(C.unavailable)).toBeInTheDocument();
  });
});
