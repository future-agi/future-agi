package tenancy

import (
	"fmt"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// A just-refreshed shared grant gets its own connection. Wait only for the
// entry's existing connector, bounded like initial construction, rather than
// dispatching through a stale handle or launching duplicate factories.
func (e *entry) sharedClient() (mcp.UpstreamClient, error) {
	timer := time.NewTimer(e.pool.m.opts.InitialWait)
	defer timer.Stop()
	for {
		e.mu.Lock()
		c, retired, changed := e.client, e.retired, e.connectionChanged
		healthy := c != nil && c.Healthy()
		e.mu.Unlock()
		if !retired && healthy {
			return c, nil
		}
		if retired || !e.spec().sharedOAuth {
			break
		}
		select {
		case <-changed:
			continue
		case <-e.ctx.Done():
		case <-timer.C:
		}
		break
	}
	return nil, fmt.Errorf("mcp server %q: %w", e.id, errNotConnected)
}
