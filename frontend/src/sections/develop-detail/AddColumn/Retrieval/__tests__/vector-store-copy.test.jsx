// R1-05 (one key): a Retrieval column talks to the customer's own Pinecone,
// Qdrant or Weaviate. That key authenticates to the vector database only; it
// is not model access, and model access (Future AGI credits or a provider
// key) never stands in for it. Each form says so next to the key field, and
// the key stays a required field of its own (validation.js).
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("src/sections/common/SecretSelect/SecretSelect", () => ({
  default: ({ label, helperText }) => (
    <div data-testid="vector-store-auth">
      <span>{label}</span>
      {helperText}
    </div>
  ),
}));
vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldControl: () => null,
}));
vi.mock("src/components/HeadingAndSubheading/HeadingAndSubheading", () => ({
  default: () => null,
}));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: () => null,
}));
vi.mock("../EmbeddingConfigField", () => ({ default: () => null }));
// Weaviate's Search Type select (weaviate-search-type.test.jsx covers it).
vi.mock("src/components/FormSelectField", () => ({
  FormSelectField: () => null,
}));

import PineConeForm from "../PineConeForm";
import QdrantForm from "../QdrantForm";
import WeivateForm from "../WeivateForm";
import { RetrievalValidationSchema } from "../validation";

describe.each([
  ["Pinecone", PineConeForm, "index"],
  ["Qdrant", QdrantForm, "collection"],
  ["Weaviate", WeivateForm, "cluster"],
])("%s form", (store, Form, target) => {
  it("labels the key as the vector database's own", () => {
    render(<Form control={{}} allColumns={[]} />);
    const field = screen.getByTestId("vector-store-auth");
    expect(field).toHaveTextContent(`${store} API Key`);
    expect(field).toHaveTextContent(
      `Your ${store} API key, used only to query this ${target}.`,
    );
    expect(field).toHaveTextContent(
      "It is separate from model access (Future AGI credits or provider keys).",
    );
  });
});

describe("the vector database key stays required on its own", () => {
  it.each(["pinecone", "qdrant", "weaviate"])(
    "%s refuses a form without it, even with an embedding configured",
    (subType) => {
      const result = RetrievalValidationSchema(false, false).safeParse({
        subType,
        newColumnName: "hits",
        columnId: "c1",
        embeddingConfig: { type: "openai", model: "text-embedding-3-small" },
      });
      expect(result.success).toBe(false);
      expect(
        result.error.issues.some((issue) => issue.path.join(".") === "apiKey"),
      ).toBe(true);
    },
  );
});
