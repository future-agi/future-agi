/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Controller } from "react-hook-form";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  notify: vi.fn(),
  navigate: vi.fn(),
  close: vi.fn(),
  refresh: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post, get: mocks.get },
  endpoints: { develop: { uploadDatasetLocalFile: "/upload/" } },
}));
vi.mock("react-router", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("src/utils/prototype", () => ({
  isPrototypeMode: () => false,
  prototypeModeFromEnv: () => false,
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: mocks.notify }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../UploadedFileIllustration", () => ({ default: () => null }));
vi.mock("../ImportPreviewPane", () => ({ default: () => null }));
vi.mock("../ImportFileExtras", () => ({ default: () => null }));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: () => null }));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: ({ control, fieldName, label }) => (
    <Controller
      name={fieldName}
      control={control}
      render={({ field }) => <input aria-label={label} {...field} />}
    />
  ),
}));
vi.mock("src/components/hook-form", () => ({
  RHFUpload: ({ onDrop }) => (
    <button
      onClick={() => {
        const file = new File(["input\nhello\n"], "data.csv", {
          type: "text/csv",
        });
        file.text = async () => "input\nhello\n";
        onDrop([file]);
      }}
    >
      Choose fixture
    </button>
  ),
}));

import UploadFileModal from "../UploadFileModal";

// The upload dialog is named by its title alone and described by its helper
// line, so assistive tech and role queries find it as "Upload a file".
let client;
beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
});

describe("the upload dialog's name and description", () => {
  it("is named by its title alone", () => {
    render(
      <QueryClientProvider client={client}>
        <UploadFileModal
          open
          onClose={mocks.close}
          refreshGrid={mocks.refresh}
        />
      </QueryClientProvider>,
    );
    const dialog = screen.getByRole("dialog", { name: "Upload a file" });
    expect(dialog).toBeVisible();
  });

  it("is described by the line that says which files it takes", () => {
    render(
      <QueryClientProvider client={client}>
        <UploadFileModal
          open
          onClose={mocks.close}
          refreshGrid={mocks.refresh}
        />
      </QueryClientProvider>,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleDescription(
      /^CSV, JSON or JSONL, up to 25 MB\. After you choose a file/,
    );
  });
});
