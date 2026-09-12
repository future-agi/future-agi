import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { enqueueSnackbar } from "notistack";
import ReplaceMediaDialog from "../ReplaceMediaDialog";
import { NOT_A_WEB_ADDRESS_MESSAGE } from "../editHelper";

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-color" />,
}));

vi.mock("src/components/iconify", () => ({
  default: () => null,
}));

vi.mock("src/components/hook-form", () => ({
  RHFUpload: ({ onDrop }) => (
    <div data-testid="upload-dropzone">
      <button
        type="button"
        onClick={() =>
          onDrop([
            new File(["hello"], "notes.exe", {
              type: "application/x-msdownload",
            }),
          ])
        }
      >
        drop-invalid
      </button>
      <button
        type="button"
        onClick={() =>
          onDrop([
            new File(["%PDF"], "report.pdf", { type: "application/pdf" }),
          ])
        }
      >
        drop-pdf
      </button>
    </div>
  ),
}));

describe("ReplaceMediaDialog Link tab (#2433)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const renderDialog = () => {
    const onUpload = vi.fn();
    const onClose = vi.fn();
    render(
      <ReplaceMediaDialog open onClose={onClose} onUpload={onUpload} />,
    );
    return { onUpload, onClose };
  };

  it("refuses a value that is not a web address and says so", async () => {
    const user = userEvent.setup();
    const { onUpload, onClose } = renderDialog();

    await user.click(screen.getByRole("tab", { name: "Link" }));
    await user.type(screen.getByLabelText("Link"), "sssss");
    await user.click(screen.getByRole("button", { name: "Upload" }));

    expect(enqueueSnackbar).toHaveBeenCalledWith(NOT_A_WEB_ADDRESS_MESSAGE, {
      variant: "error",
    });
    expect(onUpload).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("accepts a working document address with no new message", async () => {
    const user = userEvent.setup();
    const { onUpload } = renderDialog();
    const url = "https://example.com/report.pdf";

    await user.click(screen.getByRole("tab", { name: "Link" }));
    await user.type(screen.getByLabelText("Link"), url);
    await user.click(screen.getByRole("button", { name: "Upload" }));

    expect(onUpload).toHaveBeenCalledWith(url);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });
});

describe("ReplaceMediaDialog Upload tab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("still refuses a file of the wrong type with a named reason", async () => {
    const user = userEvent.setup();
    const onUpload = vi.fn();
    render(<ReplaceMediaDialog open onClose={vi.fn()} onUpload={onUpload} />);

    await user.click(screen.getByRole("button", { name: "drop-invalid" }));

    expect(enqueueSnackbar).toHaveBeenCalledWith("Invalid file type: notes.exe", {
      variant: "error",
    });
    expect(onUpload).not.toHaveBeenCalled();
  });

  it("still accepts a pdf from disk", async () => {
    const user = userEvent.setup();
    const onUpload = vi.fn();
    render(<ReplaceMediaDialog open onClose={vi.fn()} onUpload={onUpload} />);

    await user.click(screen.getByRole("button", { name: "drop-pdf" }));
    await user.click(screen.getByRole("button", { name: "Upload" }));

    expect(onUpload).toHaveBeenCalledTimes(1);
    expect(onUpload.mock.calls[0][0]).toBeInstanceOf(File);
    expect(onUpload.mock.calls[0][0].name).toBe("report.pdf");
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });
});
