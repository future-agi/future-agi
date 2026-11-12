/** HR-JRN-01: the vocabulary of a value-free refusal record (value-free-vocabulary.mjs). */
export const REASON_CODES: readonly string[];
export const VALUE_KINDS: readonly string[];
export const REGISTRATION_KINDS: readonly string[];
export const AREAS: readonly string[];
export const PLACES: readonly string[];
export function isCleanReason(r: unknown): boolean;
export function cleanReason(r: unknown): { reason: string } & Record<string, string | number>;
