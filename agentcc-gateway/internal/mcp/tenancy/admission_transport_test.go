package tenancy

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func r6ComingSoon(t *testing.T, err error) {
	t.Helper()
	var d *mcp.Denial
	if !errors.As(err, &d) {
		t.Errorf("expected typed Coming soon refusal, got %v", err)
		return
	}
	meta, ok := d.Metadata.(map[string]any)
	if !ok || meta["state"] != "coming_soon" {
		t.Errorf("wrong refusal metadata: %+v", d)
	}
}
func r6Wait(t *testing.T, ch <-chan struct{}, what string) bool {
	t.Helper()
	select {
	case <-ch:
		return true
	case <-time.After(2500 * time.Millisecond):
		t.Errorf("no %s", what)
		return false
	}
}

// No outer pool operation runs between the flip and the autonomous mediation
// assertion. The real factory owns the health, TTL, stream and reconnect loops.
func TestK2R6RealTransportMediation(t *testing.T) {
	for _, transport := range []string{"http", "sse"} {
		for _, stage := range []string{"before-discovery", "after-tools", "mid-stream", "before-health", "before-refresh", "before-reconnect"} {
			for _, mutation := range []string{"catalog", "ref", "url"} {
				t.Run(transport+"/"+stage+"/"+mutation, func(t *testing.T) {
					f := newR6Fixture(t, "google-docs", "1.0.2", transport, stage, mutation)
					c, err := f.pool.Client("service", "person", f.auth)
					if stage == "before-discovery" || stage == "after-tools" {
						r6ComingSoon(t, err)
					} else {
						if err != nil {
							t.Fatalf("positive connection: %v", err)
						}
						if _, err := c.CallTool(context.Background(), "echo", nil, mcp.CallOptions{}); err != nil {
							t.Fatalf("positive dispatch: %v", err)
						}
						if f.wire.calls.Load() != 1 {
							t.Fatal("positive control did not send")
						}
						switch stage {
						case "mid-stream":
							if r6Wait(t, f.wire.streamOpened, "stream open") {
								close(f.wire.releaseStream)
							}
						case "before-health", "before-refresh":
							f.wire.flip()
						}
					}
					if !r6Wait(t, f.wire.flipped, "flip boundary") {
						return
					}
					// Catalog-only changes have no store callback. This channel must be
					// closed by a real send's admission, NOT by the assertions below.
					if r6Wait(t, f.policy.denied, "autonomous current-state admission refusal") {
						// The policy returns before its caller retires the entry.
						eventually(t, "transport-triggered retirement", f.entry.isRetired)
					}
					if n := f.wire.after.Load(); n != 0 {
						t.Errorf("%d authenticated requests after refusal", n)
					}
					if n := f.wire.authenticated.Load(); n == 0 {
						t.Error("test never exercised authenticated transport")
					}
					if stage == "after-tools" {
						if f.wire.lists.Load() != 1 {
							t.Error("tools/list boundary not reached exactly once")
						}
						for _, method := range []string{"resources/list", "prompts/list"} {
							if _, sent := f.wire.methods.Load(method); sent {
								t.Errorf("sent %s after tools/list flipped admission", method)
							}
						}
					}
					_, err = f.pool.Client("service", "person", f.auth)
					r6ComingSoon(t, err)
					if c != nil {
						_, err = c.CallTool(context.Background(), "echo", nil, mcp.CallOptions{})
						r6ComingSoon(t, err)
					}
					if n := f.wire.after.Load(); n != 0 {
						t.Errorf("retained handle leaked %d authenticated requests", n)
					}
				})
			}
		}
	}
}

func TestK2R6AllEnabledRealTransport(t *testing.T) {
	if len(r6Records) != 12 {
		t.Fatal("positive census changed")
	}
	for _, rec := range r6Records {
		for _, transport := range []string{"http", "sse"} {
			t.Run(rec.app+"/"+transport, func(t *testing.T) {
				f := newR6Fixture(t, rec.app, rec.version, transport, "positive", "catalog")
				c, err := f.pool.Client("service", "person", f.auth)
				if err != nil {
					t.Fatal(err)
				}
				if _, err = c.CallTool(context.Background(), "echo", nil, mcp.CallOptions{}); err != nil {
					t.Fatal(err)
				}
				for _, method := range []string{"initialize", "tools/list", "resources/list", "prompts/list", "tools/call"} {
					if _, ok := f.wire.methods.Load(method); !ok {
						t.Errorf("positive never sent %s", method)
					}
				}
				if f.wire.calls.Load() != 1 || f.entry.isRetired() {
					t.Fatal("enabled record cannot dispatch")
				}
			})
		}
	}
}
