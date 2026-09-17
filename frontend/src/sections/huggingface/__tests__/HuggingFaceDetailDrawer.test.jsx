import React from "react";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import HuggingFaceDetailDrawer from "../HuggingFaceDetailDrawer";

/* eslint-disable react/prop-types -- lightweight UI mocks for this unit test */
vi.mock("@mui/material", () => ({
  Drawer: ({ children, open }) => (open ? <div>{children}</div> : null),
  IconButton: ({ children, onClick }) => (
    <button type="button" onClick={onClick}>
      {children}
    </button>
  ),
}));
/* eslint-enable react/prop-types */

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span>{icon}</span>,
}));

vi.mock("../HuggingDetailForm", () => ({
  default: () => <div />,
}));

const renderDrawer = ({
  showNameField = false,
  huggingFaceDetail,
  watch = vi.fn(() => ({})),
} = {}) => {
  const reset = vi.fn();

  render(
    <HuggingFaceDetailDrawer
      show
      setShow={vi.fn()}
      reset={reset}
      control={{}}
      huggingFaceDetail={huggingFaceDetail}
      watch={watch}
      subsetOptions={[
        { label: "default", value: "default" },
        { label: "custom", value: "custom" },
      ]}
      splitOptions={[{ label: "train", value: "train" }]}
      onSubmit={vi.fn()}
      onClose={vi.fn()}
      isLoadingCreateDataset={false}
      showNameField={showNameField}
    />,
  );

  return reset;
};

describe("HuggingFaceDetailDrawer", () => {
  it("initializes import options when adding rows to an existing dataset", () => {
    const reset = renderDrawer({
      huggingFaceDetail: { name: "existing-dataset" },
    });

    expect(reset).toHaveBeenCalledWith({
      huggingface_dataset_config: "default",
      huggingface_dataset_split: "train",
      num_rows: 1,
    });
  });

  it("does not overwrite selected import options when the effect reruns", () => {
    const reset = renderDrawer({
      watch: vi.fn(() => ({
        huggingface_dataset_config: "custom",
        huggingface_dataset_split: "train",
        num_rows: 3,
      })),
    });

    expect(reset).not.toHaveBeenCalled();
  });

  it("initializes the dataset name only for the create-new flow", () => {
    const reset = renderDrawer({
      showNameField: true,
      huggingFaceDetail: { name: "new-dataset" },
    });

    expect(reset).toHaveBeenCalledWith({
      name: "new-dataset",
      huggingface_dataset_config: "default",
      huggingface_dataset_split: "train",
      num_rows: 1,
    });
  });
});
