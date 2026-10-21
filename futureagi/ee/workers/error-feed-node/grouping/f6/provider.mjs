// The durable gateway/accounting adapter is injected by the coordinator.
export class Paused extends Error {
  constructor(reason) {
    super(reason);
    this.name = 'Paused';
  }
}
