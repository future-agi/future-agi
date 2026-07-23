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
import { render, screen, act } from "@testing-library/react";
import { useForm, FormProvider } from "react-hook-form";
import CallChatSOPOption from "../CallChatSOPOption";

function TestWrapper({ children }) {
  const methods = useForm({
    defaultValues: { "config.sopUrl": null },
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

describe("CallChatSOPOption", () => {
  beforeEach(() => {
    capturedOnDrop = null;
    mockEnqueue.mockClear();
  });

  it("renders the upload dropzone", () => {
    render(React.createElement(TestWrapper, null,
      React.createElement(CallChatSOPOption, null),
    ));
    expect(screen.getByText("Call/Chat SOP")).toBeTruthy();
  });

  it("rejects a 0-byte file and shows an error snackbar", () => {
    render(React.createElement(TestWrapper, null,
      React.createElement(CallChatSOPOption, null),
    ));

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
    render(React.createElement(TestWrapper, null,
      React.createElement(CallChatSOPOption, null),
    ));

    const validFile = createFile("script.txt", "print('hello')", "text/plain");
    triggerDrop([validFile], []);

    expect(mockEnqueue).not.toHaveBeenCalled();
  });

  it("accepts a valid PDF file", () => {
    render(React.createElement(TestWrapper, null,
      React.createElement(CallChatSOPOption, null),
    ));

    const validPdf = createFile("doc.pdf", "%PDF-1.4", "application/pdf");
    triggerDrop([validPdf], []);

    expect(mockEnqueue).not.toHaveBeenCalled();
  });

  it("shows snackbar for each rejected file", () => {
    render(React.createElement(TestWrapper, null,
      React.createElement(CallChatSOPOption, null),
    ));

    const rejection1 = {
      file: createFile("empty1.pdf", "", "application/pdf"),
      errors: [
        { code: "file-too-small", message: "File is smaller than 1 bytes" },
      ],
    };
    const rejection2 = {
      file: createFile("empty2.pdf", "", "application/pdf"),
      errors: [
        { code: "file-too-small", message: "File is smaller than 1 bytes" },
      ],
    };

    triggerDrop([], [rejection1, rejection2]);

    expect(mockEnqueue).toHaveBeenCalledTimes(2);
  });

  it("processes an accepted file alongside a rejected one", () => {
    render(React.createElement(TestWrapper, null,
      React.createElement(CallChatSOPOption, null),
    ));

    const validFile = createFile("good.txt", "content", "text/plain");
    const rejection = {
      file: createFile("bad.pdf", "", "application/pdf"),
      errors: [
        { code: "file-too-small", message: "File is smaller than 1 bytes" },
      ],
    };

    triggerDrop([validFile], [rejection]);

    expect(mockEnqueue).toHaveBeenCalledTimes(1);
  });

  it("does not crash when fileRejections is null", () => {
    render(React.createElement(TestWrapper, null,
      React.createElement(CallChatSOPOption, null),
    ));

    const validFile = createFile("doc.txt", "text", "text/plain");

    expect(() => triggerDrop([validFile], null)).not.toThrow();
    expect(mockEnqueue).not.toHaveBeenCalled();
  });

  it("does not crash when acceptedFiles is null", () => {
    render(React.createElement(TestWrapper, null,
      React.createElement(CallChatSOPOption, null),
    ));

    expect(() => triggerDrop(null, [])).not.toThrow();
  });

  it("does not crash on rejection item with no errors array", () => {
    render(React.createElement(TestWrapper, null,
      React.createElement(CallChatSOPOption, null),
    ));

    const rejection = {
      file: createFile("unknown.pdf", "", "application/pdf"),
    };

    expect(() => triggerDrop([], [rejection])).not.toThrow();
    expect(mockEnqueue).toHaveBeenCalledTimes(1);
  });

  it("does not crash on rejection item with null file", () => {
    render(React.createElement(TestWrapper, null,
      React.createElement(CallChatSOPOption, null),
    ));

    const rejection = {
      errors: [
        { code: "file-too-small", message: "File is smaller than 1 bytes" },
      ],
    };

    expect(() => triggerDrop([], [rejection])).not.toThrow();
  });
});
