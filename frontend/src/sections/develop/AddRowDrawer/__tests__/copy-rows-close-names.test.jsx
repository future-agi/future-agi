// "Copy rows from another dataset" and its "Create New Column" dialog close
// with icon-only buttons. Each one needs a name a screen reader announces and
// a user can find it by. The panel sits inside drawers that already have their
// own "Close" button, so its name says what it closes.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useController } from "react-hook-form";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import ExistingDatasetModal from "../ExistingDatasetModal";

const h = vi.hoisted(() => ({
  // Stable references: the panel's effect keys on these arrays.
  datasets: [
    { datasetId: "cur", name: "Current" },
    { datasetId: "seed-1", name: "Seed" },
  ],
  columns: {
    cur: [{ id: "cur-q", name: "question", data_type: "text" }],
    "seed-1": [{ id: "src-q", name: "question", data_type: "text" }],
  },
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  const pending = () => new Promise(() => {});
  return {
    ...real,
    default: {
      ...real.default,
      get: pending,
      post: pending,
    },
  };
});
vi.mock("src/api/develop/develop-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useDevelopDatasetList: () => ({ data: h.datasets }),
  useGetDatasetColumns: (dataset, options) => ({
    data: options?.enabled ? h.columns[dataset] : undefined,
    isLoading: false,
  }),
}));
/* eslint-disable react/prop-types */
// The searchable picker, as a plain select on the same form field.
vi.mock("src/components/FromSearchSelectField", async (importOriginal) => ({
  ...(await importOriginal()),
  FormSearchSelectFieldControl: function SelectStub({
    control,
    fieldName,
    options,
    label,
  }) {
    const { field } = useController({ control, name: fieldName });
    return (
      <select
        aria-label={label}
        value={field.value ?? ""}
        onChange={(e) => field.onChange(e.target.value)}
      >
        <option value="">Select Option</option>
        {(options ?? []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  },
}));
// Each mapping row, reduced to the door that opens "Create New Column".
vi.mock(
  "src/sections/develop-detail/Common/FieldSelection",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: function CreateColumnDoor({ createLabel, handleCreateLabel }) {
      return createLabel ? (
        <button type="button" onClick={handleCreateLabel}>
          {createLabel}
        </button>
      ) : null;
    },
  }),
);
/* eslint-enable react/prop-types */
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: () => {},
}));
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: () => {},
}));

const renderPanel = ({ onClose }) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ExistingDatasetModal
        open
        onClose={onClose}
        refreshGrid={() => {}}
        datasetId="cur"
        closeDrawer={() => {}}
      />
    </QueryClientProvider>,
  );
};

afterEach(() => {
  cleanup();
});

describe("Copy rows close buttons have names", () => {
  it("U08-1: the panel's icon-only close button is named and closes the panel", () => {
    const onClose = vi.fn();
    renderPanel({ onClose });

    const close = screen.getByRole("button", { name: "Close copy rows" });
    screen
      .getAllByRole("button")
      .forEach((button) => expect(button).toHaveAccessibleName());

    fireEvent.click(close);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("U08-2: the Create New Column dialog's icon-only close button is named and closes only the dialog", async () => {
    const onClose = vi.fn();
    renderPanel({ onClose });

    fireEvent.change(screen.getByLabelText("Choose a dataset"), {
      target: { value: "seed-1" },
    });
    fireEvent.click(
      await screen.findByRole("button", { name: "Create New Column" }),
    );

    const dialog = await screen.findByRole("dialog");
    const close = within(dialog).getByRole("button", {
      name: "Close new column",
    });
    within(dialog)
      .getAllByRole("button")
      .forEach((button) => expect(button).toHaveAccessibleName());

    fireEvent.click(close);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // The copy panel stays open behind it.
    expect(onClose).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Close copy rows" }),
    ).toBeInTheDocument();
  });
});
