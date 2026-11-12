package tenancy

import (
	"errors"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// errBuiltinUnregistered: a built-in id is registered at startup or never, so
// its entry stops retrying. Any other failure (a refused or unreachable
// credential authority) retries like a remote server's connect.
var errBuiltinUnregistered = errors.New("not registered")

// builtinClient gives each org entry its own lifecycle handle while keeping
// the process-wide built-in implementation alive. A built-in is registered
// once in Deps.Builtins and may be used by many org pools; retiring one pool
// must never close the shared implementation for the other pools.
type builtinClient struct {
	mcp.UpstreamClient
}

// Close is intentionally a no-op. The registry owns the built-in's lifetime;
// pool entries only own this per-entry adapter.
func (*builtinClient) Close() error { return nil }
