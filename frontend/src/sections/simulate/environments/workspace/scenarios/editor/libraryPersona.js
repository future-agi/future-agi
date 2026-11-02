import { PERSONA_FIELDS } from "../scenarioEditor.constants";

const asList = (value) => {
  if (value == null || value === "") return [];
  return (Array.isArray(value) ? value : [value]).filter(Boolean).map(String);
};

// Where each writable persona field sits on a library persona (normalised by
// personaApiShape: most traits arrive as lists). A scenario keeps one value per
// field, so a list gives its first; languages keep the whole list.
const FROM_LIBRARY = {
  accent: (p) => p.accent,
  communicationStyle: (p) => p.communicationStyle,
  personality: (p) => p.personality,
  occupation: (p) => p.profession ?? p.occupation,
  location: (p) => p.location,
  languages: (p) => p.languages ?? p.language,
};

// The draft persona values a library pick fills: only the fields the server
// lets this scenario write, and only those the library persona has a value
// for. Name, age group and gender aren't writable, so a pick never touches
// them. Returns the patch and the labels it filled.
export function libraryPersonaPatch(libraryPersona, canEditPersona) {
  const patch = {};
  const filled = [];
  if (!libraryPersona) return { patch, filled };
  PERSONA_FIELDS.forEach(({ key, server, label, list }) => {
    if (!canEditPersona(server)) return;
    const values = asList(FROM_LIBRARY[key](libraryPersona));
    if (!values.length) return;
    patch[key] = list ? values.join(", ") : values[0];
    filled.push(label.toLowerCase());
  });
  return { patch, filled };
}
