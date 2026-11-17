import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { copyToClipboard } from "../utils";
import { logger } from "../logger";

describe("copyToClipboard", () => {
  let writeTextMock;
  let loggerWarnSpy;
  let originalClipboard;

  beforeEach(() => {
    writeTextMock = vi.fn().mockResolvedValue(undefined);
    originalClipboard = navigator.clipboard;
    Object.assign(navigator, {
      clipboard: { writeText: writeTextMock },
    });
    // Copy failures are now logged at WARNING (breadcrumb), not ERROR; silence
    // it so the "does not throw" path doesn't pollute test output.
    loggerWarnSpy = vi.spyOn(logger, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    loggerWarnSpy.mockRestore();
    Object.assign(navigator, { clipboard: originalClipboard });
  });

  it("copies a string as-is", async () => {
    await copyToClipboard("hello");
    expect(writeTextMock).toHaveBeenCalledWith("hello");
  });

  it("serializes an object to pretty-printed JSON", async () => {
    const obj = { model: "gpt-4", temp: 0.7 };
    await copyToClipboard(obj);
    expect(writeTextMock).toHaveBeenCalledWith(JSON.stringify(obj, null, 2));
  });

  it("serializes an array to pretty-printed JSON", async () => {
    const arr = [1, 2, 3];
    await copyToClipboard(arr);
    expect(writeTextMock).toHaveBeenCalledWith(JSON.stringify(arr, null, 2));
  });

  it("passes null through without serialization", async () => {
    await copyToClipboard(null);
    expect(writeTextMock).toHaveBeenCalledWith(null);
  });

  it("passes undefined through without serialization", async () => {
    await copyToClipboard(undefined);
    expect(writeTextMock).toHaveBeenCalledWith(undefined);
  });

  it("passes a number through without serialization", async () => {
    await copyToClipboard(42);
    expect(writeTextMock).toHaveBeenCalledWith(42);
  });

  it("returns true on a successful copy", async () => {
    await expect(copyToClipboard("text")).resolves.toBe(true);
  });

  it("does not throw and returns false on clipboard error", async () => {
    writeTextMock.mockRejectedValue(new Error("denied"));
    await expect(copyToClipboard("text")).resolves.toBe(false);
  });

  // Regression for the FRONTEND null-guard fix: navigator.clipboard is
  // undefined on insecure (http) origins — must NOT throw
  // "Cannot read properties of undefined (reading 'writeText')".
  it("falls back to execCommand when navigator.clipboard is undefined", async () => {
    Object.assign(navigator, { clipboard: undefined });
    // jsdom doesn't implement execCommand — define it so we can assert the fallback.
    const execMock = vi.fn(() => true);
    const hadExec = "execCommand" in document;
    document.execCommand = execMock;
    const result = await copyToClipboard("text"); // must not throw
    expect(writeTextMock).not.toHaveBeenCalled();
    expect(execMock).toHaveBeenCalledWith("copy");
    expect(result).toBe(true);
    if (!hadExec) delete document.execCommand;
  });

  // W10-DESIGN-SYSTEM-U06 (W11-QUICKSTART2-R): the fallback copies from a
  // hidden textarea, which takes focus from the control that asked for the
  // copy. Focus goes back to that control, so a keyboard user keeps their place
  // and the control's tooltip stays open.
  describe("gives focus back after the execCommand fallback", () => {
    let execDescriptor;
    let button;

    beforeEach(() => {
      Object.assign(navigator, { clipboard: undefined });
      execDescriptor = Object.getOwnPropertyDescriptor(document, "execCommand");
      button = document.createElement("button");
      document.body.appendChild(button);
      button.focus();
    });

    afterEach(() => {
      button.remove();
      if (execDescriptor)
        Object.defineProperty(document, "execCommand", execDescriptor);
      else delete document.execCommand;
    });

    it.each([
      ["copies", () => true, true],
      ["does not copy", () => false, false],
      [
        "throws",
        () => {
          throw new Error("copy is not supported");
        },
        false,
      ],
    ])("when the copy command %s", async (_, exec, expected) => {
      Object.defineProperty(document, "execCommand", {
        configurable: true,
        value: vi.fn(exec),
      });
      expect(document.activeElement).toBe(button);
      const refocus = vi.spyOn(button, "focus");

      await expect(copyToClipboard("text")).resolves.toBe(expected);

      expect(refocus).toHaveBeenCalledTimes(1);
      expect(document.activeElement).toBe(button);
      expect(document.querySelector("textarea")).toBeNull();
    });
  });
});
