package server

import (
	"context"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/keyscope"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// keyScopeFlushInterval is how often a scoped key's refusals are sent to the
// control plane's audit log.
const keyScopeFlushInterval = 10 * time.Second

// keyScopeState is the server's part of scoped keys (CN22c, keyscope): the
// MCP endpoint the key gate admits for the mcp scope, and the reporter that
// sends every refusal (the key gate's 403s here, the keyscope plugin's 402s
// and 403s in the pipeline) to the control plane's audit log.
type keyScopeState struct {
	mcpRoot   string
	mcpSignIn bool               // mcp.oauth.enabled: the endpoint signs in its own callers
	reporter  *keyscope.Reporter // nil without a control plane
	cancel    context.CancelFunc
	done      chan struct{}
}

func newKeyScopeState(cfg *config.Config) *keyScopeState {
	st := &keyScopeState{reporter: keyscope.NewReporter(cfg.ControlPlane.URL, cfg.ControlPlane.AdminToken)}
	if cfg.MCP.Enabled {
		st.mcpRoot = mcpEndpoint(cfg)
		st.mcpSignIn = cfg.MCP.ClientOAuthEnabled && cfg.MCP.OAuth.Enabled
	}
	return st
}

// gate is the key gate's view of scoped keys.
func (st *keyScopeState) gate() middleware.KeyScopes {
	return middleware.KeyScopes{MCPRoot: st.mcpRoot, Refusals: st.reporter, MCPSignIn: st.mcpSignIn}
}

// KeyScopeReporter is the reporter the keyscope pipeline plugin shares with
// the key gate, nil without a control plane.
func (s *Server) KeyScopeReporter() *keyscope.Reporter {
	if s.keyScopes == nil {
		return nil
	}
	return s.keyScopes.reporter
}

// start runs the reporter until stop.
func (st *keyScopeState) start() {
	if st == nil || st.reporter == nil || st.cancel != nil {
		return
	}
	ctx, cancel := context.WithCancel(context.Background())
	st.cancel, st.done = cancel, make(chan struct{})
	go func() {
		defer close(st.done)
		st.reporter.Run(ctx, keyScopeFlushInterval)
	}()
}

// stop ends the reporter after a last flush, waiting at most until ctx ends.
func (st *keyScopeState) stop(ctx context.Context) {
	if st == nil || st.cancel == nil {
		return
	}
	st.cancel()
	select {
	case <-st.done:
	case <-ctx.Done():
	}
}

// keyBudgeted reports whether the key that authenticated rc carries a spend
// budget (keyscope.ParseBudget: a budget it cannot read counts too, as it
// fails closed). A budgeted key's call must be priced however its stream
// ends: when the upstream reports no usage, on an estimate, as a managed
// call is (CN22c, R-CN22c-1 and R-CN22c-3).
func (h *Handlers) keyBudgeted(rc *models.RequestContext) bool {
	if h.keyStore == nil {
		return false
	}
	id := rc.Metadata["auth_key_id"]
	if id == "" {
		return false
	}
	key := h.keyStore.Get(id)
	if key == nil {
		return false
	}
	_, ok := keyscope.ParseBudget(h.keyStore.MetadataOf(key))
	return ok
}
