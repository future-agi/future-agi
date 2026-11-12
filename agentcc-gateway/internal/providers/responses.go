package providers

import "context"

// ResponsesBindingProvider identifies the actual upstream namespace used for a
// Responses call. The opaque binding covers provider, endpoint and effective
// credentials (including account/project headers), not caller-supplied metadata.
// Providers without this contract may create responses, but their IDs cannot
// authorize continuations. The binding is private to the gateway's store.
type ResponsesBindingProvider interface {
	ResponsesBinding(context.Context) string
}
