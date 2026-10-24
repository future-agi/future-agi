import { Box } from "@mui/material";
import React, { useState } from "react";
import AddToDataset from "../dialogue-boxes/add-to-dataset";
import CreateNewDataset from "../dialogue-boxes/create-new-dataset";
import ChainSpan from "./RightSpans/ChainSpan";
import LLMSpan from "./RightSpans/LLMSpan";
import EmbeddingsSpan from "./RightSpans/EmbeddingsSpan";
import RetreiveSpan from "./RightSpans/RetrieveSpan";
import RankerSpan from "./RightSpans/RankerSpan";
import {
  getLlmData,
  parseRankerDocs,
  parseRetrieveDocs,
  getSpanAttributes,
  getObservationType,
  getProviderLogo,
  getInput,
  getOutput,
  getModel,
  getPromptName,
  getPromptTemplateId,
} from "./getSpanData";
import GuardRail from "./RightSpans/GuardRail";
import PropTypes from "prop-types";

const DrawerRightRenderer = ({ observationSpan }) => {
  const [addToDatasetOpen, setAddToDatasetOpen] = useState(false);
  const [createNewDatasetOpen, setCreateNewDatasetOpen] = useState(false);

  const observationType = getObservationType(observationSpan);

  const handleCloseAddToDataset = () => {
    setAddToDatasetOpen(false);
  };

  const handleOpenCreateNewDataset = () => {
    setAddToDatasetOpen(false);
    setCreateNewDatasetOpen(true);
  };

  const handleCloseCreateNewDataset = () => {
    setCreateNewDatasetOpen(false);
  };

  const attributes = getSpanAttributes(observationSpan);
  const llmDataInput = getLlmData(observationSpan, "input");
  llmDataInput.invocationParams =
    attributes?.["llm.invocationParameters"] || null;

  const llmDataOutput = getLlmData(observationSpan, "output");

  const retrieveDocs = parseRetrieveDocs(observationSpan);

  const inputReRankerDocs = parseRankerDocs(observationSpan, "input");
  const outputRerankerDocs = parseRankerDocs(observationSpan, "output");

  const spanComponents = {
    chain: ChainSpan,
    llm: LLMSpan,
    retriever: RetreiveSpan,
    embedding: EmbeddingsSpan,
    reranker: RankerSpan,
    guardrail: GuardRail,
  };

  const getSpanProps = (observationType, observationSpan) => {
    switch (observationType) {
      case "chain":
        return [
          {
            column: { headerName: "Input", dataType: "text" },
            value: { cellValue: getInput(observationSpan) },
            allowCopy: true,
          },
          {
            column: { headerName: "Output", dataType: "text" },
            value: { cellValue: getOutput(observationSpan) },
            allowCopy: true,
            promptName: getPromptName(observationSpan),
            promptTemplateId: getPromptTemplateId(observationSpan),
          },
        ];
      case "guardrail": {
        const attrs = getSpanAttributes(observationSpan);
        return [
          {
            column: { headerName: "Input", dataType: "text" },
            value: {
              cellValue: attrs
                ? attrs?.["raw.input"]?.inputs
                  ? attrs?.["raw.input"]?.inputs
                  : attrs?.["raw.input"]?.output
                : null,
            },
            allowCopy: true,
            promptName: getPromptName(observationSpan),
            promptTemplateId: getPromptTemplateId(observationSpan),
          },
          {
            column: { headerName: "Configuration", dataType: "json" },
            value: {
              cellValue: attrs ? JSON.stringify(attrs["raw.input"]) : "",
            },
            allowCopy: true,
            guardConfig: true,
            promptName: getPromptName(observationSpan),
            promptTemplateId: getPromptTemplateId(observationSpan),
          },
          {
            column: { headerName: "Protect output", dataType: "text" },
            value: {
              cellValue: attrs
                ? attrs["raw.output"]?.messages
                  ? attrs["raw.output"]?.messages
                  : attrs["raw.output"]?.outputs?.guardedOutput
                : null,
            },
            allowCopy: true,
            rightRuleset: attrs ? attrs["guardrail.failedRule"] : null,
            promptName: getPromptName(observationSpan),
            promptTemplateId: getPromptTemplateId(observationSpan),
          },
        ];
      }
      case "llm":
        return [
          {
            column: { headerName: "Input", dataType: "text" },
            tabData: llmDataInput,
            model: getModel(observationSpan),
            modelLogo: getProviderLogo(observationSpan),
          },
          {
            column: { headerName: "Output", dataType: "text" },
            tabData: llmDataOutput,
            promptName: getPromptName(observationSpan),
            promptTemplateId: getPromptTemplateId(observationSpan),
          },
        ];
      case "retriever":
        return [
          {
            column: { headerName: "Input", dataType: "text" },
            value: { cellValue: getInput(observationSpan) },
            allowCopy: true,
          },
          {
            column: { headerName: "Documents", dataType: "text" },
            value: { cellValue: getInput(observationSpan) },
            allowCopy: true,
            showScore: true,
            retreiveDocs: retrieveDocs,
            promptName: getPromptName(observationSpan),
            promptTemplateId: getPromptTemplateId(observationSpan),
          },
        ];
      case "embedding": {
        const attrs = getSpanAttributes(observationSpan) || {};
        const embeddings = Object.entries(attrs).filter(
          ([key]) =>
            key.startsWith("embedding.embeddings.") &&
            key.endsWith(".embedding.text"),
        );

        return embeddings.map(([_, text]) => ({
          column: { headerName: `Embedding`, dataType: "text" },
          value: { cellValue: text },
          allowCopy: true,
          tabLabel: `Embedded Text`,
          promptName: getPromptName(observationSpan),
          promptTemplateId: getPromptTemplateId(observationSpan),
        }));
      }
      case "reranker":
        return [
          {
            column: { headerName: "Input", dataType: "text" },
            value: { cellValue: getInput(observationSpan) },
            allowCopy: true,
            showScore: true,
            RankerDocs: inputReRankerDocs,
          },
          {
            column: { headerName: "Output", dataType: "text" },
            value: { cellValue: getOutput(observationSpan) },
            allowCopy: true,
            showScore: true,
            RankerDocs: outputRerankerDocs,
            promptName: getPromptName(observationSpan),
            promptTemplateId: getPromptTemplateId(observationSpan),
          },
        ];
      default:
        return [
          {
            column: { headerName: "Input", dataType: "text" },
            value: { cellValue: getInput(observationSpan) },
            allowCopy: true,
          },
          {
            column: { headerName: "Output", dataType: "text" },
            value: { cellValue: getOutput(observationSpan) },
            allowCopy: true,
            promptName: getPromptName(observationSpan),
            promptTemplateId: getPromptTemplateId(observationSpan),
          },
        ];
    }
  };

  const SpanComponent =
    spanComponents[getObservationType(observationSpan)] ||
    spanComponents["chain"] ||
    (() => <span>Unknown Type</span>);
  const spanPropsArray = getSpanProps(observationType, observationSpan);

  return (
    <Box>
      <Box sx={{ marginTop: 2, display: "flex", gap: 1 }}>
        {/* <Button
          variant="outlined"
          color="primary"
          size="small"
          sx={{
            backgroundColor: "transparent",
            color: "primary.main",
            border: "2px solid var(--primary-main)",
            display: "flex",
            alignItems: "center",
            gap: 1,
            marginBottom: 2,
            ":hover": {
              border: "2px solid",
            },
          }}
          onClick={handleOpenAddToDataset}
        >
          <Iconify icon="solar:add-linear" color="primary.main" width={20} />
          Add to Dataset
        </Button> */}

        {/* Show only if observationType is 'llm' */}
        {/* {selectedNode?.observation_type === "llm" && (
          <Button
            variant="outlined"
            color="primary"
            size="small"
            sx={{
              backgroundColor: "transparent",
              color: "primary.main",
              border: "2px solid var(--primary-main)",
              display: "flex",
              alignItems: "center",
              gap: 1,
              marginBottom: 2,
              ":hover": {
                border: "2px solid",
              },
            }}
          >
            <Iconify
              icon="solar:chat-square-code-linear"
              color="primary.main"
              width={20}
            />
            Prompt Playground
          </Button>
        )} */}

        {/* {selectedNode?.observation_type === "embedding" && (
          <Button
            variant="outlined"
            color="primary"
            size="small"
            sx={{
              backgroundColor: "transparent",
              color: "primary.main",
              border: "2px solid var(--primary-main)",
              display: "flex",
              alignItems: "center",
              gap: 1,
              marginBottom: 2,
              ":hover": {
                border: "2px solid",
              },
            }}
          >
            <Iconify
              icon="solar:graph-linear"
              color="primary.main"
              width={20}
            />
            View Embedding
          </Button>
        )} */}
      </Box>

      <Box
        sx={{
          zIndex: 3,
          display: "flex",
          flexDirection: "column",
          gap: 3,
        }}
      >
        {spanPropsArray.map((props, index) => (
          <SpanComponent key={index} {...props} />
        ))}
      </Box>
      <AddToDataset
        open={addToDatasetOpen}
        onClose={handleCloseAddToDataset}
        onOpenCreateNewDataset={handleOpenCreateNewDataset}
      />
      <CreateNewDataset
        open={createNewDatasetOpen}
        onClose={handleCloseCreateNewDataset}
      />
    </Box>
  );
};

DrawerRightRenderer.propTypes = {
  observationSpan: PropTypes.object,
};

export default DrawerRightRenderer;
