import React from "react";
import { render, screen } from "src/utils/test-utils";
import { markGeneratedCamelAlias } from "src/utils/responseAliasMetadata";
import { describe, expect, it } from "vitest";
import { GatewayMetadataJson, GatewayMetadataTable } from "./GatewayMetadata";
import {
  hasGatewayMetadata,
  normalizeGatewayMetadata,
  normalizeGatewayMetadataField,
  normalizeGatewayMetadataResponse,
  stringifyGatewayMetadata,
} from "../utils/metadataDisplay";

const withGeneratedAliases = (obj) => {
  const out = { ...obj };
  Object.keys(obj).forEach((key) => {
    if (key.includes("_")) {
      const camel = key.replace(/_([a-z0-9])/g, (_, c) => c.toUpperCase());
      if (camel !== key && !(camel in out)) {
        out[camel] = obj[key];
        markGeneratedCamelAlias(out, camel);
      }
    }
  });
  return out;
};

describe("GatewayMetadata Unit", () => {
  it("renders metadata tables without generated alias rows", () => {
    const metadata = withGeneratedAliases({ user_id: "abc" });
    metadata.nested = withGeneratedAliases({ inner_key: 1 });

    render(<GatewayMetadataTable metadata={metadata} />);

    expect(metadata.userId).toBe("abc");
    expect(metadata.nested.innerKey).toBe(1);
    expect(Object.keys(metadata)).toContain("userId");
    expect(Object.keys(metadata.nested)).toContain("innerKey");
    expect(screen.getByText("user_id")).toBeInTheDocument();
    expect(screen.getByText("abc")).toBeInTheDocument();
    expect(screen.queryByText("userId")).not.toBeInTheDocument();
    expect(screen.getByText("nested")).toBeInTheDocument();
    expect(screen.getByText('{"inner_key":1}')).toBeInTheDocument();
    expect(screen.queryByText(/innerKey/)).not.toBeInTheDocument();
  });

  it("keeps real camelCase metadata when the snake_case sibling differs", () => {
    render(
      <GatewayMetadataTable
        metadata={{
          user_id: "snake-value",
          userId: "camel-value",
          nested: {
            inner_key: 1,
            innerKey: 2,
          },
        }}
      />,
    );

    expect(screen.getByText("user_id")).toBeInTheDocument();
    expect(screen.getByText("snake-value")).toBeInTheDocument();
    expect(screen.getByText("userId")).toBeInTheDocument();
    expect(screen.getByText("camel-value")).toBeInTheDocument();
    expect(
      screen.getByText('{"inner_key":1,"innerKey":2}'),
    ).toBeInTheDocument();
  });

  it("keeps real camelCase metadata when same-valued siblings are user-defined", () => {
    render(
      <GatewayMetadataTable
        metadata={{
          user_id: "same-value",
          userId: "same-value",
        }}
      />,
    );

    expect(screen.getByText("user_id")).toBeInTheDocument();
    expect(screen.getByText("userId")).toBeInTheDocument();
    expect(screen.getAllByText("same-value")).toHaveLength(2);
  });

  it("keeps real object-valued camelCase metadata without generated provenance", () => {
    render(
      <GatewayMetadataTable
        metadata={{
          object_key: { a: 1 },
          objectKey: { a: 1 },
        }}
      />,
    );

    expect(screen.getByText("object_key")).toBeInTheDocument();
    expect(screen.getByText("objectKey")).toBeInTheDocument();
    expect(screen.getAllByText('{"a":1}')).toHaveLength(2);
  });

  it("renders JSON metadata with generated aliases hidden and __proto__ preserved as data", () => {
    const metadata = JSON.parse(
      '{"__proto__":{"polluted":true},"safe_key":1,"nested":{"inner_key":2}}',
    );
    metadata.safeKey = metadata.safe_key;
    metadata.nested.innerKey = metadata.nested.inner_key;
    markGeneratedCamelAlias(metadata, "safeKey");
    markGeneratedCamelAlias(metadata.nested, "innerKey");

    render(<GatewayMetadataJson metadata={metadata} />);

    const json = stringifyGatewayMetadata(metadata);
    expect(
      Object.prototype.hasOwnProperty.call(Object.prototype, "polluted"),
    ).toBe(false);
    expect(json).toContain('"__proto__"');
    expect(json).toContain('"safe_key"');
    expect(json).not.toContain('"safeKey"');
    expect(json).not.toContain('"innerKey"');
    expect(screen.getByText(/"safe_key": 1/)).toBeInTheDocument();
  });

  it("treats canonical metadata and non-null scalars as present", () => {
    expect(hasGatewayMetadata({ user_id: 1, userId: 1 })).toBe(true);
    expect(hasGatewayMetadata("plain-text")).toBe(true);
    expect(stringifyGatewayMetadata("plain-text")).toBe('"plain-text"');
    expect(hasGatewayMetadata(undefined)).toBe(false);
    expect(hasGatewayMetadata(null)).toBe(false);
    expect(hasGatewayMetadata({})).toBe(false);
    render(<GatewayMetadataTable metadata={undefined} />);
    expect(screen.getByText("No metadata")).toBeInTheDocument();
  });

  it("normalizes generated aliases recursively without dropping real camelCase keys", () => {
    const metadata = withGeneratedAliases({
      user_id: "abc",
      userId: "real-user-field",
      items: [withGeneratedAliases({ request_id: "req-1" })],
    });

    const normalized = normalizeGatewayMetadata(metadata);

    expect(Object.keys(normalized)).toEqual(["user_id", "userId", "items"]);
    expect(Object.keys(normalized.items[0])).toEqual(["request_id"]);
    expect(normalized.userId).toBe("real-user-field");
    expect(normalized.items[0].requestId).toBeUndefined();
  });

  it("normalizes gateway detail records before they are cached", () => {
    const record = {
      id: "log-1",
      metadata: withGeneratedAliases({ user_id: "abc" }),
    };

    const normalized = normalizeGatewayMetadataField(record);

    expect(record.metadata.userId).toBe("abc");
    expect(normalized).not.toBe(record);
    expect(Object.keys(normalized.metadata)).toEqual(["user_id"]);
    expect(normalized.metadata.userId).toBeUndefined();
    expect({ ...normalized.metadata }.userId).toBeUndefined();
  });

  it("normalizes enveloped gateway detail responses before they are cached", () => {
    const response = {
      status: "ok",
      result: {
        id: "log-1",
        metadata: withGeneratedAliases({ request_id: "req-1" }),
      },
    };

    const normalized = normalizeGatewayMetadataResponse(response);

    expect(response.result.metadata.requestId).toBe("req-1");
    expect(normalized).not.toBe(response);
    expect(normalized.result).not.toBe(response.result);
    expect(Object.keys(normalized.result.metadata)).toEqual(["request_id"]);
    expect(normalized.result.metadata.requestId).toBeUndefined();
  });
});
