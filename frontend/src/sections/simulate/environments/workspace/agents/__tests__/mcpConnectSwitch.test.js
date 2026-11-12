import { describe, it, expect, afterEach } from "vitest";
import {
  MCP_CONNECT_SWITCH,
  isMcpConnectEnabled,
  readMcpConnectSwitch,
} from "../connect/mcpConnectSwitch";

// The MCP connect reach stays hidden until a release turns its
// switch on, because no endpoint answers its address yet.
describe("readMcpConnectSwitch", () => {
  it("is off when neither runtime nor build config names it", () => {
    expect(readMcpConnectSwitch({}, {})).toBe(false);
    expect(readMcpConnectSwitch(undefined, undefined)).toBe(false);
    expect(readMcpConnectSwitch({ [MCP_CONNECT_SWITCH]: "" }, {})).toBe(false);
  });

  it("stays off for any value other than true", () => {
    for (const value of ["false", "0", "1", "yes", "on", "enabled", " "]) {
      expect(readMcpConnectSwitch({ [MCP_CONNECT_SWITCH]: value }, {})).toBe(
        false,
      );
    }
    expect(readMcpConnectSwitch({ [MCP_CONNECT_SWITCH]: true }, {})).toBe(
      false,
    );
  });

  it("turns on for true in runtime or build config", () => {
    expect(readMcpConnectSwitch({ [MCP_CONNECT_SWITCH]: "true" }, {})).toBe(
      true,
    );
    expect(readMcpConnectSwitch({}, { [MCP_CONNECT_SWITCH]: " TRUE " })).toBe(
      true,
    );
  });

  it("lets runtime config win over the build-time value", () => {
    expect(
      readMcpConnectSwitch(
        { [MCP_CONNECT_SWITCH]: "false" },
        { [MCP_CONNECT_SWITCH]: "true" },
      ),
    ).toBe(false);
    expect(
      readMcpConnectSwitch(
        { [MCP_CONNECT_SWITCH]: "" },
        { [MCP_CONNECT_SWITCH]: "true" },
      ),
    ).toBe(true);
  });
});

describe("isMcpConnectEnabled", () => {
  afterEach(() => {
    delete window.__FUTURE_AGI_CONFIG__;
  });

  it("is off by default in this build", () => {
    delete window.__FUTURE_AGI_CONFIG__;
    expect(isMcpConnectEnabled()).toBe(false);
  });

  it("reads window.__FUTURE_AGI_CONFIG__", () => {
    window.__FUTURE_AGI_CONFIG__ = { [MCP_CONNECT_SWITCH]: "true" };
    expect(isMcpConnectEnabled()).toBe(true);
  });
});
