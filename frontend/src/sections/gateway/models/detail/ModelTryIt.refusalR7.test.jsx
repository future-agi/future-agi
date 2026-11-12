import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import ModelTryIt from "./ModelTryIt";
import { ACCESS } from "../catalogRows";
import {
  REMEDIES,
  blockedWire,
  directFailure,
  leakedCopy,
  refusalWire,
} from "../../utils/__tests__/guardrailRefusalR7Fixtures";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post },
}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQuery: () => ({ data: false }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));

const row = {
  id: "openai/model-a",
  type: "chat",
  access: ACCESS.MANAGED,
  managedOn: true,
  ownRoutes: [],
  model: {},
};
beforeEach(() => post.mockReset());
afterEach(cleanup);

async function run(result, transport) {
  if (transport === "raw" || transport === "normalized") {
    post.mockRejectedValue(directFailure(result, transport === "normalized"));
  } else if (transport === "sse") {
    const data = `event: result\ndata: ${JSON.stringify(result)}\n\n`;
    post.mockImplementation(async (_url, _body, options) => {
      options.onDownloadProgress?.({
        event: { target: { responseText: data } },
      });
      return { data };
    });
  } else post.mockResolvedValue({ data: { result } });
  const view = render(
    <MemoryRouter>
      <ModelTryIt row={row} gatewayId="gw-1" />
    </MemoryRouter>,
  );
  if (transport === "json") {
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Stream the answer" }),
    );
  }
  await userEvent.click(screen.getByRole("button", { name: "Run" }));
  await screen.findByRole("alert");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Run" })).toBeEnabled(),
  );
  expect(post).toHaveBeenCalledTimes(1);
  expect(post.mock.calls[0][1].model).toBe(row.id);
  return view;
}

function expectControlled(view, guidance) {
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(guidance);
  expect(alert).toHaveTextContent("not retry automatically");
  expect(view.container).not.toHaveTextContent(leakedCopy);
  expect(view.container).not.toHaveTextContent(/Blocked by a guardrail/);
  expect(within(alert).queryByRole("button")).toBeNull();
  expect(within(alert).queryByRole("link")).toBeNull();
}

describe.each(["json", "sse", "raw", "normalized"])(
  "r7 ModelTryIt body-type conflict via %s",
  (transport) => {
    it.each(REMEDIES)(
      "maps %s without rendering upstream values",
      async (code, guidance) => {
        const view = await run(refusalWire(code), transport);
        expectControlled(view, guidance);
      },
    );

    it("does not infer a block from 403 and body type alone", async () => {
      const view = await run(
        refusalWire("unknown_future_code", { status_code: 403 }),
        transport,
      );
      expectControlled(view, "The gateway refused the request.");
    });
  },
);

describe.each(["json", "sse"])(
  "r7 ModelTryIt relay blocks via %s",
  (transport) => {
    it("preserves genuine block copy even if the body type disagrees", async () => {
      const result = blockedWire();
      result.body.error.type = "permission_error";
      const view = await run(result, transport);
      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent("Blocked by a guardrail. HTTP 403:");
      expect(alert).toHaveTextContent(result.body.error.message);
      expect(view.container).not.toHaveTextContent(
        "The gateway refused the request.",
      );
    });

    it("rejects a block flag conflicting with the refusal status", async () => {
      const view = await run(
        refusalWire("managed_output_ceiling_required", { blocked: true }),
        transport,
      );
      expectControlled(view, "Set a positive max_tokens value");
    });
  },
);
