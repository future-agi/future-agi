import { describe, expect, it, vi } from "vitest";

import { createScenarioFileDropHandler } from "./common";

describe("createScenarioFileDropHandler", () => {
  const createHandler = () => {
    const enqueueSnackbar = vi.fn();
    const onChange = vi.fn();

    const handleFileChange = createScenarioFileDropHandler({
      enqueueSnackbar,
      onChange,
    });

    return { handleFileChange, enqueueSnackbar, onChange };
  };

  it("rejects an empty file", () => {
    const { handleFileChange, enqueueSnackbar, onChange } = createHandler();

    const emptyFile = new File([""], "empty.pdf", {
      type: "application/pdf",
    });

    handleFileChange([emptyFile]);

    expect(enqueueSnackbar).toHaveBeenCalledWith("File is empty", {
      variant: "error",
    });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("accepts a file with 1 byte", () => {
    const { handleFileChange, enqueueSnackbar, onChange } = createHandler();

    const file = new File(["a"], "small.txt", {
      type: "text/plain",
    });

    handleFileChange([file]);

    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(onChange).toHaveBeenCalledWith({
      file,
      name: "small.txt",
      size: 1,
    });
  });

  it("accepts a file exactly 5 MB", () => {
    const { handleFileChange, enqueueSnackbar, onChange } = createHandler();

    const fiveMB = new File([new Uint8Array(5 * 1024 * 1024)], "large.pdf", {
      type: "application/pdf",
    });

    handleFileChange([fiveMB]);

    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(onChange).toHaveBeenCalledWith({
      file: fiveMB,
      name: "large.pdf",
      size: 5 * 1024 * 1024,
    });
  });

  it("rejects a file larger than 5 MB", () => {
    const { handleFileChange, enqueueSnackbar, onChange } = createHandler();

    const overLimitFile = new File(
      [new Uint8Array(5 * 1024 * 1024 + 1)],
      "too-large.pdf",
      {
        type: "application/pdf",
      },
    );

    handleFileChange([overLimitFile]);

    expect(enqueueSnackbar).toHaveBeenCalledWith("File size is too large", {
      variant: "error",
    });
    expect(onChange).not.toHaveBeenCalled();
  });
});