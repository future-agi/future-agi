package server

import (
	"context"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type orgLockMirrorProvider struct {
	calls    atomic.Int32
	requests chan models.ChatCompletionRequest
}

func (p *orgLockMirrorProvider) ChatCompletion(_ context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	p.calls.Add(1)
	p.requests <- *req
	return &models.ChatCompletionResponse{Model: req.Model}, nil
}

func (p *orgLockMirrorProvider) wait(t *testing.T) {
	t.Helper()
	select {
	case req := <-p.requests:
		if req.Model != "shadow-model" || req.Stream {
			t.Errorf("mirror model/stream = %q/%t, want shadow-model/false", req.Model, req.Stream)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("mirror provider was not called after lookup")
	}
}

func TestChatCompletion_OrgProviderLockMirrorAdmission(t *testing.T) {
	tests := []struct {
		name, source, mirrorTarget string
		policy                     *tenant.ProviderLockConfig
		global                     config.ProviderLockConfig
		wantMirror                 bool
	}{
		{
			name: "locked denied mirror header", source: "header", mirrorTarget: "anthropic",
			policy: &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"anthropic"}},
		},
		{
			name: "locked denied mirror metadata", source: "metadata", mirrorTarget: "anthropic",
			policy: &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"ANTH*"}},
		},
		{
			name: "locked mirror outside allow list", source: "header", mirrorTarget: "anthropic",
			policy: &tenant.ProviderLockConfig{Enabled: true, AllowedProviders: []string{"Open*"}},
		},
		{
			name: "lock rejects otherwise permitted mirror", source: "header", mirrorTarget: "anthropic",
			policy: &tenant.ProviderLockConfig{Enabled: true},
		},
		{
			name: "unlocked denied mirror", source: "none", mirrorTarget: "anthropic",
			policy: &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"ANTH*"}},
		},
		{
			name: "unlocked mirror outside allow list", source: "none", mirrorTarget: "anthropic",
			policy: &tenant.ProviderLockConfig{Enabled: true, AllowedProviders: []string{"open*"}},
		},
		{
			name: "mirror to validated lock target", source: "header", mirrorTarget: "openai", wantMirror: true,
			policy: &tenant.ProviderLockConfig{Enabled: true, AllowedProviders: []string{"OPEN*"}},
		},
		{
			name: "unlocked allowed mirror", source: "none", mirrorTarget: "anthropic", wantMirror: true,
			policy: &tenant.ProviderLockConfig{Enabled: true, AllowedProviders: []string{"open*", "ANTH*"}},
		},
		{
			name: "absent policy keeps mirroring", source: "header", mirrorTarget: "anthropic", wantMirror: true,
		},
		{
			name: "disabled policy keeps mirroring", source: "header", mirrorTarget: "anthropic", wantMirror: true,
			policy: &tenant.ProviderLockConfig{Enabled: false, DenyProviders: []string{"anthropic"}},
			global: config.ProviderLockConfig{Enabled: true, DenyProviders: []string{"anthropic"}},
		},
		{
			name: "inherited policy keeps mirroring", source: "header", mirrorTarget: "anthropic", wantMirror: true,
			global: config.ProviderLockConfig{Enabled: true, DenyProviders: []string{"anthropic"}},
		},
	}
	for _, scope := range []string{"direct", "gateway", "org"} {
		for _, tt := range tests {
			t.Run(scope+"/"+tt.name, func(t *testing.T) {
				opts := orgLockRepairOptions{policy: tt.policy, global: tt.global, failoverScope: scope}
				if !tt.wantMirror {
					// Failover must skip the denied/non-lock first hop and succeed
					// through openai before the same denied target is mirrored.
					opts.targets = []string{"anthropic", "openai"}
				}
				srv, calls := newOrgLockRepairServer(t, opts)
				mirrorProvider := &orgLockMirrorProvider{requests: make(chan models.ChatCompletionRequest, 1)}
				var lookups atomic.Int32
				mirror := routing.NewMirror(config.MirrorConfig{Enabled: true, Rules: []config.MirrorRule{{
					SourceModel: "gpt-4o", TargetProvider: tt.mirrorTarget, TargetModel: "shadow-model", SampleRate: 1,
				}}}, func(id string) (routing.MirrorProvider, bool) {
					lookups.Add(1)
					if id != tt.mirrorTarget {
						t.Errorf("mirror lookup = %q, want %q", id, tt.mirrorTarget)
					}
					return mirrorProvider, true
				})
				srv.handlers.mirror.Store(mirror)
				target := "openai"
				if tt.source == "none" {
					target = ""
				}
				w := postOrgProviderLock(t, srv, "sk-agentcc-lock-a", tt.source, target, false)
				if w.Code != http.StatusOK {
					t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
				}
				for id, counter := range calls {
					want := int32(0)
					if id == "openai" {
						want = 1
					}
					if got := counter.Load(); got != want {
						t.Errorf("primary %s calls = %d, want %d", id, got, want)
					}
				}
				// Lookup is synchronous and precedes the goroutine. A zero lookup
				// assertion proves no dispatch is pending without timing a sleep.
				if lookups.Load() != 0 {
					mirrorProvider.wait(t)
				}
				want := int32(0)
				if tt.wantMirror {
					want = 1
				}
				if got := lookups.Load(); got != want {
					t.Errorf("mirror lookups = %d, want %d", got, want)
				}
				if got := mirrorProvider.calls.Load(); got != want {
					t.Errorf("mirror calls = %d, want %d", got, want)
				}
				if srv.handlers.mirror.Load() != mirror {
					t.Error("request replaced the shared mirror")
				}
			})
		}
	}
}

func TestChatCompletion_OrgProviderLockMirrorDoesNotChangeOtherOrg(t *testing.T) {
	srv, _ := newOrgProviderLockServer(t, config.ProviderLockConfig{}, &tenant.RoutingConfig{
		ProviderLock: &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"anthropic"}},
	})
	mirrorProvider := &orgLockMirrorProvider{requests: make(chan models.ChatCompletionRequest, 2)}
	var lookups atomic.Int32
	mirror := routing.NewMirror(config.MirrorConfig{Enabled: true, Rules: []config.MirrorRule{{
		SourceModel: "*", TargetProvider: "anthropic", TargetModel: "shadow-model", SampleRate: 1,
	}}}, func(string) (routing.MirrorProvider, bool) {
		lookups.Add(1)
		return mirrorProvider, true
	})
	srv.handlers.mirror.Store(mirror)
	for _, org := range []string{"a", "b"} {
		before := lookups.Load()
		w := postOrgProviderLock(t, srv, "sk-agentcc-lock-"+org, "header", "openai", false)
		if w.Code != http.StatusOK {
			t.Fatalf("org %s status = %d, want 200: %s", org, w.Code, w.Body.String())
		}
		if lookups.Load() > before {
			mirrorProvider.wait(t)
		}
		want := int32(0)
		if org == "b" {
			want = 1
		}
		if got := lookups.Load() - before; got != want {
			t.Errorf("org %s mirror lookups = %d, want %d", org, got, want)
		}
	}
	if got := mirrorProvider.calls.Load(); got != 1 {
		t.Errorf("mirror calls = %d, want 1 for org b only", got)
	}
	if srv.handlers.mirror.Load() != mirror {
		t.Error("request replaced the shared mirror")
	}
}
