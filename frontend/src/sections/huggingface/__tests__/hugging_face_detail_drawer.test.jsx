import { describe, expect, it, vi } from "vitest";
import { render, waitFor } from "src/utils/test-utils";
import HuggingFaceDetailDrawer from "../HuggingFaceDetailDrawer";

vi.mock("../HuggingDetailForm", () => ({
  default: () => <div>Hugging Face detail form</div>,
}));

const createProps = (overrides = {}) => ({
  show: true,
  setShow: vi.fn(),
  reset: vi.fn(),
  control: {},
  huggingFaceDetail: { name: "future-agi/example-dataset" },
  watch: vi.fn(),
  subsetOptions: [],
  splitOptions: [],
  onSubmit: vi.fn(),
  onClose: vi.fn(),
  isLoadingCreateDataset: false,
  showNameField: false,
  ...overrides,
});

describe("HuggingFaceDetailDrawer", () => {
  it("initializes subset and split after options load for an existing dataset", async () => {
    const props = createProps();
    const { rerender } = render(<HuggingFaceDetailDrawer {...props} />);

    rerender(
      <HuggingFaceDetailDrawer
        {...props}
        subsetOptions={[{ label: "default", value: "default" }]}
      />,
    );

    await waitFor(() => {
      expect(props.reset).toHaveBeenLastCalledWith({
        huggingface_dataset_config: "default",
        num_rows: 1,
      });
    });

    rerender(
      <HuggingFaceDetailDrawer
        {...props}
        subsetOptions={[{ label: "default", value: "default" }]}
        splitOptions={[{ label: "train", value: "train" }]}
      />,
    );

    await waitFor(() => {
      expect(props.reset).toHaveBeenLastCalledWith({
        huggingface_dataset_config: "default",
        huggingface_dataset_split: "train",
        num_rows: 1,
      });
    });
  });

  it("also initializes the dataset name when creating a new dataset", async () => {
    const props = createProps({
      showNameField: true,
      subsetOptions: [{ label: "default", value: "default" }],
      splitOptions: [{ label: "validation", value: "validation" }],
    });

    render(<HuggingFaceDetailDrawer {...props} />);

    await waitFor(() => {
      expect(props.reset).toHaveBeenCalledWith({
        name: "future-agi/example-dataset",
        huggingface_dataset_config: "default",
        huggingface_dataset_split: "validation",
        num_rows: 1,
      });
    });
  });

  it("initializes available options before a new dataset name loads", async () => {
    const props = createProps({
      showNameField: true,
      huggingFaceDetail: {},
      subsetOptions: [{ label: "default", value: "default" }],
      splitOptions: [{ label: "test", value: "test" }],
    });

    render(<HuggingFaceDetailDrawer {...props} />);

    await waitFor(() => {
      expect(props.reset).toHaveBeenCalledWith({
        huggingface_dataset_config: "default",
        huggingface_dataset_split: "test",
        num_rows: 1,
      });
    });
  });

  it("does not initialize form values while the drawer is closed", () => {
    const props = createProps({
      show: false,
      subsetOptions: [{ label: "default", value: "default" }],
      splitOptions: [{ label: "test", value: "test" }],
    });

    render(<HuggingFaceDetailDrawer {...props} />);

    expect(props.reset).not.toHaveBeenCalled();
  });
});
