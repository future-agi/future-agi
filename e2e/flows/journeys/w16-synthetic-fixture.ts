import type {
  BillingMode,
  Observations,
  Org,
  Role,
  SearchConfig,
  SearchState,
  W16SyntheticPort,
} from './w16-synthetic-contract';

export type Fault =
  | 'absent-dispatch'
  | 'incomplete-dispatch'
  | 'third-dispatch'
  | 'post-disable-dispatch'
  | 'missing-ledger'
  | 'wrong-request-id'
  | 'wrong-billing-mode'
  | 'config-version'
  | 'org-b-dispatch'
  | 'member-write'
  | 'viewer-write'
  | 'qualified-teardown';

const off = (): SearchState => ({
  saved: null,
  gateway: null,
  savedVersion: 0,
  gatewayVersion: 0,
  uiOff: true,
  disposition: 'OFF/NOT_QUALIFIED',
});

// Pure test fixture. It performs no HTTP, provider, database or money operation.
// Its fabricated records are synthetic inputs to contract tests, not receipts.
export class W16MockFixture implements W16SyntheticPort {
  readonly fixtureKind = 'mock-contract' as const;
  private states: Record<Org, SearchState> = { A: off(), B: off() };
  private observations: Observations = {
    calls: [],
    requests: [],
    ledger: [],
    admissionRefusals: 0,
  };
  private mode: BillingMode = 'byok';
  private minute = 0;
  private used = 0;
  private indexed = false;
  private wasEnabled = false;

  constructor(private readonly fault?: Fault) {}

  async reset(mode: BillingMode): Promise<void> {
    this.mode = mode;
    this.states = { A: off(), B: off() };
    this.observations = {
      calls: [],
      requests: [],
      ledger: [],
      admissionRefusals: 0,
    };
    this.minute = 0;
    this.used = 0;
    this.indexed = false;
    this.wasEnabled = false;
  }

  async setMinute(minute: number): Promise<void> {
    if (minute !== this.minute) this.used = 0;
    this.minute = minute;
  }

  async save(
    role: Role,
    org: Org,
    config: SearchConfig | null,
  ): Promise<number> {
    if (role === 'Member' || role === 'Viewer') {
      const broken =
        (role === 'Member' && this.fault === 'member-write') ||
        (role === 'Viewer' && this.fault === 'viewer-write');
      if (!broken) return 403;
    }
    const state = this.states[org];
    state.saved = structuredClone(config);
    state.gateway = structuredClone(config);
    state.savedVersion += 1;
    state.gatewayVersion = state.savedVersion;
    state.uiOff = !this.complete(config);
    this.indexed = false;
    if (this.complete(config)) this.wasEnabled = true;
    return 200;
  }

  async state(org: Org): Promise<SearchState> {
    const state = structuredClone(this.states[org]);
    if (
      org === 'A' &&
      this.fault === 'config-version' &&
      this.complete(state.saved)
    ) {
      state.gatewayVersion -= 1;
    }
    return state;
  }

  async discover(org: Org): Promise<void> {
    if (await this.dispatch(org)) this.indexed = true;
  }

  async search(org: Org): Promise<void> {
    if (this.indexed || !this.complete(this.states[org].saved) || org === 'B') {
      await this.dispatch(org);
    }
  }

  async observe(): Promise<Observations> {
    const out = structuredClone(this.observations);
    if (this.fault === 'missing-ledger') out.ledger = [];
    if (this.fault === 'wrong-request-id' && out.requests.length) {
      out.requests[0].gatewayRequestId = 'synthetic-caller-id-not-gateway-id';
    }
    if (this.fault === 'wrong-billing-mode' && out.requests.length) {
      out.requests[0].billingMode =
        this.mode === 'managed' ? 'byok' : 'managed';
    }
    return out;
  }

  async teardown(): Promise<void> {
    this.states = { A: off(), B: off() };
    this.indexed = false;
    if (this.fault === 'qualified-teardown')
      this.states.A.disposition = 'QUALIFIED';
  }

  private complete(config: SearchConfig | null): boolean {
    return (
      config?.enabled === true &&
      config.model === 'synthetic/embed' &&
      config.calls_per_minute === 2
    );
  }

  private async dispatch(org: Org): Promise<boolean> {
    const config = this.states[org].saved;
    const force =
      (org === 'A' &&
        config === null &&
        !this.wasEnabled &&
        this.fault === 'absent-dispatch') ||
      (org === 'A' &&
        config?.enabled === true &&
        !config.model &&
        this.fault === 'incomplete-dispatch') ||
      (org === 'A' &&
        config?.enabled === false &&
        this.wasEnabled &&
        this.fault === 'post-disable-dispatch') ||
      (org === 'B' && this.fault === 'org-b-dispatch');
    if (!force && !this.complete(config)) return false;
    if (!force && this.used >= 2 && this.fault !== 'third-dispatch') {
      this.observations.admissionRefusals += 1;
      return false;
    }
    this.used += 1;
    const gatewayRequestId = `synthetic-gateway-${this.observations.calls.length + 1}`;
    this.observations.calls.push({
      org,
      gatewayRequestId,
      model: 'synthetic/embed',
      method: 'POST',
      path: '/v1/embeddings',
      local: true,
    });
    this.observations.requests.push({
      org,
      gatewayRequestId,
      billingMode: this.mode,
    });
    if (this.mode === 'managed') {
      this.observations.ledger.push({
        org,
        gatewayRequestId,
        holdMicros: 10,
        settledMicros: 7,
      });
    }
    return true;
  }
}
