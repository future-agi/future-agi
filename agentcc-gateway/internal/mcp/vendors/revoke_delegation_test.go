package vendors

import (
	"context"
	"errors"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

type revisionDelegate struct {
	calls       int
	org, server string
	through     int64
	err         error
}

func (*revisionDelegate) Resolve(*mcp.CallContext) (*mcp.AuthOverride, *mcp.Denial) { return nil, nil }
func (r *revisionDelegate) RevokePushedGrants(_ context.Context, org, server string, through int64) error {
	r.calls++
	r.org, r.server, r.through = org, server, through
	return r.err
}
func TestK2UnavailableRuntimeDelegatesRevision(t *testing.T) {
	base := &revisionDelegate{}
	wrapper := NewUnavailableRuntime(base)
	revoker, ok := any(wrapper).(mcp.PushedGrantRevoker)
	if !ok {
		t.Fatal("wrapper dropped RevokePushedGrants")
	}
	if err := revoker.RevokePushedGrants(t.Context(), "org-a", "ordinary", 7); err != nil {
		t.Fatal(err)
	}
	if base.calls != 1 || base.org != "org-a" || base.server != "ordinary" || base.through != 7 {
		t.Fatal("revocation arguments were not preserved")
	}
	base.err = errors.New("synthetic revocation unavailable")
	if err := revoker.RevokePushedGrants(t.Context(), "org-a", "ordinary", 8); !errors.Is(err, base.err) {
		t.Fatal("revocation failure swallowed")
	}
	unsupported := NewUnavailableRuntime(nil)
	if r, ok := any(unsupported).(mcp.PushedGrantRevoker); ok && r.RevokePushedGrants(t.Context(), "org-a", "ordinary", 9) == nil {
		t.Fatal("unsupported revision silently accepted")
	}
}
