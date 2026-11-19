// Typed interfaces for the existing ESM fixtures consumed by the J6 flow.
declare module '*skills-r3/library.mjs' {
  export function libraryFixture(options?: { unsupportedMember?: boolean }): {
    schema: number;
    fixture_sha256: string;
    skills: { id: string; name: string; files: { path: string; content: string }[]; unsupported: object[] }[];
  };
}
declare module '*skills-r3/programmable-provider.mjs' {
  type Step = { kind: 'json'; body: unknown; status?: number };
  type Boundary = { state: string; request_sha256: string };
  export class ProgrammableProvider {
    listen(): Promise<this>;
    register(steps: Step[]): { capability: string; baseURL(): string };
    ledger(capability: string): Boundary[];
    remaining(capability: string): number;
    close(): Promise<void>;
  }
  export function completion(message: { content: string | null; tool_calls?: object[] }, options?: { finishReason?: string }): object;
}
declare module '*skills-r3/j6-guest.mjs' {
  export function startGuest(options: { runId: string; wheelsDir: string; fixtureDir: string }): Promise<any>;
  export function attachGuest(options: unknown): any;
}
declare module '*skills-r3/j6-scenario.mjs' {
  export function actorOf(actor: unknown): any;
  export function prepareJ6Scenario(options: Record<string, unknown>): Promise<any>;
}
declare module '*skills-r3/j6-registration.mjs' {
  export function buildJ6Registration(options: Record<string, unknown>): Promise<{ registration: any; provenance: any }>;
}
