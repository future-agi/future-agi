import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { endpoints } from "src/utils/axios";
import { renderPage } from "../../guardrails/__tests__/helpers";
import CreateKeyDialog from "../CreateKeyDialog";

const state = vi.hoisted(() => ({ post: vi.fn() }));

// Keep the real create mutation and endpoint; only the transport and unrelated
// access-option fields are fake. A refusal must not invoke the success callback.
vi.mock("src/utils/axios", async (importOriginal) => {
  const original = await importOriginal();
  return { ...original, default: { post: state.post } };
});
vi.mock("../AccessListField", () => ({ default: () => null }));
vi.mock("../AllowedProvidersField", () => ({ default: () => null }));
vi.mock("../useKeyAccessOptions", () => ({
  useKeyAccessOptions: () => ({ modelOptions: [] }),
}));

const REMEDY = "Choose or join a workspace to create API keys.";
const UNTRUSTED_MESSAGE = "private upstream context must not be displayed";

beforeEach(() => {
  state.post.mockReset();
});
afterEach(cleanup);

function renderDialog() {
  const onCreated = vi.fn();
  const onClose = vi.fn();
  renderPage(
    <CreateKeyDialog
      open
      onClose={onClose}
      onCreated={onCreated}
      gatewayId="fixture-gateway"
    />,
  );
  return { user: userEvent.setup(), onCreated, onClose };
}

async function submit(user) {
  await user.type(screen.getByRole("textbox", { name: /Name/ }), "Personal key");
  await user.click(screen.getByRole("button", { name: "Create", exact: true }));
}

describe("personal-key workspace refusal", () => {
  it.each(["normalized", "axios"])(
    "shows fixed value-free copy for a %s refusal without reporting success",
    async (shape) => {
      const body = {
        status: false,
        type: "permission_error",
        code: "personal_key_workspace_required",
        message: UNTRUSTED_MESSAGE,
        result: UNTRUSTED_MESSAGE,
      };
      state.post.mockRejectedValue(
        shape === "normalized"
          ? { ...body, statusCode: 403 }
          : {
              message: "Request failed with status code 403",
              response: { status: 403, data: body },
            },
      );
      const { user, onCreated, onClose } = renderDialog();
      await submit(user);
      const dialog = screen.getByRole("dialog", { name: "Create API key" });
      const alert = await within(dialog).findByRole("alert");
      expect(alert.textContent).toBe(REMEDY);
      expect(screen.queryByText(UNTRUSTED_MESSAGE)).toBeNull();
      expect(screen.getByRole("textbox", { name: /Name/ })).toHaveValue("Personal key");
      expect(onCreated).not.toHaveBeenCalled();
      expect(onClose).not.toHaveBeenCalled();
      expect(state.post).toHaveBeenCalledExactlyOnceWith(
        endpoints.gateway.createApiKey,
        {
          gateway_id: "fixture-gateway",
          name: "Personal key",
          allowed_models: [],
          allowed_providers: [],
        },
      );
    },
  );

  it("preserves other creation errors", async () => {
    state.post.mockRejectedValue({ code: "invalid", message: "Name is required" });
    const { user, onCreated, onClose } = renderDialog();
    await submit(user);
    expect((await screen.findByRole("alert")).textContent).toBe("Name is required");
    expect(screen.queryByText(REMEDY)).toBeNull();
    expect(onCreated).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("keeps the successful one-time reveal callback", async () => {
    state.post.mockResolvedValue({ data: { result: {
      id: "fixture-key", name: "Personal key", key: "synthetic-show-once",
    } } });
    const { user, onCreated, onClose } = renderDialog();
    await submit(user);
    await waitFor(() => expect(onCreated).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({
        id: "fixture-key", name: "Personal key", secret: "synthetic-show-once",
      }),
    ));
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
