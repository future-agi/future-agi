// Without the optional `ml` extra, the config route answers a request that
// names no revision with the base's HTTP-only metadata: splits, with no
// revision and no row counts. The backend test
// test_a_base_only_install_answers_a_config_request_without_revision pins
// that dataset_info. The real drawer must still create a dataset and add
// rows from that answer, without a revision as at the base, whether or not
// the Revision control is shown.
import React from "react";
import PropTypes from "prop-types";
import { useController } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import { endpoints } from "src/utils/axios";
import HuggingFaceView from "../HuggingFaceView";

const h = vi.hoisted(() => ({ post: vi.fn(), revisionShown: true }));

vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: (id) => (id === "hf.revision" ? h.revisionShown : true),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const real = /** @type {typeof import("src/utils/axios")} */ (
    await importOriginal()
  );
  return {
    ...real,
    default: { ...real.default, post: (...a) => h.post(...a) },
  };
});
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: () => {},
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: () => {},
}));
// The searchable picker, as a plain select on the same form field.
vi.mock("src/components/FromSearchSelectField", async (importOriginal) => {
  function SelectStub({ control, fieldName, options, label }) {
    const { field } = useController({ control, name: fieldName });
    return (
      <select
        aria-label={label}
        value={field.value ?? ""}
        onChange={(e) => field.onChange(e.target.value)}
      >
        <option value="">Select Option</option>
        {(options ?? []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  SelectStub.propTypes = {
    control: PropTypes.object,
    fieldName: PropTypes.string,
    options: PropTypes.array,
    label: PropTypes.string,
  };
  return {
    ...(await importOriginal()),
    FormSearchSelectFieldControl: SelectStub,
  };
});

const REPO = "fixture/legacy";
// The response body GetHuggingFaceDatasetConfigView sends without `ml`.
const NO_ML_CONFIG = {
  status: true,
  result: {
    message: "Dataset configuration retrieved successfully",
    dataset_info: { splits: { default: ["train", "test"] } },
  },
};

const theme = createTheme({ palette: palette("light") });
const posts = (url) => h.post.mock.calls.filter(([u]) => u === url);

beforeEach(() => {
  h.revisionShown = true;
  const bodies = new Map(
    /** @type {[string, { status?: boolean, result: object }][]} */ ([
      [
        endpoints.huggingFace.list,
        { result: { datasets: [{ id: REPO, author: "fixture" }] } },
      ],
      [
        endpoints.huggingFace.detail,
        {
          result: { dataset: { id: REPO, name: "legacy", author: "fixture" } },
        },
      ],
      [endpoints.develop.getHuggingFaceDataset, NO_ML_CONFIG],
      [
        endpoints.develop.createHuggingFaceDataset,
        { status: true, result: { dataset_id: "ds-new" } },
      ],
      [
        endpoints.huggingFace.addHuggingFaceRow("ds-1"),
        { status: true, result: { message: "2 Row(s) imported Succesfully" } },
      ],
    ]),
  );
  h.post.mockReset();
  h.post.mockImplementation(async (url) => ({
    data: bodies.get(url) ?? { result: {} },
  }));
});

function renderView(state) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter
          initialEntries={[{ pathname: "/dashboard/huggingface", state }]}
        >
          <HuggingFaceView />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

// Opens the repo's drawer and chooses its subset and split once the detail
// and the config answer (no revision) are both in.
async function chooseLegacySplit() {
  fireEvent.click(await screen.findByTestId("hf-item"));
  await screen.findByText("legacy");
  await screen.findByRole("option", { name: "default" });
  if (h.revisionShown) {
    expect(screen.getByRole("textbox", { name: "Revision" })).toHaveValue("");
  } else {
    expect(
      screen.queryByRole("textbox", { name: "Revision" }),
    ).not.toBeInTheDocument();
  }
  fireEvent.change(screen.getByLabelText("Subset"), {
    target: { value: "default" },
  });
  await screen.findByRole("option", { name: "train" });
  fireEvent.change(screen.getByLabelText("Split"), {
    target: { value: "train" },
  });
}

const expectConfigReadsWithoutRevision = () => {
  const reads = posts(endpoints.develop.getHuggingFaceDataset);
  expect(reads.length).toBeGreaterThan(0);
  for (const [, body] of reads) expect(body).toEqual({ dataset_path: REPO });
};

describe("Hugging Face import without the ml extra (real backend)", () => {
  it.each([true, false])(
    "creates a dataset from the answer without a revision (Revision shown: %s)",
    async (shown) => {
      h.revisionShown = shown;
      renderView();
      await chooseLegacySplit();
      fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
        target: { value: "legacy-copy" },
      });

      const submit = screen.getByRole("button", { name: "Create dataset" });
      expect(submit).toBeEnabled();
      fireEvent.click(submit);

      const create = endpoints.develop.createHuggingFaceDataset;
      await waitFor(() => expect(posts(create)).toHaveLength(1));
      const [[, body]] = posts(create);
      expect(body).toMatchObject({
        name: "legacy-copy",
        huggingface_dataset_name: REPO,
        huggingface_dataset_config: "default",
        huggingface_dataset_split: "train",
        model_type: "GenerativeLLM",
      });
      expect(body).not.toHaveProperty("revision");
      expect(body).not.toHaveProperty("huggingface_dataset_revision");
      expectConfigReadsWithoutRevision();
    },
  );

  it.each([true, false])(
    "adds rows from the answer without a revision (Revision shown: %s)",
    async (shown) => {
      h.revisionShown = shown;
      renderView({ datasetId: "ds-1" });
      await chooseLegacySplit();

      const submit = screen.getByRole("button", { name: "Add rows" });
      expect(submit).toBeEnabled();
      fireEvent.click(submit);

      const add = endpoints.huggingFace.addHuggingFaceRow("ds-1");
      await waitFor(() => expect(posts(add)).toHaveLength(1));
      const [[, body]] = posts(add);
      expect(body).toMatchObject({
        huggingface_dataset_name: REPO,
        huggingface_dataset_config: "default",
        huggingface_dataset_split: "train",
      });
      expect(body).not.toHaveProperty("revision");
      expect(body).not.toHaveProperty("huggingface_dataset_revision");
      expectConfigReadsWithoutRevision();
    },
  );
});
