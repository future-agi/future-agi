import { createContext, useContext } from "react";
import { getRandomId } from "src/utils/utils";

/**
 * @typedef {Object} PromptResponse
 * @property {string} id
 * @property {string} templateVersion
 * @property {string[]} output
 * @property {Object} promptConfigSnapshot
 * @property {Object[]} promptConfigSnapshot.messages
 * @property {string} promptConfigSnapshot.messages[].id
 * @property {"user" | "system" | "assistant"} promptConfigSnapshot.messages[].role
 * @property {Object[]} promptConfigSnapshot.messages[].content
 * @property {string} [promptConfigSnapshot.messages[].content[].text] - Optional
 * @property {{url: string}} [promptConfigSnapshot.messages[].content[].image_url] - Optional
 * @property {"text" | "image_url"} promptConfigSnapshot.messages[].content[].type
 * @property {Object} promptConfigSnapshot.configuration
 * @property {string} promptConfigSnapshot.configuration.model
 * @property {number} promptConfigSnapshot.configuration.topP
 * @property {number} promptConfigSnapshot.configuration.maxTokens
 * @property {number} promptConfigSnapshot.configuration.temperature
 * @property {number} promptConfigSnapshot.configuration.presencePenalty
 * @property {number} promptConfigSnapshot.configuration.frequencyPenalty
 * @property {string} templateName
 * @property {string} originalTemplate
 * @property {Object} metadata
 * @property {Object} metadata.cost
 * @property {number} metadata.cost.totalCost
 * @property {number} metadata.cost.promptCost
 * @property {number} metadata.cost.completionCost
 * @property {Object} metadata.usage
 * @property {number} metadata.usage.totalTokens
 * @property {number} metadata.usage.promptTokens
 * @property {number} metadata.usage.completionTokens
 * @property {number} metadata.responseTime
 * @property {Object.<string, any[]>} variableNames
 * @property {any[]} evaluationResults
 * @property {any[]} evaluationConfigs
 * @property {string} createdAt
 */

/**
 * @callback SetResponseState
 * @param {PromptResponse[] | ((prevState: PromptResponse[]) => PromptResponse[])} state
 */

/** @type {React.Context<{responseState: PromptResponse[] | null, setResponseState: SetResponseState}>} */
export const PromptContext = createContext({
  responseState: [
    {
      id: "",
      templateVersion: "",
      templateName: "",
      output: [],
      promptConfigSnapshot: {
        messages: [
          {
            id: getRandomId(),
            role: "user", // Default role
            content: [
              {
                text: "", // Default to text, or use imageUrl instead

                // imageUrl: { url: "" }, // Uncomment this line if using imageUrl instead of text
                type: "text", // Default to "text" or "image_url" as needed
              },
            ],
          },
        ],
        configuration: {
          model: "",
          topP: 0,
          maxTokens: 0,
          temperature: 0,
          presencePenalty: 0,
          frequencyPenalty: 0,
        },
      },
      originalTemplate: "",
      metadata: {
        cost: {
          totalCost: 0,
          promptCost: 0,
          completionCost: 0,
        },
        usage: {
          totalTokens: 0,
          promptTokens: 0,
          completionTokens: 0,
        },
        responseTime: 0,
      },
      variableNames: {},
      evaluationResults: [],
      evaluationConfigs: [],
      createdAt: "",
    },
  ],
  setResponseState: (_state) => {},
});

export const usePromptContext = () => {
  return useContext(PromptContext);
};
