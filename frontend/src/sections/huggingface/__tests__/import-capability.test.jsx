import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import { endpoints } from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import {
  huggingFaceImportDisabled,
  offeredImportOptions,
  rowImportOption,
} from "src/sections/develop/importOptions";
import HuggingFaceView from "../HuggingFaceView";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: http.get, post: http.post },
  };
});
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: () => {},
}));

const NOTICE =
  "This install lacks the datasets package needed for Hugging Face import.";
const clients = [];
const capabilityResponse = (allowed) => ({
  data: { features: { huggingface_import: { allowed } } },
});

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, retryDelay: 0 } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <HuggingFaceView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  http.get.mockReset();
  http.post.mockReset().mockResolvedValue({
    data: { result: { datasets: [], total_datasets: 0 } },
  });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
});

describe.each([true, false])(
  "Hugging Face availability (launch=%s)",
  (launch) => {
    beforeEach(() => setLaunchOverrideForTest({ launch, madeReal: [] }));

    it.each([true, false])(
      "uses the server answer in every chooser (%s)",
      (allowed) => {
        const capability = { allowed };
        expect(huggingFaceImportDisabled(capability)).toBe(!allowed);
        const create = offeredImportOptions(capability).find(
          ({ id }) => id === "importFromHuggingface",
        );
        const add = rowImportOption(
          { id: "importFromHuggingface" },
          capability,
        );
        for (const option of [create, add]) {
          expect(option).toBeDefined();
          expect(Boolean(option.disabled)).toBe(!allowed);
          if (!allowed) expect(option.disabledReason).toBe(NOTICE);
        }
      },
    );

    it("opens the catalog when the package is available", async () => {
      http.get.mockResolvedValue(capabilityResponse(true));
      mount();

      await waitFor(() =>
        expect(http.post).toHaveBeenCalledWith(
          endpoints.huggingFace.list,
          expect.objectContaining({ search_query: "" }),
          expect.any(Object),
        ),
      );
      expect(screen.getByPlaceholderText("Search")).toBeVisible();
      expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
    });

    it("shows the package explanation without mounting the catalog", async () => {
      http.get.mockResolvedValue(capabilityResponse(false));
      mount();

      expect(await screen.findByText(NOTICE)).toBeVisible();
      expect(
        screen.getByRole("heading", { name: "Hugging Face" }),
      ).toBeVisible();
      for (const name of ["Create dataset", "Add rows"]) {
        expect(screen.getByRole("button", { name })).toBeDisabled();
      }
      expect(http.post).not.toHaveBeenCalled();
      expect(screen.queryByPlaceholderText("Search")).not.toBeInTheDocument();
    });
  },
);

it.each([
  undefined,
  { allowed: false },
  { allowed: "true" },
  { allowed: true, isLoading: true },
  { allowed: true, isError: true },
])(
  "keeps imports inactive until availability is confirmed (%j)",
  (capability) => expect(huggingFaceImportDisabled(capability)).toBe(true),
);

it.each([
  undefined,
  { allowed: false, isKnown: false, reason: NOTICE },
  { allowed: false, isError: true, reason: NOTICE },
  { allowed: "false" },
])("keeps unconfirmed chooser notices neutral (%j)", (capability) => {
  const create = offeredImportOptions(capability).find(
    ({ id }) => id === "importFromHuggingface",
  );
  const add = rowImportOption({ id: "importFromHuggingface" }, capability);
  for (const option of [create, add]) {
    expect(option.disabled).toBe(true);
    expect(option.disabledReason).toBe(
      "Could not check Hugging Face import availability.",
    );
  }
  expect(add.subTitle).toBe(
    "Could not check Hugging Face import availability.",
  );
});

it("does not describe a pending capability read as a missing package", () => {
  http.get.mockImplementation(() => new Promise(() => {}));
  mount();

  expect(screen.getByRole("status")).toHaveTextContent(
    "Checking Hugging Face import availability.",
  );
  expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
  expect(http.post).not.toHaveBeenCalled();
});

it("can retry a failed capability read before opening the catalog", async () => {
  http.get.mockRejectedValue(new Error("Unavailable"));
  mount();

  expect(
    await screen.findByText(
      "Could not check Hugging Face import availability.",
    ),
  ).toBeVisible();
  expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
  expect(http.post).not.toHaveBeenCalled();
  http.get.mockResolvedValue(capabilityResponse(true));
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByPlaceholderText("Search")).toBeVisible();
});

it("does not mistake an older server response for a missing package", async () => {
  http.get.mockResolvedValue({ data: { features: {} } });
  mount();

  expect(
    await screen.findByText(
      "Could not check Hugging Face import availability.",
    ),
  ).toBeVisible();
  expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
  expect(http.post).not.toHaveBeenCalled();
});
