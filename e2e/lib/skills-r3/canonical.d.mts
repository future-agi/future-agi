export function canonical(value: unknown): string;
export function digest(value: unknown): string;
export function sha256(bytes: string | Uint8Array): string;
export function requireValue(condition: unknown, message: string): asserts condition;
export function exactSet(actual: readonly unknown[], expected: readonly unknown[], label?: string): void;
export const SHA256: RegExp;
export const COMMIT: RegExp;
export function token(value: unknown, field: string): string;
