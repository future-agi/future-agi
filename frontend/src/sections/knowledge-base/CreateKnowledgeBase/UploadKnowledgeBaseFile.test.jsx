import { describe, it, expect, vi, beforeEach } from "vitest";

let capturedOnDrop = null;

vi.mock("react-dropzone", () => ({
  useDropzone: vi.fn((props) => {
    capturedOnDrop = props.onDrop;
    return {
      getRootProps: () => ({}),
      getInputProps: () => ({}),
      isDragActive: false,
      isDragReject: false,
      fileRejections: [],
    };
  }),
}));

const mockEnqueue = vi.fn();
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mockEnqueue }),
}));

import React from "react";
import { render, screen, act } from "@testing-library/react";
import { useForm, FormProvider } from "react-hook-form";
import UploadKnowledgeBaseFile from "./UploadKnowledgeBaseFile";

const mockShowSdkInfo = vi.fn();

let methods;

function TestWrapper() {
  methods = useForm({
    defaultValues: { file: { file: [] } },
  });
  return React.createElement(
    FormProvider,
    { ...methods },
    React.createElement(UploadKnowledgeBaseFile, {
      control: methods.control,
      handleShowSdkInfo: mockShowSdkInfo,
      isPending: false,
    }),
  );
}

function createFile(name, content, type) {
  return new File([content], name, { type });
}

function triggerDrop(acceptedFiles, fileRejections) {
  act(() => {
    capturedOnDrop(acceptedFiles, fileRejections);
  });
}

describe("UploadKnowledgeBaseFile", () => {
  beforeEach(() => {
    capturedOnDrop = null;
    mockEnqueue.mockClear();
    mockShowSdkInfo.mockClear();
  });

  it("renders the upload dropzone", () => {
    render(React.createElement(TestWrapper));
    expect(
      screen.getByText("Choose a file or drag & drop it here"),
    ).toBeTruthy();
  });

  it("rejects a 0-byte file and shows an error snackbar", () => {
    render(React.createElement(TestWrapper));

    const emptyFile = createFile("empty.pdf", "", "application/pdf");
    const rejection = {
      file: emptyFile,
      errors: [
        { code: "file-too-small", message: "File is smaller than 1 bytes" },
      ],
    };

    triggerDrop([], [rejection]);

    expect(mockEnqueue).toHaveBeenCalledWith(
      expect.stringContaining("empty.pdf"),
      { variant: "error" },
    );
    // Rejected file is not added to the list
    expect(methods.getValues("file").file).toHaveLength(0);
  });

  it("adds a valid file to the list", () => {
    render(React.createElement(TestWrapper));

    const validFile = createFile("doc.pdf", "%PDF-1.4", "application/pdf");
    triggerDrop([validFile], []);

    expect(mockEnqueue).not.toHaveBeenCalled();
    const updated = methods.getValues("file").file;
    expect(updated).toHaveLength(1);
    expect(updated[0].status).toBe("not_started");
    expect(updated[0].item.name).toBe("doc.pdf");
  });

  it("does not add a rejected file alongside an accepted one", () => {
    render(React.createElement(TestWrapper));

    const validFile = createFile("good.txt", "content", "text/plain");
    const rejection = {
      file: createFile("bad.pdf", "", "application/pdf"),
      errors: [
        { code: "file-too-small", message: "File is smaller than 1 bytes" },
      ],
    };

    triggerDrop([validFile], [rejection]);

    expect(mockEnqueue).toHaveBeenCalledTimes(1);
    const updated = methods.getValues("file").file;
    expect(updated).toHaveLength(1);
    expect(updated[0].item.name).toBe("good.txt");
  });

  it("shows SDK info dialog and toast for an oversized file", () => {
    render(React.createElement(TestWrapper));

    const rejection = {
      file: createFile(
        "big.pdf",
        "x".repeat(6 * 1024 * 1024),
        "application/pdf",
      ),
      errors: [
        {
          code: "file-too-large",
          message: "File is larger than 5242880 bytes",
        },
      ],
    };

    triggerDrop([], [rejection]);

    expect(mockShowSdkInfo).toHaveBeenCalledTimes(1);
    expect(mockEnqueue).toHaveBeenCalledWith(
      "File size is too large. Please upload a file under 5 MB.",
      { variant: "error" },
    );
    // Oversized file is not added to the list (does not block Create)
    expect(methods.getValues("file").file).toHaveLength(0);
  });

  it("shows friendly message for file-invalid-type", () => {
    render(React.createElement(TestWrapper));

    const rejection = {
      file: createFile("notes.txt", "content", "image/png"),
      errors: [
        { code: "file-invalid-type", message: "File type must be .pdf,.docx" },
      ],
    };

    triggerDrop([], [rejection]);

    expect(mockEnqueue).toHaveBeenCalledWith(
      "Unsupported file type. Please upload a PDF, DOCX, RTF, or TXT file.",
      { variant: "error" },
    );
  });

  it("does not crash on rejection item with null file", () => {
    render(React.createElement(TestWrapper));

    const rejection = {
      errors: [
        { code: "file-too-small", message: "File is smaller than 1 bytes" },
      ],
    };

    expect(() => triggerDrop([], [rejection])).not.toThrow();
    // Nothing is added, nothing crashes
    expect(methods.getValues("file").file).toHaveLength(0);
  });

  it("does not crash on rejection item with no errors array", () => {
    render(React.createElement(TestWrapper));

    const rejection = {
      file: createFile("unknown.pdf", "", "application/pdf"),
    };

    expect(() => triggerDrop([], [rejection])).not.toThrow();
    expect(mockEnqueue).toHaveBeenCalledTimes(1);
  });

  it("does not crash when errors is null", () => {
    render(React.createElement(TestWrapper));

    const rejection = {
      file: createFile("doc.txt", "", "text/plain"),
      errors: null,
    };

    expect(() => triggerDrop([], [rejection])).not.toThrow();
    expect(mockEnqueue).toHaveBeenCalledTimes(1);
  });

  it("passes minSize and maxSize to useDropzone", async () => {
    render(React.createElement(TestWrapper));

    const { useDropzone } = await import("react-dropzone");
    expect(useDropzone).toHaveBeenCalledWith(
      expect.objectContaining({
        minSize: 1,
        maxSize: 5 * 1024 * 1024,
      }),
    );
  });
});
