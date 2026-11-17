// Walk flow 8: Add rows > Generate rows with How many 3 and a Description kept
// "Generate and save" disabled with no reason while the model was empty, and
// nothing on the dialog said a model was needed. The append writer has no
// default model: it refuses a blank model and a blank description. The dialog
// must mark both as required and name what is missing beside the button.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import { DevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import ImmediateGenerateRows from "../ImmediateGenerateRows";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  refreshGrid: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: mocks.get, post: mocks.post, put: mocks.put },
}));
// Example rows and knowledge-base sources are a separate surface; keep it off.
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: () => false,
}));
// The picker module has only a default export; a labelled input stands in.
vi.mock("src/components/custom-model-dropdown/CustomModelDropdown", () => ({
  default: ({ value, onChange }) => (
    <input
      aria-label="Model"
      value={value}
      onChange={(event) =>
        onChange({ target: { value: { model_name: event.target.value } } })
      }
    />
  ),
}));

const MODEL_HELP = "Required. This model writes the new rows.";
const DESCRIPTION_HELP = "Required. Describe the rows to generate.";

function mount({ pending = false } = {}) {
  mocks.get.mockImplementation((path) =>
    pending
      ? new Promise(() => {})
      : Promise.resolve({
          data: {
            result: path.includes("synthetic-config")
              ? { data: {} }
              : {
                  metadata: { dataset_name: "Questions" },
                  column_config: [
                    {
                      id: "col",
                      name: "question",
                      data_type: "text",
                      origin_type: "OTHERS",
                    },
                  ],
                },
          },
        }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DevelopDetailContext.Provider
        value={{
          gridApi: null,
          setGridApi: () => {},
          refreshGrid: mocks.refreshGrid,
          setRefetchTable: () => {},
        }}
      >
        <ImmediateGenerateRows
          request={{ datasetId: "dataset", rowIds: [] }}
          onClose={vi.fn()}
        />
      </DevelopDetailContext.Provider>
    </QueryClientProvider>,
  );
}

const loaded = () =>
  waitFor(() =>
    expect(
      screen.queryByText("Loading configuration…"),
    ).not.toBeInTheDocument(),
  );
const generate = () =>
  screen.getByRole("button", { name: "Generate and save" });
const type = (role, name, value) =>
  fireEvent.change(screen.getByRole(role, { name }), { target: { value } });

beforeEach(() => {
  vi.clearAllMocks();
  mocks.post.mockResolvedValue({});
  mocks.put.mockResolvedValue({});
});
afterEach(() => {
  cleanup();
});

describe("generate rows required inputs", () => {
  it("marks the model as required and says why Generate and save is off without one", async () => {
    mount();
    await loaded();
    type("spinbutton", "How many", "3");
    type("textbox", "Description", "More short general knowledge questions");

    expect(screen.getByText(MODEL_HELP)).toBeInTheDocument();
    expect(generate()).toBeDisabled();
    expect(generate()).toHaveAccessibleDescription(
      "To generate rows, choose a model.",
    );
    expect(
      screen.getByText("To generate rows, choose a model."),
    ).toBeInTheDocument();

    type("textbox", "Model", "configured-model");
    expect(generate()).toBeEnabled();
    expect(generate()).not.toHaveAccessibleDescription();
    expect(screen.queryByText(/^To generate rows,/)).not.toBeInTheDocument();
    expect(screen.getByText(MODEL_HELP)).toBeInTheDocument();

    fireEvent.click(generate());
    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/model-hub/develops/dataset/add_synthetic_data/",
        expect.objectContaining({
          generation_mode: "append",
          model: "configured-model",
          num_rows: 3,
        }),
      ),
    );
    await screen.findByText(
      "3 generated rows saved. Existing rows are unchanged.",
    );
  });

  it("names every missing required input and agrees with the button state", async () => {
    mount();
    await loaded();

    expect(
      screen.getByRole("textbox", { name: "Description" }),
    ).toHaveAccessibleDescription(DESCRIPTION_HELP);
    expect(generate()).toBeDisabled();
    expect(generate()).toHaveAccessibleDescription(
      "To generate rows, add a description and choose a model.",
    );

    type("textbox", "Model", "configured-model");
    expect(generate()).toBeDisabled();
    expect(generate()).toHaveAccessibleDescription(
      "To generate rows, add a description.",
    );

    type("spinbutton", "How many", "0");
    expect(generate()).toHaveAccessibleDescription(
      "To generate rows, enter 1 to 100 rows and add a description.",
    );

    type("textbox", "Description", "Refunds");
    expect(generate()).toBeDisabled();
    expect(generate()).toHaveAccessibleDescription(
      "To generate rows, enter 1 to 100 rows.",
    );

    type("spinbutton", "How many", "2");
    expect(generate()).toBeEnabled();
    expect(screen.queryByText(/^To generate rows,/)).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("gives no missing-input reason while the configuration is still loading", () => {
    mount({ pending: true });

    expect(screen.getByText("Loading configuration…")).toBeInTheDocument();
    expect(generate()).toBeDisabled();
    expect(screen.queryByText(/^To generate rows,/)).not.toBeInTheDocument();
  });
});
