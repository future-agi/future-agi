import React from "react";
import { cleanup, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { recoveryKey } from "../createAdmission";
import {
  recoveryDraftKey,
  useCreateDraftRecovery,
  useRecoveryPrefill,
} from "../createAdmissionDraft";

const datasetId = "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3e";
const body = { dataset_id: datasetId, name: "Saved duplicate" };
// A Duplicate & edit form has no `rowIds`; its snapshot omits the key.
const savedValues = { name: "Saved duplicate", promptConfig: [] };
const linkPrefill = {
  row_ids: ["0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c45"],
  row_refs: ["0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c45"],
};

const clients = [];
const clientWith = ({ saved = body, draftBody = body } = {}) => {
  const client = new QueryClient();
  clients.push(client);
  if (saved) client.setQueryData(recoveryKey(datasetId), saved);
  if (draftBody)
    client.setQueryData(recoveryDraftKey(datasetId), {
      mount: 0,
      values: savedValues,
      body: draftBody,
    });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
};
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("saved draft restore", () => {
  it("clears fresh fields the saved form did not have", () => {
    const { wrapper } = clientWith();
    const fresh = {
      name: "",
      promptConfig: [],
      rowIds: linkPrefill.row_ids,
    };
    const setValue = vi.fn();
    const keepChoices = vi.fn();
    renderHook(
      () =>
        useCreateDraftRecovery(datasetId, body, {
          getValues: () => fresh,
          setValue,
          setCurrentStep: vi.fn(),
          keepChoices,
        }),
      { wrapper },
    );
    expect(setValue).toHaveBeenCalledWith("rowIds", undefined, {
      shouldDirty: true,
    });
    expect(setValue).toHaveBeenCalledWith("name", "Saved duplicate", {
      shouldDirty: true,
    });
    expect(setValue).not.toHaveBeenCalledWith(
      "promptConfig",
      undefined,
      expect.anything(),
    );
    expect(keepChoices).toHaveBeenCalledWith(savedValues);
  });
});

describe("prefill of a drawer that restores a saved submission", () => {
  it("drops the opening link's prefill when a saved draft will be restored", () => {
    const { wrapper } = clientWith();
    const { result } = renderHook(
      () => useRecoveryPrefill(datasetId, linkPrefill, false),
      { wrapper },
    );
    expect(result.current).toBeNull();
  });

  it.each([
    ["no saved submission", { saved: null }],
    ["no saved draft", { draftBody: null }],
    ["a draft of another request", { draftBody: { ...body, name: "Other" } }],
  ])("keeps the link's prefill with %s", (_, state) => {
    const { wrapper } = clientWith(state);
    const { result } = renderHook(
      () => useRecoveryPrefill(datasetId, linkPrefill, false),
      { wrapper },
    );
    expect(result.current).toBe(linkPrefill);
  });

  it("keeps the prefill while editing an experiment", () => {
    const { wrapper } = clientWith();
    const { result } = renderHook(
      () => useRecoveryPrefill(datasetId, linkPrefill, true),
      { wrapper },
    );
    expect(result.current).toBe(linkPrefill);
  });
});
