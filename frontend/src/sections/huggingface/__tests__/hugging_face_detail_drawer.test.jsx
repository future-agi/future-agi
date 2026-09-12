import { useForm } from "react-hook-form";
import { describe, expect, it, vi } from "vitest";
import { act, render, renderHook, waitFor } from "src/utils/test-utils";
import HuggingFaceDetailDrawer from "../HuggingFaceDetailDrawer";

vi.mock("../HuggingDetailForm", () => ({
  default: () => <div>Hugging Face detail form</div>,
}));

const createProps = (overrides = {}) => ({
  show: true,
  setShow: vi.fn(),
  huggingFaceDetail: { name: "future-agi/example-dataset" },
  subsetOptions: [],
  splitOptions: [],
  onSubmit: vi.fn(),
  onClose: vi.fn(),
  isLoadingCreateDataset: false,
  showNameField: false,
  ...overrides,
});

const renderDrawer = (overrides = {}, initialValues = {}) => {
  const { result } = renderHook(() =>
    useForm({
      defaultValues: {
        name: "",
        huggingface_dataset_config: "",
        huggingface_dataset_split: "",
        num_rows: "",
        ...initialValues,
      },
    }),
  );
  const props = createProps({
    control: result.current.control,
    getValues: result.current.getValues,
    setValue: result.current.setValue,
    watch: result.current.watch,
    ...overrides,
  });

  return {
    form: result.current,
    props,
    ...render(<HuggingFaceDetailDrawer {...props} />),
  };
};

describe("HuggingFaceDetailDrawer", () => {
  it("initializes subset and split after options load for an existing dataset", async () => {
    const { form, props, rerender } = renderDrawer();

    rerender(
      <HuggingFaceDetailDrawer
        {...props}
        subsetOptions={[{ label: "default", value: "default" }]}
      />,
    );

    await waitFor(() => {
      expect(form.getValues("huggingface_dataset_config")).toBe("default");
      expect(form.getValues("num_rows")).toBe(1);
    });

    rerender(
      <HuggingFaceDetailDrawer
        {...props}
        subsetOptions={[{ label: "default", value: "default" }]}
        splitOptions={[{ label: "train", value: "train" }]}
      />,
    );

    await waitFor(() => {
      expect(form.getValues()).toEqual({
        name: "",
        huggingface_dataset_config: "default",
        huggingface_dataset_split: "train",
        num_rows: 1,
      });
    });
  });

  it("also initializes the dataset name when creating a new dataset", async () => {
    const { form } = renderDrawer({
      showNameField: true,
      subsetOptions: [{ label: "default", value: "default" }],
      splitOptions: [{ label: "validation", value: "validation" }],
    });

    await waitFor(() => {
      expect(form.getValues()).toEqual({
        name: "future-agi/example-dataset",
        huggingface_dataset_config: "default",
        huggingface_dataset_split: "validation",
        num_rows: 1,
      });
    });
  });

  it("initializes available options before a new dataset name loads", async () => {
    const { form, props, rerender } = renderDrawer({
      showNameField: true,
      huggingFaceDetail: {},
      subsetOptions: [{ label: "default", value: "default" }],
      splitOptions: [{ label: "test", value: "test" }],
    });

    await waitFor(() => {
      expect(form.getValues()).toEqual({
        name: "",
        huggingface_dataset_config: "default",
        huggingface_dataset_split: "test",
        num_rows: 1,
      });
    });

    rerender(
      <HuggingFaceDetailDrawer
        {...props}
        huggingFaceDetail={{ name: "future-agi/delayed-dataset" }}
      />,
    );

    await waitFor(() => {
      expect(form.getValues("name")).toBe("future-agi/delayed-dataset");
    });
  });

  it("preserves valid manual selections and row count when options recompute", async () => {
    const subsetOptions = [
      { label: "default", value: "default" },
      { label: "english", value: "english" },
    ];
    const splitOptions = [
      { label: "train", value: "train" },
      { label: "test", value: "test" },
    ];
    const { form, props, rerender } = renderDrawer({
      subsetOptions,
      splitOptions,
    });

    await waitFor(() => {
      expect(form.getValues("huggingface_dataset_config")).toBe("default");
      expect(form.getValues("huggingface_dataset_split")).toBe("train");
    });

    act(() => {
      form.setValue("huggingface_dataset_config", "english");
      form.setValue("huggingface_dataset_split", "test");
      form.setValue("num_rows", 25);
    });

    rerender(
      <HuggingFaceDetailDrawer
        {...props}
        subsetOptions={subsetOptions.map((option) => ({ ...option }))}
        splitOptions={splitOptions.map((option) => ({ ...option }))}
      />,
    );

    await waitFor(() => {
      expect(form.getValues()).toEqual({
        name: "",
        huggingface_dataset_config: "english",
        huggingface_dataset_split: "test",
        num_rows: 25,
      });
    });
  });

  it("replaces selections that are no longer available", async () => {
    const { form } = renderDrawer(
      {
        subsetOptions: [{ label: "default", value: "default" }],
        splitOptions: [{ label: "train", value: "train" }],
      },
      {
        huggingface_dataset_config: "removed-subset",
        huggingface_dataset_split: "removed-split",
        num_rows: 50,
      },
    );

    await waitFor(() => {
      expect(form.getValues()).toEqual({
        name: "",
        huggingface_dataset_config: "default",
        huggingface_dataset_split: "train",
        num_rows: 50,
      });
    });
  });

  it("does not initialize form values while the drawer is closed", () => {
    const { form } = renderDrawer({
      show: false,
      subsetOptions: [{ label: "default", value: "default" }],
      splitOptions: [{ label: "test", value: "test" }],
    });

    expect(form.getValues()).toEqual({
      name: "",
      huggingface_dataset_config: "",
      huggingface_dataset_split: "",
      num_rows: "",
    });
  });
});
