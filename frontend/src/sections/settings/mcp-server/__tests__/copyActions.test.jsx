import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { enqueueSnackbar } from "notistack";
import { render } from "src/utils/test-utils";
import { logger } from "src/utils/logger";
import ClientSetupGuide from "../ClientSetupGuide";
import ConnectionInfo from "../ConnectionInfo";
import { buildMcpClientConfigs } from "../clientSetupSnippets";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

// A self-hosted install opened over plain http on a non-localhost address
// (TEST-NET-1). Browsers expose no navigator.clipboard on such an origin.
const MCP_URL = "http://192.0.2.10:8000/mcp";
const configs = buildMcpClientConfigs(MCP_URL);
const COPY_FAILED =
  "Could not copy. Press Ctrl+C (Cmd+C on Mac) to copy the selected text.";

let clipboardDescriptor;
let execDescriptor;
let warnSpy;
let writeText;
let execCommand;
let execCopied;

// Secure origin: the async Clipboard API exists.
function withClipboardApi(impl = () => Promise.resolve()) {
  writeText = vi.fn(impl);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
}

// Insecure origin: no navigator.clipboard at all.
function withoutClipboardApi() {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: undefined,
  });
}

// The legacy copy command the helper falls back to. It records what it would
// have copied (the focused helper textarea's value) and returns `result`.
function withExecCommand(result) {
  execCopied = null;
  execCommand = vi.fn(() => {
    execCopied = document.activeElement?.value ?? null;
    return result;
  });
  Object.defineProperty(document, "execCommand", {
    configurable: true,
    value: execCommand,
  });
}

beforeEach(() => {
  clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  execDescriptor = Object.getOwnPropertyDescriptor(document, "execCommand");
  warnSpy = vi.spyOn(logger, "warn").mockImplementation(() => {});
  window.getSelection().removeAllRanges();
  enqueueSnackbar.mockClear();
});

afterEach(() => {
  warnSpy.mockRestore();
  if (clipboardDescriptor)
    Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  else delete navigator.clipboard;
  if (execDescriptor)
    Object.defineProperty(document, "execCommand", execDescriptor);
  else delete document.execCommand;
});

const urlInput = () => screen.getByRole("textbox", { name: "MCP server URL" });

// Text the user would copy with Ctrl+C / Cmd+C right now.
function inputSelection(input) {
  expect(document.activeElement).toBe(input);
  return input.value.slice(input.selectionStart, input.selectionEnd);
}

const SITES = [
  {
    name: "the MCP URL copy icon",
    open: () => {},
    button: () => screen.getByRole("button", { name: "Copy URL" }),
    text: MCP_URL,
    success: "MCP URL copied to clipboard",
    selected: () => inputSelection(urlInput()),
  },
  {
    name: "a config snippet's copy icon",
    open: () => {},
    button: () => screen.getByRole("button", { name: "Copy" }),
    text: configs.cursor,
    success: "Copied to clipboard",
    selected: () => window.getSelection().toString(),
  },
  {
    name: "Claude Code 'Copy command'",
    open: () =>
      fireEvent.click(screen.getByRole("tab", { name: "Claude Code" })),
    button: () => screen.getByRole("button", { name: "Copy command" }),
    text: configs.claudeCode,
    success: "Claude Code command copied — paste it in your terminal",
    selected: () => window.getSelection().toString(),
  },
];

function renderGuide() {
  render(<ClientSetupGuide config={{ mcp_url: MCP_URL }} />);
}

describe("Settings > MCP Server copy actions", () => {
  describe.each(SITES)("$name", (site) => {
    it("copies with the Clipboard API and shows the success toast", async () => {
      withClipboardApi();
      withExecCommand(false);
      renderGuide();
      site.open();

      fireEvent.click(site.button());

      await waitFor(() =>
        expect(enqueueSnackbar).toHaveBeenCalledWith(site.success, {
          variant: "success",
        }),
      );
      expect(writeText).toHaveBeenCalledWith(site.text);
      expect(execCommand).not.toHaveBeenCalled();
      expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    });

    it("falls back on an insecure http origin and still shows the success toast", async () => {
      withoutClipboardApi();
      withExecCommand(true);
      renderGuide();
      site.open();

      fireEvent.click(site.button());

      await waitFor(() =>
        expect(enqueueSnackbar).toHaveBeenCalledWith(site.success, {
          variant: "success",
        }),
      );
      expect(execCommand).toHaveBeenCalledWith("copy");
      expect(execCopied).toBe(site.text);
      expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    });

    it("says how to copy by hand and selects the text when every copy path fails", async () => {
      withoutClipboardApi();
      withExecCommand(false);
      renderGuide();
      site.open();

      expect(() => fireEvent.click(site.button())).not.toThrow();

      await waitFor(() =>
        expect(enqueueSnackbar).toHaveBeenCalledWith(COPY_FAILED, {
          variant: "error",
        }),
      );
      expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
      expect(site.selected()).toBe(site.text);
    });
  });

  it("shows the error toast when the browser blocks the Clipboard API", async () => {
    withClipboardApi(() => Promise.reject(new Error("blocked")));
    withExecCommand(false);
    renderGuide();

    fireEvent.click(screen.getByRole("button", { name: "Copy URL" }));

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(COPY_FAILED, {
        variant: "error",
      }),
    );
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(inputSelection(urlInput())).toBe(MCP_URL);
  });
});

describe("ConnectionInfo copy action", () => {
  const renderInfo = () =>
    render(<ConnectionInfo config={{ mcp_url: MCP_URL }} />);
  const infoInput = () =>
    screen.getByRole("textbox", { name: "MCP Endpoint URL" });

  it("falls back on an insecure http origin and keeps its success toast", async () => {
    withoutClipboardApi();
    withExecCommand(true);
    renderInfo();

    fireEvent.click(screen.getByRole("button", { name: "Copy URL" }));

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "MCP URL copied to clipboard",
        { variant: "success" },
      ),
    );
    expect(execCopied).toBe(MCP_URL);
  });

  it("shows the error toast and selects the URL when copying fails", async () => {
    withoutClipboardApi();
    withExecCommand(false);
    renderInfo();

    expect(() =>
      fireEvent.click(screen.getByRole("button", { name: "Copy URL" })),
    ).not.toThrow();

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(COPY_FAILED, {
        variant: "error",
      }),
    );
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(inputSelection(infoInput())).toBe(MCP_URL);
  });
});
