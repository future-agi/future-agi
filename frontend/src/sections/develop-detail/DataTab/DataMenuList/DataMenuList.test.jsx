// W10-EXPERIMENTS-DATASETS-U03: selection bar > Add to dataset > Add to new
// dataset. The dialog stays open while the rows are copied (its Add button
// busy) and closes once they are in. A refused or failed request keeps the
// dialog and the typed name, and says why above the actions.
import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
} from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { palette } from "src/theme/palette";
import DataMenuList from "./DataMenuList";

vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [
      { datasetId: "support-chats", name: "Support chats" },
      { datasetId: "q2-eval", name: "Q2 eval" },
    ],
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner", user: {} }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: () => {},
  resetUser: () => {},
  Events: {},
  PropertyName: {},
}));

const theme = createTheme({ palette: palette("light") });

// Wired the way the selection bar wires it (DevelopDataSelectionActive):
// the create mutation's `mutate` and `isPending`.
function AddToDataset({ createRows }) {
  const { mutate, isPending } = useMutation({ mutationFn: createRows });
  return (
    <DataMenuList
      setName={() => {}}
      onCreateDatasetRows={mutate}
      onMergeRows={() => {}}
      setTargetDatasetId={() => {}}
      loading={isPending}
    />
  );
}
AddToDataset.propTypes = { createRows: PropTypes.func.isRequired };

function renderAddToDataset(createRows) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter>
          <AddToDataset createRows={createRows} />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

const nameField = (dialog) =>
  within(dialog).getByRole("textbox", { name: "Dataset Name" });
const addButton = (dialog) =>
  within(dialog).getByRole("button", { name: /Add Dataset/ });

async function addToNewDataset(name) {
  fireEvent.click(screen.getByRole("button", { name: "Add to dataset" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(nameField(dialog), { target: { value: name } });
  await waitFor(() => expect(addButton(dialog)).toBeEnabled());
  fireEvent.click(addButton(dialog));
  return dialog;
}

describe("Add to dataset > new dataset (W10-EXPERIMENTS-DATASETS-U03)", () => {
  it("stays open with the typed name and a busy Add button until the rows are in", async () => {
    let finish;
    const createRows = vi.fn(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    renderAddToDataset(createRows);
    const dialog = await addToNewDataset("Q3 eval");

    await waitFor(() => expect(createRows).toHaveBeenCalledTimes(1));
    expect(createRows.mock.calls[0][0]).toBe("Q3 eval");
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(nameField(dialog)).toHaveValue("Q3 eval");
    await waitFor(() => expect(addButton(dialog)).toBeDisabled());
    expect(nameField(dialog)).toHaveValue("Q3 eval");

    await act(async () => {
      finish({ data: { result: {} } });
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("a refused name keeps the dialog and the typed name, and says why", async () => {
    const createRows = vi.fn().mockRejectedValue({
      statusCode: 400,
      result: "A dataset named Q3 eval already exists.",
    });
    renderAddToDataset(createRows);
    const dialog = await addToNewDataset("Q3 eval");

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      'Couldn\'t create "Q3 eval": A dataset named Q3 eval already exists.',
    );
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(nameField(dialog)).toHaveValue("Q3 eval");
    await waitFor(() => expect(addButton(dialog)).toBeEnabled());
  });

  it("a failure with no reason keeps the name, says to try again, and the retry closes it", async () => {
    const createRows = vi
      .fn()
      .mockRejectedValueOnce({ statusCode: 502 })
      .mockResolvedValueOnce({ data: { result: {} } });
    renderAddToDataset(createRows);
    const dialog = await addToNewDataset("Q3 eval");

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      'Couldn\'t create "Q3 eval". Try again.',
    );
    expect(nameField(dialog)).toHaveValue("Q3 eval");

    await waitFor(() => expect(addButton(dialog)).toBeEnabled());
    fireEvent.click(addButton(dialog));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(createRows).toHaveBeenCalledTimes(2);
    expect(createRows.mock.calls[1][0]).toBe("Q3 eval");
  });
});
