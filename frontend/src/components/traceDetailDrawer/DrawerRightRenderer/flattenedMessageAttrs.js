// Flattened OTel/OpenInference message-attribute prefixes, by message type.
export const messageAttrPrefixes = (type) => [
  `llm.${type}Messages`,
  `llm.${type}_messages`,
  `gen_ai.${type}.messages`,
];

// Shared recognition + index-grouping for flattened message attrs. Returns each
// message's raw { property, value } entries in index order; callers format them
// themselves (drawer builds a display array, workbench a flattened string).
export function groupFlattenedMessageAttrs(attrs, type = "input") {
  const prefixes = messageAttrPrefixes(type);
  const byIndex = {};

  Object.keys(attrs || {}).forEach((key) => {
    const prefix = prefixes.find((p) => key.startsWith(`${p}.`));
    if (!prefix) return;
    const rest = key.slice(prefix.length + 1);
    const firstDot = rest.indexOf(".");
    if (firstDot === -1) return;
    const index = rest.slice(0, firstDot);
    const property = rest.slice(firstDot + 1);
    if (!byIndex[index]) byIndex[index] = [];
    byIndex[index].push({ property, value: attrs[key] });
  });

  return Object.keys(byIndex)
    .sort((a, b) => Number(a) - Number(b))
    .map((index) => ({ index, entries: byIndex[index] }));
}
