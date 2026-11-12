export function sha256(value: string): string;
export function orderId(attemptId: string, caseId: string, variant: string, repeat: number): string;
export function businessLedger(container: string): any[];
export function runOperations(container: string, attemptId: string, cells: any[]): any[];
export function firstTurns(ledger: any[]): { system: string; request: string; catalogMembers: number; toolCalls: number }[];
