// The SDK runner, `futureagi run <file>:<attr> --run <uuid>`, ships in no
// released package yet (Python SDK 0.7.0, CLI v0.1.0): a user who copied
// that line would get "command not found". While this is false no screen
// prints it; each place that printed it says "Not available in this
// release" instead (waitingModel.js). Turn it on in the release whose
// package provides the command.
export const SDK_RUNNER_RELEASED = false;
