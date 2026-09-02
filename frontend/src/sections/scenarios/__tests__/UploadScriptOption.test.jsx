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
import PropTypes from "prop-types";
import { render, screen, act, waitFor } from "@testing-library/react";
import { useForm, FormProvider } from "react-hook-form";
import UploadScriptOption from "../UploadScriptOption";

function TestWrapper({ children }) {
  const methods = useForm({
    defaultValues: { "config.scriptUrl": null },
  });
  return React.createElement(FormProvider, { ...methods }, children);
}

TestWrapper.propTypes = {
  children: PropTypes.node,
};

function createFile(name, content, type) {
  return new File([content], name, { type });
}

function triggerDrop(acceptedFiles, fileRejections) {
  act(() => {
    capturedOnDrop(acceptedFiles, fileRejections);
  });
}

describe("UploadScriptOption", () => {
  beforeEach(() => {
    capturedOnDrop = null;
    mockEnqueue.mockClear();
  });

  it("renders the upload dropzone", () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );
    expect(screen.getByText("Upload Script")).toBeTruthy();
  });

  it("rejects a 0-byte file and shows an error snackbar", () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );

    const emptyFile = createFile("empty.pdf", "", "application/pdf");
    const rejection = {
      file: emptyFile,
      errors: [
        { code: "file-too-small", message: "File is smaller than 1 bytes" },
      ],
    };

    triggerDrop([], [rejection]);

    expect(mockEnqueue).toHaveBeenCalledTimes(1);
    expect(mockEnqueue).toHaveBeenCalledWith(
      expect.stringContaining("empty.pdf"),
      { variant: "error" },
    );
  });

  it("accepts a valid file without showing an error", () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );

    const validFile = createFile("script.txt", "print('hello')", "text/plain");
    triggerDrop([validFile], []);

    expect(mockEnqueue).not.toHaveBeenCalled();
  });

  it("shows snackbar for each rejected file", () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );

    const rejection1 = {
      file: createFile("empty1.txt", "", "text/plain"),
      errors: [
        { code: "file-too-small", message: "File is smaller than 1 bytes" },
      ],
    };
    const rejection2 = {
      file: createFile("empty2.txt", "", "text/plain"),
      errors: [
        { code: "file-too-small", message: "File is smaller than 1 bytes" },
      ],
    };

    triggerDrop([], [rejection1, rejection2]);

    expect(mockEnqueue).toHaveBeenCalledTimes(2);
  });

  it("processes an accepted file alongside a rejected one", async () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );

    const validFile = createFile("good.txt", "content", "text/plain");
    const rejection = {
      file: createFile("bad.pdf", "", "application/pdf"),
      errors: [
        { code: "file-too-small", message: "File is smaller than 1 bytes" },
      ],
    };

    triggerDrop([validFile], [rejection]);

    // Rejection shows an error
    expect(mockEnqueue).toHaveBeenCalledTimes(1);
    expect(mockEnqueue).toHaveBeenCalledWith(
      expect.stringContaining("bad.pdf"),
      { variant: "error" },
    );

    // Accepted file is still processed — it appears in the UI
    await waitFor(() => {
      expect(screen.getByText("good.txt")).toBeTruthy();
    });
  });

  it("does not crash when fileRejections is null", () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );

    const validFile = createFile("doc.txt", "text", "text/plain");

    expect(() => triggerDrop([validFile], null)).not.toThrow();
    expect(mockEnqueue).not.toHaveBeenCalled();
  });

  it("does not crash when acceptedFiles is null", () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );

    expect(() => triggerDrop(null, [])).not.toThrow();
  });

  it("does not crash on rejection item with no errors array", () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );

    const rejection = {
      file: createFile("unknown.pdf", "", "application/pdf"),
    };

    expect(() => triggerDrop([], [rejection])).not.toThrow();
    expect(mockEnqueue).toHaveBeenCalledTimes(1);
  });

  it("does not crash on rejection item with null file", () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );

    const rejection = {
      errors: [
        { code: "file-too-small", message: "File is smaller than 1 bytes" },
      ],
    };

    expect(() => triggerDrop([], [rejection])).not.toThrow();
    // Shows a generic message instead of being silent
    expect(mockEnqueue).toHaveBeenCalledWith("File could not be uploaded", {
      variant: "error",
    });
  });

  it("passes minSize and maxSize to useDropzone", async () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );

    const { useDropzone } = await import("react-dropzone");
    expect(useDropzone).toHaveBeenCalledWith(
      expect.objectContaining({
        minSize: 1,
        maxSize: 5 * 1024 * 1024,
      }),
    );
  });

  it("rejects a too-many-files and aggregates into one toast", () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );

    const file1 = createFile("a.txt", "content", "text/plain");
    const file2 = createFile("b.txt", "content", "text/plain");

    const rejection1 = {
      file: file1,
      errors: [{ code: "too-many-files", message: "Too many files" }],
    };
    const rejection2 = {
      file: file2,
      errors: [{ code: "too-many-files", message: "Too many files" }],
    };

    triggerDrop([], [rejection1, rejection2]);

    // Aggregated into one toast, not two
    expect(mockEnqueue).toHaveBeenCalledTimes(1);
    expect(mockEnqueue).toHaveBeenCalledWith("Please upload only one file.", {
      variant: "error",
    });
  });

  it("shows friendly message for file-invalid-type", () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );

    const rejection = {
      file: createFile("notes.docx", "", "application/msword"),
      errors: [
        {
          code: "file-invalid-type",
          message: "File type must be text/plain,.txt,application/pdf,.pdf",
        },
      ],
    };

    triggerDrop([], [rejection]);

    expect(mockEnqueue).toHaveBeenCalledWith(
      "Unsupported file type. Please upload a TXT or PDF file.",
      { variant: "error" },
    );
  });

  it("shows friendly message for file-too-large", () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );

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

    expect(mockEnqueue).toHaveBeenCalledWith(
      "File size is too large. Please upload a file under 5 MB.",
      { variant: "error" },
    );
  });

  it("does not crash when errors is null", () => {
    render(
      React.createElement(
        TestWrapper,
        null,
        React.createElement(UploadScriptOption, null),
      ),
    );

    const rejection = {
      file: createFile("doc.txt", "", "text/plain"),
      errors: null,
    };

    expect(() => triggerDrop([], [rejection])).not.toThrow();
    expect(mockEnqueue).toHaveBeenCalledTimes(1);
  });
});
