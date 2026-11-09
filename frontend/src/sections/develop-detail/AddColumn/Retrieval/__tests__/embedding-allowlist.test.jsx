// T6 #1, release-lead round-2 addition (a): the Retrieval form matches the
// server. Model serving loads only four Hugging Face embedding models, and
// add_vector_db_column refuses any other with "Hugging Face embedding model
// is not approved. Approved models: ...". The shared Retrieval form (the
// Vector DB drawer, its edit, and a conditional column's retrieval branch)
// must refuse the same IDs with the same message, name the approved models
// before submit, and send the snake_case body the endpoint accepts.
import React from "react";
import PropTypes from "prop-types";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { useForm } from "react-hook-form";

vi.mock("src/api/model/api-keys", () => ({
  useApiKeysStatus: () => ({ data: [] }),
}));
vi.mock(
  "src/sections/develop-detail/Common/ConfigureKeys/ConfigureKeys",
  () => ({
    default: () => null,
  }),
);

import EmbeddingConfigField from "../EmbeddingConfigField";
import * as validation from "../validation";
import * as retrieval from "../Retrieval";

// Server truth, read from the backend: its mirror of model serving's
// allowlist (pinned to serving by a backend test) and the request
// serializer of POST add_vector_db_column/.
const CONTRACTS = readFileSync(
  resolve(process.cwd(), "../futureagi/model_hub/serializers/contracts.py"),
  "utf8",
);
const SERVER_APPROVED = [
  ...CONTRACTS.match(
    /APPROVED_HUGGINGFACE_EMBEDDING_MODELS = frozenset\(\s*\{([^}]*)\}/,
  )[1].matchAll(/"([^"]+)"/g),
]
  .map((m) => m[1])
  .sort();
const SERVER_MESSAGE = `Hugging Face embedding model is not approved. Approved models: ${SERVER_APPROVED.join(", ")}.`;
const VECTOR_DB_REQUEST_FIELDS = [
  ...CONTRACTS.match(
    /class VectorDBColumnRequestSerializer\(serializers\.Serializer\):\n([\s\S]*?)\n\n/,
  )[1].matchAll(/^ {4}(\w+) = serializers\./gm),
].map((m) => m[1]);

const HF_TYPES = ["huggingface", "sentence_transformers"];
const CUSTOM_MODELS = [
  "nomic-ai/nomic-embed-text-v1",
  "/tmp/evil-model",
  "all-mpnet-base-v2 ",
  "ALL-MPNET-BASE-V2",
];

// A valid form per vector DB, as the drawer's fields hold it (camelCase).
const FORMS = {
  pinecone: {
    subType: "pinecone",
    newColumnName: "hits",
    columnId: "4d1f1d70-8f0e-4f53-9d55-7a4a1b0c2d3e",
    apiKey: "secret-id",
    indexName: "docs",
    namespace: "prod",
    topK: 2,
    queryKey: "vector",
    key: "text",
    concurrency: 1,
    vectorLength: 384,
  },
  qdrant: {
    subType: "qdrant",
    newColumnName: "hits",
    columnId: "4d1f1d70-8f0e-4f53-9d55-7a4a1b0c2d3e",
    apiKey: "secret-id",
    topK: 2,
    concurrency: 1,
    url: "https://qdrant.example.com",
    collectionName: "docs",
    key: "text",
    vectorLength: 384,
  },
  weaviate: {
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
  },
};
const SUB_TYPES = Object.keys(FORMS);
// RetrievalValidationSchema(isConditionalNode, isEdit)
/** @type {Array<[string, boolean, boolean]>} */
const VARIANTS = [
  ["drawer", false, false],
  ["edit", false, true],
  ["conditional branch", true, false],
];

const parse = (subType, embeddingConfig, [, conditional, edit]) =>
  validation
    .RetrievalValidationSchema(conditional, edit)
    .safeParse({ ...FORMS[subType], embeddingConfig });

const modelErrors = (result) =>
  result.success
    ? []
    : result.error.issues
        .filter((issue) => issue.path.join(".") === "embeddingConfig.model")
        .map((issue) => issue.message);

describe("Retrieval form refuses the Hugging Face models the server refuses", () => {
  it("knows the same four models as the server", () => {
    expect(SERVER_APPROVED).toHaveLength(4);
    expect([
      ...(validation.APPROVED_HUGGINGFACE_EMBEDDING_MODELS ?? []),
    ]).toEqual(SERVER_APPROVED);
  });

  describe.each(SUB_TYPES)("%s", (subType) => {
    describe.each(VARIANTS)("%s", (...variant) => {
      it.each(HF_TYPES.flatMap((t) => CUSTOM_MODELS.map((m) => [t, m])))(
        "%s refuses %j with the server's message",
        (type, model) => {
          const result = parse(subType, { type, model }, variant);
          expect(result.success).toBe(false);
          expect(modelErrors(result)).toEqual([SERVER_MESSAGE]);
        },
      );

      it.each(HF_TYPES.flatMap((t) => SERVER_APPROVED.map((m) => [t, m])))(
        "%s accepts approved %s",
        (type, model) => {
          expect(parse(subType, { type, model }, variant).success).toBe(true);
        },
      );

      it.each([
        ["openai", "text-embedding-3-large"],
        ["openai", "my-org/azure-embedding-deployment"],
        // What the form holds when no embedding is configured.
        ["", ""],
      ])("%j / %j is not restricted", (type, model) => {
        expect(parse(subType, { type, model }, variant).success).toBe(true);
      });
    });
  });
});

const FieldHarness = ({ type }) => {
  const { control } = useForm({
    defaultValues: { embeddingConfig: { type, model: "" } },
  });
  return <EmbeddingConfigField control={control} />;
};
FieldHarness.propTypes = { type: PropTypes.string };

describe("EmbeddingConfigField names the allowed models before submit", () => {
  it("lists the approved models for Hugging Face", () => {
    render(<FieldHarness type="huggingface" />);
    const hint = screen.getByTestId("embedding-model-hint");
    for (const model of SERVER_APPROVED) expect(hint).toHaveTextContent(model);
  });

  it("says Sentence Transformers loads no model on this server", () => {
    render(<FieldHarness type="sentence_transformers" />);
    expect(screen.getByTestId("embedding-model-hint")).toHaveTextContent(
      "not available on this server",
    );
  });

  it("keeps OpenAI model entry free", () => {
    render(<FieldHarness type="openai" />);
    expect(screen.queryByTestId("embedding-model-hint")).toBeNull();
  });
});

describe("Retrieval form sends the body add_vector_db_column accepts", () => {
  it("reads the serializer's fields", () => {
    expect(VECTOR_DB_REQUEST_FIELDS).toEqual(
      expect.arrayContaining(["column_id", "api_key", "embedding_config"]),
    );
  });

  it.each(SUB_TYPES)(
    "%s: every key is a snake_case request field",
    (subType) => {
      const embeddingConfig = {
        type: "huggingface",
        model: SERVER_APPROVED[0],
      };
      const body = retrieval.retrievalFormToApi?.({
        ...FORMS[subType],
        embeddingConfig,
      });

      expect(body).toBeTypeOf("object");
      const sent = Object.keys(body).filter((k) => body[k] !== undefined);
      expect(sent.filter((k) => !VECTOR_DB_REQUEST_FIELDS.includes(k))).toEqual(
        [],
      );
      expect(body).toMatchObject({
        sub_type: subType,
        column_id: FORMS[subType].columnId,
        api_key: "secret-id",
        top_k: 2,
        vector_length: 384,
        embedding_config: embeddingConfig,
      });
    },
  );

  it("loads saved snake_case metadata into the form's fields for edit", () => {
    const saved = {
      sub_type: "pinecone",
      index_name: "docs",
      top_k: 2,
      namespace: "prod",
      api_key: "secret-id",
      embedding_config: { type: "huggingface", model: "all-mpnet-base-v2" },
      column_id: "4d1f1d70-8f0e-4f53-9d55-7a4a1b0c2d3e",
      concurrency: 1,
      query_key: "vector",
      vector_length: 384,
      key: "text",
    };
    const form = retrieval.retrievalApiToForm?.(saved);

    expect(form).toMatchObject({
      subType: "pinecone",
      indexName: "docs",
      topK: 2,
      apiKey: "secret-id",
      embeddingConfig: saved.embedding_config,
      columnId: saved.column_id,
      queryKey: "vector",
      vectorLength: 384,
    });
    // The edit form's values already camelCase (a conditional branch).
    expect(retrieval.retrievalApiToForm(form)).toEqual(form);
  });
});
