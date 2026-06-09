import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import ExportToDatasetDialog from "../export-to-dataset-dialog";

const mocks = vi.hoisted(() => ({
  exportMutate: vi.fn(),
  fieldsLoading: false,
  datasetsLoading: false,
  datasetColumnsLoading: false,
  exportPending: false,
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

const exportFields = {
  fields: [
    {
      id: "source_type",
      label: "Source type",
      column: "source_type",
      data_type: "text",
      group: "Source",
      default: true,
    },
    {
      id: "eval_metrics",
      label: "Eval metrics",
      column: "eval_metrics",
      data_type: "json",
      group: "Evals",
      default: true,
    },
    {
      id: "annotation_metrics",
      label: "Annotation metrics",
      column: "annotation_metrics",
      data_type: "json",
      group: "Annotations",
      default: true,
    },
    {
      id: "label:label-1:slot:1:value",
      label: "thumbs annotation 1 score",
      column: "thumbs annotation 1 score",
      data_type: "text",
      group: "Annotations",
      default: true,
    },
    {
      id: "label:label-1:slot:1:annotator_email",
      label: "thumbs annotation 1 annotator email",
      column: "thumbs annotation 1 annotator email",
      data_type: "text",
      group: "Annotations",
      default: true,
    },
    {
      id: "label:label-1:slot:1:annotator_name",
      label: "thumbs annotation 1 annotator name",
      column: "thumbs annotation 1 annotator name",
      data_type: "text",
      group: "Annotations",
      default: false,
    },
    {
      id: "label:label-1:slot:1:notes",
      label: "thumbs annotation 1 notes",
      column: "thumbs annotation 1 notes",
      data_type: "text",
      group: "Annotations",
      default: false,
    },
    {
      id: "label:label-1:annotation_columns",
      label: "thumbs annotation columns",
      column: "thumbs annotation columns",
      data_type: "json",
      group: "Annotations",
      default: false,
      expand_fields: [
        "label:label-1:slot:1:value",
        "label:label-1:slot:1:annotator_name",
        "label:label-1:slot:1:notes",
      ],
    },
    {
      id: "attr:span_attributes.customer.tier",
      label: "span attributes customer tier",
      column: "span_attributes.customer.tier",
      data_type: "text",
      group: "Attributes",
      default: false,
    },
  ],
  default_mapping: [
    {
      field: "source_type",
      column: "source_type",
      data_type: "text",
      enabled: true,
    },
    {
      field: "eval_metrics",
      column: "eval_metrics",
      data_type: "json",
      enabled: true,
    },
    {
      field: "annotation_metrics",
      column: "annotation_metrics",
      data_type: "json",
      enabled: true,
    },
    {
      field: "label:label-1:slot:1:value",
      column: "thumbs annotation 1 score",
      enabled: true,
    },
    {
      field: "label:label-1:slot:1:annotator_email",
      column: "thumbs annotation 1 annotator email",
      enabled: true,
    },
  ],
};

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useAnnotationQueueExportFields: () => ({
    data: exportFields,
    isLoading: mocks.fieldsLoading,
  }),
  useExportToDataset: () => ({
    mutate: mocks.exportMutate,
    isPending: mocks.exportPending,
  }),
}));

vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [
      { datasetId: "dataset-1", name: "Production traces" },
      { id: "dataset-2", name: "Voice QA exports" },
    ],
    isLoading: mocks.datasetsLoading,
    isFetching: false,
  }),
  useGetDatasetColumns: () => ({
    data: [
      { id: "col-1", name: "existing_source_type" },
      { id: "col-2", name: "existing_annotation_score" },
    ],
    isLoading: mocks.datasetColumnsLoading,
  }),
}));

describe("ExportToDatasetDialog", () => {
  beforeEach(() => {
    mocks.fieldsLoading = false;
    mocks.datasetsLoading = false;
    mocks.datasetColumnsLoading = false;
    mocks.exportPending = false;
  });

  it("opens as a right drawer with annotation and eval defaults selected", async () => {
    render(<ExportToDatasetDialog open onClose={() => {}} queueId="queue-1" />);

    expect(
      await screen.findByTestId("export-to-dataset-drawer"),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("Eval metrics")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Annotation metrics")).toBeInTheDocument();
    expect(
      screen.getAllByDisplayValue("thumbs annotation 1 annotator email").length,
    ).toBeGreaterThan(0);
    expect(screen.queryByLabelText("Type")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("export-mapping-row")).toHaveLength(5);
  });

  it("lets users add and remove custom attribute columns", async () => {
    const user = userEvent.setup();
    render(<ExportToDatasetDialog open onClose={() => {}} queueId="queue-1" />);

    await user.click(screen.getByRole("button", { name: /add column/i }));
    expect(screen.getAllByTestId("export-mapping-row")).toHaveLength(6);
    expect(screen.getByLabelText("Attribute path")).toBeInTheDocument();

    const removeButtons = screen.getAllByRole("button", {
      name: "Remove column",
    });
    await user.click(removeButtons[removeButtons.length - 1]);
    expect(screen.getAllByTestId("export-mapping-row")).toHaveLength(5);
  });

  it("lets users search the source field list", async () => {
    const user = userEvent.setup();
    render(<ExportToDatasetDialog open onClose={() => {}} queueId="queue-1" />);

    await user.click(screen.getByRole("button", { name: /add column/i }));
    const sourceField = screen.getAllByLabelText("Source field").at(-1);
    await user.click(sourceField);
    await user.clear(sourceField);
    await user.type(sourceField, "annotator name");

    const sourceOption = await screen.findByRole("option", {
      name: /thumbs annotation 1 annotator name/i,
    });
    expect(sourceOption).toBeInTheDocument();
    expect(sourceOption).toHaveAttribute(
      "title",
      "thumbs annotation 1 annotator name",
    );
    expect(
      screen.queryByRole("option", {
        name: /^source type$/i,
      }),
    ).not.toBeInTheDocument();
  });

  it("shows attributes without the span_attributes prefix", async () => {
    const user = userEvent.setup();
    render(<ExportToDatasetDialog open onClose={() => {}} queueId="queue-1" />);

    await user.click(screen.getByRole("button", { name: /add column/i }));
    const sourceField = screen.getAllByLabelText("Source field").at(-1);
    await user.click(sourceField);
    await user.clear(sourceField);
    await user.type(sourceField, "customer");

    const attributeOption = await screen.findByRole("option", {
      name: /^customer\.tier$/i,
    });
    expect(attributeOption).toBeInTheDocument();
    expect(attributeOption).toHaveAttribute("title", "customer.tier");
    expect(
      screen.queryByRole("option", {
        name: /^span_attributes\.customer\.tier$/i,
      }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("option", {
        name: /span attributes customer tier/i,
      }),
    ).not.toBeInTheDocument();

    await user.click(attributeOption);
    expect(screen.getAllByDisplayValue("customer.tier").length).toBeGreaterThan(
      1,
    );
  });

  it("shows a loading state while source fields load", () => {
    mocks.fieldsLoading = true;
    render(<ExportToDatasetDialog open onClose={() => {}} queueId="queue-1" />);

    expect(
      screen.getByRole("progressbar", { name: /loading export drawer/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("progressbar", { name: /loading source fields/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add column/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Export" })).toBeDisabled();
  });

  it("shows export progress while queue data is being mapped", () => {
    mocks.exportPending = true;
    render(<ExportToDatasetDialog open onClose={() => {}} queueId="queue-1" />);

    expect(screen.getByRole("status")).toHaveTextContent(
      /loading queue data and mapping selected columns/i,
    );
    expect(
      screen.getByRole("progressbar", { name: /exporting annotation data/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /exporting/i })).toBeDisabled();
  });

  it("expands annotation bundles into individual dataset columns", async () => {
    const user = userEvent.setup();
    render(<ExportToDatasetDialog open onClose={() => {}} queueId="queue-1" />);

    await user.click(screen.getByRole("button", { name: /add column/i }));
    await user.click(screen.getAllByLabelText("Source field").at(-1));
    await user.click(
      await screen.findByRole("option", {
        name: /thumbs annotation columns/i,
      }),
    );

    expect(
      screen.getAllByDisplayValue("thumbs annotation 1 annotator name").length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByDisplayValue("thumbs annotation 1 notes").length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByTestId("export-mapping-row")).toHaveLength(8);
  });

  it("submits the selected editable mapping", async () => {
    const user = userEvent.setup();
    mocks.exportMutate.mockClear();
    render(<ExportToDatasetDialog open onClose={() => {}} queueId="queue-1" />);

    await user.type(screen.getByLabelText(/Dataset name/), "Annotated export");
    await user.click(screen.getByRole("button", { name: "Export" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      /loading queue data and mapping selected columns/i,
    );
    expect(screen.getByRole("button", { name: /exporting/i })).toBeDisabled();
    await waitFor(() =>
      expect(mocks.exportMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          queueId: "queue-1",
          dataset_name: "Annotated export",
          status_filter: "completed",
          column_mapping: expect.arrayContaining([
            expect.objectContaining({ field: "eval_metrics" }),
            expect.objectContaining({ field: "annotation_metrics" }),
            expect.objectContaining({
              field: "label:label-1:slot:1:annotator_email",
            }),
          ]),
        }),
        expect.any(Object),
      ),
    );
    expect(
      mocks.exportMutate.mock.calls[0][0].column_mapping[0],
    ).not.toHaveProperty("data_type");
  });

  it("lets users choose an existing dataset without pasting a dataset id", async () => {
    const user = userEvent.setup();
    mocks.exportMutate.mockClear();
    render(<ExportToDatasetDialog open onClose={() => {}} queueId="queue-1" />);

    await user.click(screen.getByLabelText(/Add to existing dataset/i));

    expect(screen.queryByLabelText(/Dataset ID/i)).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("combobox", { name: /^Dataset\s*\*?$/i }),
    );
    const datasetOption = await screen.findByRole("option", {
      name: /Production traces/i,
    });
    expect(datasetOption).toHaveAttribute("title", "Production traces");
    expect(datasetOption).not.toHaveTextContent("dataset-1");
    await user.click(datasetOption);

    await user.click(screen.getAllByLabelText("Dataset column")[0]);
    const columnOption = await screen.findByRole("option", {
      name: /existing_source_type/i,
    });
    expect(columnOption).toHaveAttribute("title", "existing_source_type");
    await user.click(columnOption);

    await user.click(screen.getByRole("button", { name: "Export" }));

    await waitFor(() =>
      expect(mocks.exportMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          queueId: "queue-1",
          dataset_id: "dataset-1",
          column_mapping: expect.arrayContaining([
            expect.objectContaining({
              field: "source_type",
              column: "existing_source_type",
            }),
          ]),
        }),
        expect.any(Object),
      ),
    );
  });
});
