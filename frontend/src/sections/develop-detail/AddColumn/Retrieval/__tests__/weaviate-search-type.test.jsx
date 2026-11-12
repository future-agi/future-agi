// Restore row 2: Dataset > Add column > Retrieval > Weaviate shows the
// "Search Type" select production has (Semantic Search / Hybrid). The
// vector_db column reads it: dynamic_columns.py _query_weaviate runs a
// hybrid query when search_type is "hybrid" and a near-vector query
// otherwise, and POST add_vector_db_column/ accepts search_type
// (VectorDBColumnRequestSerializer). The form sends snake_case keys
// (retrievalFormToApi), so choosing Hybrid must put search_type "hybrid" in
// the submitted body.
import React from "react";
import PropTypes from "prop-types";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

// Retrieval is imported only for its request converter; keep the
// application's HTTP client and the drawer's other children out.
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: { develop: { modelList: "/model-hub/api/models_list/" } },
}));
vi.mock("../PineConeForm", () => ({ default: () => null }));
vi.mock("../QdrantForm", () => ({ default: () => null }));
vi.mock("../../PreviewAddColumn", () => ({ default: () => null }));
vi.mock("src/api/develop/develop-detail", () => ({
  useDatasetColumnConfig: () => [],
}));
vi.mock("src/api/model/api-keys", () => ({
  useApiKeysStatus: () => ({ data: [] }),
}));
vi.mock(
  "src/sections/develop-detail/Common/modelAccess",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useEmbeddingAccess: () => ({ data: undefined, isLoading: false }),
  }),
);
vi.mock(
  "src/sections/develop-detail/Common/ConfigureKeys/ConfigureKeys",
  () => ({ default: () => null }),
);

// The Weaviate form's other fields are not under test here.
vi.mock("src/sections/common/SecretSelect/SecretSelect", () => ({
  default: () => null,
}));
vi.mock("src/components/FromSearchSelectField", async (importOriginal) => ({
  ...(await importOriginal()),
  FormSearchSelectFieldControl: () => null,
}));
vi.mock("src/components/HeadingAndSubheading/HeadingAndSubheading", () => ({
  default: () => null,
}));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: () => null,
}));
vi.mock("../EmbeddingConfigField", () => ({ default: () => null }));

/* eslint-disable react/prop-types -- test stub for FormSelectField */
// FormSelectField as a native select bound to the same form field, so the
// label, options and chosen value are read without MUI's menu portal.
vi.mock("src/components/FormSelectField", async (importOriginal) => {
  const { Controller } = await import("react-hook-form");
  return {
    ...(await importOriginal()),
    FormSelectField: ({ control, fieldName, label, options }) => (
      <Controller
        control={control}
        name={fieldName}
        render={({ field, fieldState }) => (
          <>
            <label htmlFor={`select-${fieldName}`}>{label}</label>
            <select
              id={`select-${fieldName}`}
              name={field.name}
              value={field.value ?? ""}
              onChange={field.onChange}
              onBlur={field.onBlur}
            >
              <option value="">Select</option>
              {options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <span>{fieldState.error?.message}</span>
          </>
        )}
      />
    ),
  };
});

import WeivateForm from "../WeivateForm";
import { RetrievalValidationSchema } from "../validation";
import { retrievalFormToApi } from "../Retrieval";

// Server truth, read from the backend at this tree.
const DYNAMIC_COLUMNS = readFileSync(
  resolve(process.cwd(), "../futureagi/model_hub/views/dynamic_columns.py"),
  "utf8",
);
const CONTRACTS = readFileSync(
  resolve(process.cwd(), "../futureagi/model_hub/serializers/contracts.py"),
  "utf8",
);
const VECTOR_DB_REQUEST_FIELDS = [
  ...CONTRACTS.match(
    /class VectorDBColumnRequestSerializer\(serializers\.Serializer\):\n([\s\S]*?)\n\n/,
  )[1].matchAll(/^ {4}(\w+) = serializers\./gm),
].map((m) => m[1]);

// A complete Weaviate form with Search Type not chosen yet ("" is what the
// select holds before a choice; .min(1) then gives the production message).
const WEAVIATE = {
  searchType: "",
  subType: "weaviate",
  newColumnName: "hits",
  columnId: "4d1f1d70-8f0e-4f53-9d55-7a4a1b0c2d3e",
  topK: 2,
  apiKey: "secret-id",
  concurrency: 1,
  url: "https://weaviate.example.com",
  collectionName: "docs",
  key: "text",
  vectorLength: 384,
  embeddingConfig: { type: "", model: "" },
};

const Harness = ({ onSubmit }) => {
  const { control, handleSubmit } = useForm({
    defaultValues: WEAVIATE,
    resolver: zodResolver(RetrievalValidationSchema(false, false)),
  });
  return (
    <form
      onSubmit={handleSubmit((values) => onSubmit(retrievalFormToApi(values)))}
    >
      <WeivateForm control={control} allColumns={[]} />
      <button type="submit">Create New Column</button>
    </form>
  );
};
Harness.propTypes = { onSubmit: PropTypes.func };

describe("Weaviate form: Search Type", () => {
  it("the server reads search_type and runs a hybrid query for 'hybrid'", () => {
    expect(VECTOR_DB_REQUEST_FIELDS).toContain("search_type");
    expect(DYNAMIC_COLUMNS).toContain('config.get("search_type") == "hybrid"');
    expect(DYNAMIC_COLUMNS).toContain(
      'config.get("search_type", "semantic_search")',
    );
  });

  it("shows the Search Type select with Semantic Search and Hybrid", () => {
    render(<Harness onSubmit={vi.fn()} />);
    const select = screen.getByRole("combobox", { name: "Search Type" });
    const semantic = within(select).getByRole("option", {
      name: "Semantic Search",
    });
    const hybrid = within(select).getByRole("option", { name: "Hybrid" });
    expect(semantic.value).toBe("semantic_search");
    expect(hybrid.value).toBe("hybrid");
  });

  it("choosing Hybrid sends search_type 'hybrid' in the submitted body", async () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);
    fireEvent.change(screen.getByRole("combobox", { name: "Search Type" }), {
      target: { value: "hybrid" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create New Column" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const body = onSubmit.mock.calls[0][0];
    expect(body).toMatchObject({ sub_type: "weaviate", search_type: "hybrid" });
    expect(body).not.toHaveProperty("searchType");
  });

  it("choosing Semantic Search sends search_type 'semantic_search'", async () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);
    fireEvent.change(screen.getByRole("combobox", { name: "Search Type" }), {
      target: { value: "semantic_search" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create New Column" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      search_type: "semantic_search",
    });
  });

  it("asks for a search type before submitting, as production does", async () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: "Create New Column" }));

    expect(
      await screen.findByText("Please enter a search type"),
    ).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("validation requires a search type and accepts both options", () => {
    const schema = RetrievalValidationSchema(false, false);
    const missing = schema.safeParse(WEAVIATE);
    expect(missing.success).toBe(false);
    expect(
      missing.error.issues
        .filter((issue) => issue.path.join(".") === "searchType")
        .map((issue) => issue.message),
    ).toEqual(["Please enter a search type"]);

    for (const searchType of ["semantic_search", "hybrid"]) {
      expect(schema.safeParse({ ...WEAVIATE, searchType }).success).toBe(true);
    }
  });
});
