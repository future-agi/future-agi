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
import {
  render,
  screen,
  act,
  waitFor,
  fireEvent,
} from "@testing-library/react";
import { useForm, FormProvider } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import UploadScriptOption from "../UploadScriptOption";
import {
  CreateScenarioDefaultValues,
  CreateScenarioType,
  CreateScenarioValidationSchema,
  ScenarioTypeWiseDefaultValues,
} from "../common";

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

// The Create form the way CreateScenarioView builds it: the production schema
// and resolver, `control` passed as a prop (no FormProvider), and a Create
// button that calls handleSubmit with no invalid handler.
function CreateScenarioForm({ onCreate, values }) {
  const { control, handleSubmit } = useForm({
    defaultValues: {
      ...CreateScenarioDefaultValues,
      ...values,
      kind: CreateScenarioType.SCRIPT,
      config: { ...ScenarioTypeWiseDefaultValues[CreateScenarioType.SCRIPT] },
    },
    resolver: zodResolver(CreateScenarioValidationSchema),
  });
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(UploadScriptOption, { control }),
    React.createElement(
      "button",
      { type: "button", onClick: handleSubmit(onCreate) },
      "Create",
    ),
  );
}

CreateScenarioForm.propTypes = {
  onCreate: PropTypes.func,
  values: PropTypes.object,
};

// The rest of the form filled in, so only the file decides whether Create
// goes through.
const READY_TO_CREATE = {
  agentType: "voice",
  sourceId: "agent-1",
  name: "Support script",
  agentDefinitionId: "agent-1",
  agentDefinitionVersionId: "version-1",
};

function clickCreate() {
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
}

describe("UploadScriptOption: Create with no script file", () => {
  beforeEach(() => {
    capturedOnDrop = null;
    mockEnqueue.mockClear();
  });

  it("says a script file is needed when Create is clicked before any upload", async () => {
    const onCreate = vi.fn();
    render(React.createElement(CreateScenarioForm, { onCreate }));

    clickCreate();

    const message = await screen.findByText("Upload a script file");
    expect(message.className).toContain("Mui-error");
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("says a script file is needed when Create is clicked after the file is removed", async () => {
    const onCreate = vi.fn();
    render(React.createElement(CreateScenarioForm, { onCreate }));

    triggerDrop([createFile("script.txt", "print('hi')", "text/plain")], []);
    await screen.findByText("script.txt");

    // While a file is shown, the trash icon is the only button besides Create.
    const removeButtons = screen
      .getAllByRole("button")
      .filter((button) => button.textContent !== "Create");
    expect(removeButtons).toHaveLength(1);
    fireEvent.click(removeButtons[0]);
    await screen.findByText("Upload Script");

    clickCreate();

    expect(await screen.findByText("Upload a script file")).toBeTruthy();
    // The Browse Files button is a new one after the file is removed, and it
    // still takes focus.
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "Browse Files" }),
      ),
    );
    expect(onCreate).not.toHaveBeenCalled();
  });

  // When the file is the only thing missing, a failed Create must land on the
  // upload box, so it is not left out of view with nothing happening. Focusing
  // the button also scrolls it into view in a browser.
  it("moves focus to Browse Files when only the script file is missing", async () => {
    const onCreate = vi.fn();
    render(
      React.createElement(CreateScenarioForm, {
        onCreate,
        values: READY_TO_CREATE,
      }),
    );

    const browseBefore = screen.getByRole("button", { name: "Browse Files" });
    expect(document.activeElement).not.toBe(browseBefore);
    expect(browseBefore.hasAttribute("aria-invalid")).toBe(false);

    clickCreate();

    await waitFor(() => {
      const browse = screen.getByRole("button", { name: "Browse Files" });
      expect(document.activeElement).toBe(browse);
      expect(browse.getAttribute("aria-invalid")).toBe("true");
    });
    expect(onCreate).not.toHaveBeenCalled();
  });

  // Guard (holds at the base too): with a file attached, Create goes through
  // and carries the file.
  it("still creates the scenario with the uploaded file", async () => {
    const onCreate = vi.fn();
    render(
      React.createElement(CreateScenarioForm, {
        onCreate,
        values: READY_TO_CREATE,
      }),
    );

    triggerDrop([createFile("script.txt", "print('hi')", "text/plain")], []);
    await screen.findByText("script.txt");

    clickCreate();

    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1));
    expect(onCreate.mock.calls[0][0].config.scriptUrl.name).toBe("script.txt");
    expect(screen.queryByText("Upload a script file")).toBeNull();
  });
});
