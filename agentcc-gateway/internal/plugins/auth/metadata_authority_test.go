package auth

import (
	"context"
	"log/slog"
	"net/http"
	"sync"
	"testing"
	"time"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/plugins/logging"
)

// The reserved names: the authority entries this plugin writes from the key,
// and the rest, which it drops. TestPlugin_ReservedNameListsMatchTheKeyStore
// keeps both equal to the key store's lists.
var (
	authorityNames     = []string{"access_groups", "allow_policy_override", "org_id", "role", "team"}
	reservedLabelNames = []string{"id", "key_type", "name", "owner", "type"}
)

// authenticate runs this plugin for a single config key and returns the
// request it filled in. The caller releases it.
func authenticate(t *testing.T, key config.AuthKeyConfig) (*authpkg.KeyStore, *models.RequestContext) {
	t.Helper()
	ks := authpkg.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{key}})
	rc := models.AcquireRequestContext()
	rc.Model = "gpt-4o"
	rc.Metadata["authorization"] = "Bearer " + key.Key
	if res := New(ks, true).ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
		rc.Release()
		t.Fatalf("auth: action=%d err=%v", res.Action, res.Error)
	}
	return ks, rc
}

// A reserved metadata name that is not authority never reaches the request as
// a label: "type" cannot replace key_type, and id, name and owner cannot stand
// in for the key's identity. Labels still arrive under "key_".
func TestPlugin_ReservedMetadataIsNeverCopiedAsALabel(t *testing.T) {
	for _, name := range reservedLabelNames {
		t.Run(name, func(t *testing.T) {
			_, rc := authenticate(t, config.AuthKeyConfig{
				Name:     "tenant-key",
				Key:      "sk-agentcc-reserved-" + name,
				Owner:    "tenant-owner",
				KeyType:  "byok",
				Metadata: map[string]string{name: "spoofed", "env": "prod"},
			})
			defer rc.Release()

			if v, set := rc.Metadata["key_"+name]; set && v == "spoofed" {
				t.Errorf("metadata %s reached the request as key_%s=%q", name, name, v)
			}
			if v := rc.Metadata["key_type"]; v != "byok" {
				t.Errorf("key_type = %q, want byok", v)
			}
			if v := rc.Metadata["auth_key_name"]; v != "tenant-key" {
				t.Errorf("auth_key_name = %q, want tenant-key", v)
			}
			if v := rc.Metadata["auth_key_owner"]; v != "tenant-owner" {
				t.Errorf("auth_key_owner = %q, want tenant-owner", v)
			}
			if v := rc.Metadata["auth_key_id"]; v == "" || v == "spoofed" {
				t.Errorf("auth_key_id = %q, want the key's own id", v)
			}
			if v := rc.Metadata["key_env"]; v != "prod" {
				t.Errorf("key_env = %q, want prod", v)
			}
		})
	}
}

// key_org_id is the authenticated key's own org, written after the labels.
// Nothing written to the request earlier survives, whether the key has an org
// or not.
func TestPlugin_KeyOrgIDIsTheAuthenticatedKeysOwn(t *testing.T) {
	cases := []struct {
		name     string
		metadata map[string]string
		want     string
	}{
		{"key with an org", map[string]string{"org_id": "org-a", "env": "infra"}, "org-a"},
		{"key without an org", map[string]string{"env": "infra"}, ""},
	}
	for i, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			raw := "sk-agentcc-org-" + string(rune('a'+i))
			ks := authpkg.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{{
				Name: "tenant-key", Key: raw, KeyType: "byok", Metadata: tc.metadata,
			}}})
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Metadata["authorization"] = "Bearer " + raw
			// Values for another key, as an earlier writer might have left them.
			for _, name := range authorityNames {
				rc.Metadata["key_"+name] = "other-key"
			}

			if res := New(ks, true).ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
				t.Fatalf("auth: action=%d err=%v", res.Action, res.Error)
			}

			if got := rc.Metadata["key_org_id"]; got != tc.want {
				t.Errorf("key_org_id = %q, want %q", got, tc.want)
			}
			for _, name := range authorityNames {
				if v, set := rc.Metadata["key_"+name]; name != "org_id" && set {
					t.Errorf("key_%s = %q survived for a key that has none", name, v)
				}
			}
			if got := rc.Metadata["key_env"]; got != "infra" {
				t.Errorf("key_env = %q, want infra", got)
			}
		})
	}
}

// A managed key with no credits is refused by the credits plugin whatever type
// its metadata names: key_type is always the key's own.
func TestPipeline_MetadataTypeCannotSkipAManagedKeysCreditCheck(t *testing.T) {
	for _, spoof := range []string{"byok", "internal"} {
		t.Run(spoof, func(t *testing.T) {
			ks, rc := authenticate(t, config.AuthKeyConfig{
				Name:     "managed-key",
				Key:      "sk-agentcc-managed-" + spoof,
				KeyType:  "managed",
				Metadata: map[string]string{"org_id": "org-a", "type": spoof},
			})
			defer rc.Release()

			res := credits.New(true, ks).ProcessRequest(context.Background(), rc)

			if res.Error == nil || res.Error.Status != http.StatusPaymentRequired {
				t.Fatalf("credits: action=%d err=%v, want a 402 refusal for a $0 managed key", res.Action, res.Error)
			}
		})
	}
}

// type=internal on a tenant key must not hide its requests from the request
// log: the logging plugin skips only the gateway's own internal keys.
func TestPipeline_MetadataTypeCannotHideARequestFromTheLog(t *testing.T) {
	_, rc := authenticate(t, config.AuthKeyConfig{
		Name:     "tenant-key",
		Key:      "sk-agentcc-hidden",
		KeyType:  "byok",
		Metadata: map[string]string{"org_id": "org-a", "type": "internal"},
	})
	defer rc.Release()
	rc.StartTime = time.Now().Add(-10 * time.Millisecond)
	rc.Response = &models.ChatCompletionResponse{Usage: &models.Usage{TotalTokens: 3}}

	traces := &traceCounter{}
	prev := slog.Default()
	slog.SetDefault(slog.New(traces))
	t.Cleanup(func() { slog.SetDefault(prev) })

	p := logging.New(config.RequestLoggingConfig{Enabled: true, BufferSize: 4, Workers: 1}, nil)
	p.ProcessResponse(context.Background(), rc)
	p.Close()

	if traces.count() != 1 {
		t.Fatalf("request.trace records = %d, want 1: the request was left out of the log", traces.count())
	}
}

// traceCounter counts the request log records the logging plugin emits.
type traceCounter struct {
	mu sync.Mutex
	n  int
}

func (h *traceCounter) Enabled(context.Context, slog.Level) bool { return true }
func (h *traceCounter) Handle(_ context.Context, r slog.Record) error {
	if r.Message == "request.trace" {
		h.mu.Lock()
		h.n++
		h.mu.Unlock()
	}
	return nil
}
func (h *traceCounter) WithAttrs([]slog.Attr) slog.Handler { return h }
func (h *traceCounter) WithGroup(string) slog.Handler      { return h }
func (h *traceCounter) count() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.n
}
