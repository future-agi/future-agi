import React from "react";
import PropTypes from "prop-types";
import {
  PromptNodeForm,
  EvalsNodeForm,
  AgentNodeForm,
  HttpRequestNodeForm,
  ConditionalNodeForm,
} from "./forms";
import { NODE_TYPES } from "../../utils/constants";

const FORM_COMPONENTS = {
  [NODE_TYPES.LLM_PROMPT]: PromptNodeForm,
  eval: EvalsNodeForm,
  [NODE_TYPES.AGENT]: AgentNodeForm,
  [NODE_TYPES.HTTP_REQUEST]: HttpRequestNodeForm,
  [NODE_TYPES.CONDITIONAL]: ConditionalNodeForm,
};

export default function NodeConfigurationForm({ nodeType, nodeId }) {
  const FormComponent = FORM_COMPONENTS[nodeType];

  if (!FormComponent) {
    return null;
  }

  return <FormComponent nodeId={nodeId} />;
}

NodeConfigurationForm.propTypes = {
  nodeType: PropTypes.oneOf([
    NODE_TYPES.LLM_PROMPT,
    "eval",
    NODE_TYPES.AGENT,
    NODE_TYPES.HTTP_REQUEST,
    NODE_TYPES.CONDITIONAL,
  ]).isRequired,
  nodeId: PropTypes.string.isRequired,
};
