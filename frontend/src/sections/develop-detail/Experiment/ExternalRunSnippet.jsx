/**
 * Legacy props remain accepted by this inert compatibility slot; none are read.
 * @typedef {object} ExternalRunSnippetProps
 * @property {string[]} [exampleIds]
 * @property {string} [lang]
 * @property {string} [inputColumn]
 * @property {string} [experimentId]
 * @property {string} [runId]
 * @property {string} [runLabel]
 * @property {string} [exampleId]
 * @property {number} [trials]
 * @property {() => void | Promise<void>} [onSimulateUpload]
 * @property {boolean} [simulating]
 * @property {boolean} [dense]
 */

// Kept for callers that still mount this slot; no upload action is available.
/** @type {(props: ExternalRunSnippetProps) => null} */
const ExternalRunSnippet = () => null;

export default ExternalRunSnippet;
