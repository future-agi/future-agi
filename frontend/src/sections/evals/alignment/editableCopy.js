// W7-6 B2/B3 (W7-SCOPE §2.6): the editable copy of a reference-kind
// built-in. Improve judge and the tuned-judge check work on it (a built-in
// rule is never versioned). Pure; the mock finds the copy by the same name.

// A criteria eval whose clause the scorer reads as reference coverage
// (evalRules.criteriaOf).
export const COPY_INSTRUCTIONS = "Pass only if the reply covers {{expected}}.";

// create-v2 names take lowercase letters, numbers, underscores and hyphens.
export const copyNameOf = (name) => `${name}_aligned`;
