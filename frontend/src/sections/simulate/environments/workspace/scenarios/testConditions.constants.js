export const TEST_CONDITIONS_COPY = {
  heading: "Hidden · test conditions",
  empty: "Tools behave normally",
  add: "Add a test condition",
  tool: "Tool",
  whatHappens: "What happens",
  // Each field a mode can take, by the name the server lists it under.
  fields: {
    on_call: "On which call",
    delay_seconds: "Delay in seconds",
    status: "Status",
    message: "Message",
    approval: "Approval it needs",
    approved_when: "Approved when (optional)",
  },
  conditionsHelp: 'Field values the approval holds, such as {"status": "approved"}',
  remove: "Remove",
  inProcess: "This tool runs inside the agent, so it cannot be held",
};

// How a field the server lists is typed in: numbers, key/value conditions as a JSON object, anything else as text.
export const NUMBER_FIELDS = ["on_call", "delay_seconds", "status"];
export const OBJECT_FIELDS = ["approved_when"];

// Why typed conditions are not yet a JSON object, or "".
export const objectProblem = (text) => {
  if (!String(text).trim()) return "";
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? ""
      : TEST_CONDITIONS_COPY.conditionsHelp;
  } catch {
    return TEST_CONDITIONS_COPY.conditionsHelp;
  }
};

// A field's value as typed: an emptied number or condition is null; read only once `objectProblem` is "".
export const asTyped = (key, typed) => {
  const empty = String(typed).trim() === "";
  if (NUMBER_FIELDS.includes(key)) return empty ? null : Number(typed);
  if (OBJECT_FIELDS.includes(key)) return empty ? null : JSON.parse(typed);
  return typed;
};

// The route a fault holds, when it reads differently from the tool's name; none when it names no tool.
export const testConditionRoute = (fault) =>
  fault.tool && fault.tool !== fault.action ? fault.action : "";

// What the editor holds for a fault the scenario row resolved: its display fields kept, and the values its mode does
// not read (null, or an empty message) left out, so it goes back as it was stored.
export const storedFault = (fault) =>
  Object.fromEntries(
    Object.entries(fault).filter(([, value]) => value !== null && value !== ""),
  );

// A fault as the scenario file keeps it: the display fields dropped, every value as typed.
export const fileFault = ({ tool, mode_label: modeLabel, line, ...stored }) =>
  stored;
