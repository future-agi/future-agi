// Persona cells can hold a dict written as text ("{'name': 'Ana'}", or
// "{'name': None}" from Python); read it as an object, like PersonaComponent
// does, so an empty one counts as empty. Anything else is returned as-is.
export const parsePersona = (value) => {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value.replace(/'/g, '"').replace(/\bNone\b/g, "null"));
  } catch {
    return value;
  }
};

export const isEmptyPersona = (value) => {
  if (value == null || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.values(value).every(isEmptyPersona);
  return false;
};
