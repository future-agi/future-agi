// The structured disclosure (SPEC §6.7) and the approve rule of §5.4: every
// part the checker observed a fact for must be non-empty and not "None.".
// Mirrors omega_hub/services/disclosure.py so the form agrees with the server.

export const DISCLOSURE_PARTS = [
  { key: "network", label: "Network" },
  { key: "files", label: "Files" },
  { key: "shell", label: "Shell" },
  { key: "background", label: "Keeps running" },
  { key: "credentials", label: "Credentials" },
];

export const DISCLOSURE_FIELDS = ["summary", ...DISCLOSURE_PARTS.map((p) => p.key)];

export const OBSERVED_FOR_PART = {
  network: ["hosts", "vendors", "rawNetwork"],
  files: ["filesOutside", "writesCode"],
  shell: ["shell"],
  background: ["keepsRunning", "workers"],
  credentials: ["keys", "envEnumeration"],
};

export const OBSERVED_LABEL = {
  hosts: "hosts contacted",
  vendors: "model vendors",
  rawNetwork: "raw network",
  filesOutside: "files outside the entry",
  writesCode: "writes code files",
  shell: "shell",
  keepsRunning: "keeps running",
  workers: "workers",
  keys: "keys",
  envEnumeration: "environment enumeration",
};

export const SUMMARY_LENGTH = [20, 200];
export const PART_LENGTH = [4, 300];
export const DETAIL_LENGTH = [4, 1200];
export const DISCLOSURE_LENGTHS = {
  summary: SUMMARY_LENGTH,
  network: DETAIL_LENGTH,
  files: DETAIL_LENGTH,
  credentials: DETAIL_LENGTH,
  shell: PART_LENGTH,
  background: PART_LENGTH,
};

const EMPTY = /^\s*(none\.?)?\s*$/i;

export function isEmptyPart(value) {
  return typeof value !== "string" || EMPTY.test(value);
}

export function observedFacts(observed, part) {
  return (OBSERVED_FOR_PART[part] || []).flatMap((key) =>
    (observed?.[key] || []).map((fact) => ({ key, fact: String(fact) })),
  );
}

export function disclosureGaps(disclosure, observed) {
  return DISCLOSURE_PARTS.map((p) => p.key).filter(
    (part) => observedFacts(observed, part).length > 0 && isEmptyPart(disclosure?.[part]),
  );
}

export function disclosureShapeErrors(disclosure) {
  const errors = {};
  for (const field of DISCLOSURE_FIELDS) {
    const [low, high] = DISCLOSURE_LENGTHS[field];
    const value = (disclosure?.[field] || "").trim();
    if (value.length < low || value.length > high) {
      errors[field] = `${low}–${high} characters`;
    }
  }
  return errors;
}

export function emptyDisclosure() {
  return Object.fromEntries(DISCLOSURE_FIELDS.map((field) => [field, ""]));
}
