export const serializeTraceTags = (traceTags) =>
  traceTags.map((tag) => (typeof tag === "string" ? tag : tag.name));
