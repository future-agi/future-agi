import React from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderMcp } from "../testing/renderMcp";
import AddMCPServerDialog from "./AddMCPServerDialog";
import { SERVER_ID_PATTERN, SERVER_ID_RULE } from "./serverIdRule";

const mockMutate = vi.fn();
let mockMutationState = {};
const { snackbars } = vi.hoisted(() => ({ snackbars: [] }));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));
vi.mock("../hooks/useMCPConfig", () => ({
  useUpdateMCPServer: () => ({
    mutate: mockMutate,
    isPending: false,
    ...mockMutationState,
  }),
}));
vi.mock("../hooks/useMCPProbe", () => ({
  useMCPProbe: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
  }),
}));

// What a Server ID may be, in the words the dialog uses.
const RULE =
  "Lowercase letters, digits, - and _; up to 48 characters; starts with a letter or digit.";
const MCP_URL = "https://mcp.example.test/mcp";

// The server's own copies of the rule for a new server's id, read from their
// source: the pattern on the line that defines `name` in `file`, quoted as
// r"..." (Python) or `...` (Go).
const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "../../../../../..");
function serverPattern(file, name) {
  const source = readFileSync(join(REPO, file), "utf8");
  const line = source.split("\n").find((l) => l.includes(`${name} = `));
  const match = line?.match(/\(r"([^"]+)"\)|\(`([^`]+)`\)/);
  return match?.[1] ?? match?.[2];
}
const SERVER_PATTERN = serverPattern(
  "futureagi/agentcc/serializers/org_config.py",
  "_MCP_SERVER_ID",
);
const GATEWAY_COPIES = [
  ["agentcc-gateway/internal/config/config.go", "mcpServerIDPattern"],
  ["agentcc-gateway/internal/mcp/tenancy/spec.go", "serverIDPattern"],
];

const REFUSED = [
  ["49 characters", "a".repeat(49)],
  ["a capital letter", "My-Server"],
  ["a leading hyphen", "-lead"],
  ["a space", "a b"],
  ["an accented letter", "café"],
];
const ACCEPTED = [
  ["one letter", "a"],
  ["a plain name", "github"],
  ["an underscore, a hyphen and a digit", "acme_docs-2"],
  ["48 characters", "b".repeat(48)],
];

// A save refused by the server's id rule, as the API client rejects it.
function idRuleRefusal(serverId) {
  const path = `mcp.servers.${serverId}`;
  const message = `${path}: a server id must match ${SERVER_PATTERN}`;
  return {
    status: false,
    code: "invalid",
    statusCode: 400,
    detail: message,
    message,
    error: message,
    result: message,
  };
}

function open(props = {}) {
  return renderMcp(
    <AddMCPServerDialog
      open
      onClose={vi.fn()}
      gatewayId="default"
      {...props}
    />,
  );
}

const serverIdField = () => screen.getByLabelText(/Server ID/);

function fill(serverId) {
  fireEvent.change(serverIdField(), { target: { value: serverId } });
  fireEvent.change(screen.getByLabelText(/URL/), {
    target: { value: MCP_URL },
  });
}

beforeEach(() => {
  mockMutate.mockReset();
  mockMutationState = {};
  snackbars.length = 0;
});

describe("the rule the dialog checks is the server's", () => {
  it("reads the server's id rule", () => {
    expect(SERVER_PATTERN).toBe("^[a-z0-9][a-z0-9_-]{0,47}$");
  });

  it.each(GATEWAY_COPIES)("is the same in %s", (file, name) => {
    expect(serverPattern(file, name)).toBe(SERVER_PATTERN);
  });

  it.each(REFUSED)("the server refuses an id with %s", (_, serverId) => {
    expect(new RegExp(SERVER_PATTERN).test(serverId)).toBe(false);
  });

  it.each(ACCEPTED)("the server accepts an id with %s", (_, serverId) => {
    expect(new RegExp(SERVER_PATTERN).test(serverId)).toBe(true);
  });
});

describe("the dialog's copy of the rule", () => {
  it("is the server's pattern", () => {
    expect(SERVER_ID_PATTERN.source).toBe(SERVER_PATTERN);
    expect(SERVER_ID_PATTERN.flags).toBe("");
  });

  it("is said in the words the dialog shows", () => {
    expect(SERVER_ID_RULE).toBe(RULE);
  });
});

describe("the Add server dialog's Server ID", () => {
  it("says what a Server ID may be before anything is typed", () => {
    open();
    expect(serverIdField()).toHaveAccessibleDescription(
      expect.stringContaining(RULE),
    );
    expect(serverIdField()).toHaveAccessibleDescription(
      expect.stringContaining("for example github or crm"),
    );
    expect(serverIdField()).not.toHaveAttribute("aria-invalid", "true");
  });

  it("takes at most 48 characters", async () => {
    const user = userEvent.setup();
    open();
    expect(serverIdField()).toHaveAttribute("maxlength", "48");
    await user.type(serverIdField(), "c".repeat(49));
    expect(serverIdField()).toHaveValue("c".repeat(48));
  });

  it.each(REFUSED)(
    "refuses an id with %s, says the rule and sends nothing",
    (_, serverId) => {
      open();
      fill(serverId);
      expect(serverIdField()).toHaveValue(serverId);
      expect(serverIdField()).toHaveAttribute("aria-invalid", "true");
      expect(serverIdField()).toHaveAccessibleDescription(
        expect.stringContaining(RULE),
      );
      expect(document.body.textContent).not.toContain("[a-z0-9]");
      fireEvent.click(screen.getByRole("button", { name: "Add server" }));
      expect(mockMutate).not.toHaveBeenCalled();
      expect(snackbars).toEqual([
        { message: RULE, options: { variant: "warning" } },
      ]);
    },
  );

  it.each(ACCEPTED)("saves an id with %s as typed", (_, serverId) => {
    open();
    fill(serverId);
    expect(serverIdField()).not.toHaveAttribute("aria-invalid", "true");
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(mockMutate).toHaveBeenCalledOnce();
    expect(mockMutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      serverId,
      config: { transport: "http", url: MCP_URL },
    });
    expect(snackbars).toEqual([]);
  });

  it.each([
    ["empty", ""],
    ["only spaces", "   "],
  ])("still asks for a Server ID when it is %s", (_, serverId) => {
    open();
    fill(serverId);
    expect(serverIdField()).not.toHaveAttribute("aria-invalid", "true");
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(mockMutate).not.toHaveBeenCalled();
    expect(snackbars).toEqual([
      { message: "Server ID is required", options: { variant: "warning" } },
    ]);
  });

  it("keeps a saved server's id read-only and does not check it again", () => {
    open({
      editServer: {
        serverId: "Legacy_Server",
        config: { transport: "http", url: "http://old:1/mcp" },
      },
    });
    expect(serverIdField()).toBeDisabled();
    expect(serverIdField()).toHaveValue("Legacy_Server");
    expect(serverIdField()).not.toHaveAttribute("aria-invalid", "true");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mockMutate).toHaveBeenCalledOnce();
    expect(mockMutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      serverId: "Legacy_Server",
      config: { transport: "http", url: "http://old:1/mcp" },
    });
    expect(snackbars).toEqual([]);
  });
});

describe("a save the server refuses for its id", () => {
  it("shows the rule in the toast, not the server's pattern", () => {
    mockMutate.mockImplementation((_, { onError }) =>
      onError(idRuleRefusal("github")),
    );
    open();
    fill("github");
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(mockMutate).toHaveBeenCalledOnce();
    expect(snackbars).toEqual([
      { message: RULE, options: { variant: "error" } },
    ]);
  });

  it("shows the rule in the dialog's error, not the server's pattern", () => {
    mockMutationState = { isError: true, error: idRuleRefusal("github") };
    open();
    expect(screen.getByRole("alert")).toHaveTextContent(RULE);
    expect(screen.getByRole("alert")).not.toHaveTextContent(
      "a server id must match",
    );
  });

  it("shows any other refusal as before", () => {
    const other = {
      status: false,
      code: "invalid",
      statusCode: 400,
      message:
        "The gateway cannot accept this config. mcp.servers.github.command: Extra inputs are not permitted",
    };
    mockMutationState = { isError: true, error: other };
    mockMutate.mockImplementation((_, { onError }) => onError(other));
    open();
    expect(screen.getByRole("alert")).toHaveTextContent(other.message);
    fill("github");
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(snackbars).toEqual([
      {
        message: "Failed to save server configuration",
        options: { variant: "error" },
      },
    ]);
  });
});
