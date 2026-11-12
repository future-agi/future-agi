// Hugging Face imports use resolved revisions and persist rows immediately.
// Preview and pending-change interactions belong only to prototype builds.
import { createElement, useState } from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";

const proto = vi.hoisted(() => ({ on: false }));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => proto.on,
}));
const http = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { ...real.default, post: http.post } };
});
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: () => {},
}));

import {
  hfConfigBody,
  hfDirectAddHint,
  hfDirectAddToast,
  hfImportedCount,
  hfPreviewAvailable,
  hfRequestFields,
  hfRequestReady,
  hfRequestRevision,
  hfRevisionChoices,
  hfSubmitLabel,
} from "../hfLaunchModel";
import HuggingDetailForm from "../HuggingDetailForm";
import { IMPORTS_PATH } from "src/sections/common/AddData/mappingModel";
import {
  ADD_ROWS_OPTIONS,
  IMPORT_OPTIONS,
} from "src/sections/develop/importOptions";

const REVISIONS = [
  { sha: "abc123", label: "main", date: "2026-10-01", num_rows: 10 },
];
const CONFIG = {
  revision: "abc123",
  revisions: REVISIONS,
  dataset_info: {
    revision: "resolved-commit",
    splits: { default: ["train"] },
    num_rows: { default: { train: 150 } },
  },
};
// The config route's answer without the optional `ml` extra to a request
// that names no revision: the base's HTTP-only splits, with no revision and
// no row counts. The backend test
// test_a_base_only_install_answers_a_config_request_without_revision
// pins this dataset_info.
const NO_ML_CONFIG = {
  message: "Dataset configuration retrieved successfully",
  dataset_info: { splits: { default: ["train", "test"] } },
};

describe("hfLaunchModel (pure)", () => {
  it("the real backend gets only the fields its strict routes take", () => {
    expect(hfConfigBody("scikit-learn/iris", "abc123")).toEqual({
      dataset_path: "scikit-learn/iris",
      revision: "abc123",
    });
    expect(hfConfigBody("scikit-learn/iris", "abc123")).toEqual({
      dataset_path: "scikit-learn/iris",
      revision: "abc123",
    });
    expect(hfRequestRevision(CONFIG, false)).toBe("resolved-commit");
    expect(hfRequestRevision(CONFIG, true)).toBe("abc123");
    expect(hfRevisionChoices(CONFIG, false)).toEqual([]);
    expect(hfRevisionChoices(CONFIG, true)).toEqual(REVISIONS);
    expect(hfPreviewAvailable(false)).toBe(false);
    expect(hfPreviewAvailable(true)).toBe(true);
  });

  it("an answer without a revision sends none; a resolved commit is pinned", () => {
    expect(hfRequestReady(NO_ML_CONFIG, "", false)).toBe(true);
    expect(hfRequestReady(NO_ML_CONFIG, "  ", false)).toBe(true);
    expect(hfRequestReady(NO_ML_CONFIG, "v1.0", false)).toBe(false);
    expect(hfRequestReady(null, "", false)).toBe(false);
    expect(hfRequestReady(CONFIG, "v1.0", false)).toBe(true);
    expect(hfRequestReady(null, "", true)).toBe(true);
    expect(hfRequestFields(NO_ML_CONFIG, false)).toEqual({});
    expect(hfRequestFields(CONFIG, false)).toEqual({
      revision: "resolved-commit",
    });
    expect(hfRequestFields(CONFIG, true)).toEqual({
      huggingface_dataset_revision: "abc123",
    });
  });

  it("an add on the real backend never says pending changes", () => {
    expect(hfSubmitLabel("ds-1", false)).toBe("Add rows");
    expect(hfSubmitLabel("ds-1", true)).toBe("Add to pending changes");
    expect(hfSubmitLabel(undefined, false)).toBe("Create dataset");
    expect(hfImportedCount({ message: "3 Row(s) imported Succesfully" })).toBe(
      3,
    );
    expect(hfImportedCount({})).toBeNull();
    expect(
      hfDirectAddToast({ message: "3 Row(s) imported Succesfully" }, 9),
    ).toBe("3 rows added to the dataset; their values load in the background");
    expect(hfDirectAddToast({}, 1)).toBe(
      "1 row added to the dataset; their values load in the background",
    );
    expect(hfDirectAddToast({}, undefined)).toBe(
      "Rows added to the dataset; their values load in the background",
    );
    for (const text of [hfDirectAddToast({}, 2), hfDirectAddHint]) {
      expect(text).not.toMatch(/pending|revision|Save version/i);
    }
  });

  it("the import sheets describe the real Hugging Face door", () => {
    const create = IMPORT_OPTIONS.find((o) => o.id === "importFromHuggingface");
    const add = ADD_ROWS_OPTIONS.find((o) => o.id === "importFromHuggingface");
    expect(create.subTitle).toBe("A public dataset from the Hugging Face Hub");
    expect(add.subTitle).toBe(
      "Rows of a public dataset, added to this dataset now",
    );
  });
});

/** @param {PropTypes.InferProps<typeof Drawer.propTypes>} props */
function Drawer({ target, config = CONFIG, revision = "abc123", onSubmit }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  const { control } = useForm({
    defaultValues: {
      name: "iris-copy",
      huggingface_dataset_config: "default",
      huggingface_dataset_split: "train",
      num_rows: 2,
    },
  });
  return createElement(
    QueryClientProvider,
    { client },
    createElement(HuggingDetailForm, {
      control,
      config,
      huggingFaceDetail: { id: "scikit-learn/iris", name: "iris" },
      subsetOptions: [{ label: "default", value: "default" }],
      splitOptions: [{ label: "train", value: "train" }],
      targetDatasetId: target,
      showNameField: !target,
      revision,
      onRevision: () => {},
      onSubmit: onSubmit ?? (() => {}),
      onClose: () => {},
      isLoadingCreateDataset: false,
    }),
  );
}

Drawer.propTypes = {
  target: PropTypes.string,
  config: PropTypes.object,
  revision: PropTypes.string,
  onSubmit: PropTypes.func,
};

const importCalls = () =>
  http.post.mock.calls.filter(([url]) => url === IMPORTS_PATH);

beforeEach(() => {
  proto.on = false;
  http.post.mockReset().mockResolvedValue({
    data: { result: { summary: { add: 2 }, new_columns: [], v1: null } },
  });
});

describe("HuggingDetailForm by build", () => {
  it("real backend: Add rows with a revision and no preview request", async () => {
    render(createElement(Drawer, { target: "ds-1" }));

    const submit = screen.getByRole("button", { name: "Add rows" });
    expect(submit).toBeEnabled();
    expect(screen.getByTestId("hf-direct-add-hint")).toHaveTextContent(
      hfDirectAddHint,
    );
    expect(screen.getByRole("textbox", { name: "Revision" })).toHaveValue(
      "abc123",
    );
    expect(screen.queryByText(/pending changes/i)).not.toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));
    expect(importCalls()).toEqual([]);
  });

  it.each([
    [undefined, "Create dataset"],
    ["ds-1", "Add rows"],
  ])(
    "real backend without ml: the answer without a revision submits (target %s)",
    async (target, label) => {
      const onSubmit = vi.fn();
      render(
        createElement(Drawer, {
          target,
          config: NO_ML_CONFIG,
          revision: "",
          onSubmit,
        }),
      );

      const submit = screen.getByRole("button", { name: label });
      expect(submit).toBeEnabled();
      fireEvent.click(submit);
      expect(onSubmit).toHaveBeenCalledTimes(1);
      await new Promise((r) => setTimeout(r, 50));
      expect(importCalls()).toEqual([]);
    },
  );

  it("real backend without ml: a typed revision with no resolved commit cannot submit", () => {
    render(createElement(Drawer, { config: NO_ML_CONFIG, revision: "v1.0" }));

    expect(
      screen.getByRole("button", { name: "Create dataset" }),
    ).toBeDisabled();
  });

  it("prototype: the IM1 preview, the Revision control and pending changes", async () => {
    proto.on = true;
    render(createElement(Drawer, { target: "ds-1" }));

    expect(
      screen.getByRole("button", { name: "Add to pending changes" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("hf-revision")).toBeInTheDocument();
    expect(screen.queryByTestId("hf-direct-add-hint")).not.toBeInTheDocument();
    await waitFor(() => expect(importCalls().length).toBe(1));
  });
});
