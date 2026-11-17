package batch

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

const batch733Org = "11111111-1111-4111-8111-111111111111"
const batch733User = "22222222-2222-4222-8222-222222222222"
const batch733Grant = "test-only-opaque-grant-never-serialized"

func r7Requests(n int) []*models.ChatCompletionRequest {
	reqs := make([]*models.ChatCompletionRequest, n)
	for i := range reqs {
		reqs[i] = &models.ChatCompletionRequest{Model: "gpt-4o-mini", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}}
	}
	return reqs
}
func r7Submit(keyHeader string) *http.Request {
	r := httptest.NewRequest("POST", "/-/batches", nil)
	r.RemoteAddr = "203.0.113.7:4242"
	r.Header.Set("X-Forwarded-For", "198.51.100.9")
	if keyHeader != "" {
		r.Header.Set(KeyHeader, keyHeader)
	}
	return r
}
func batch733Context() context.Context {
	now := time.Now().Unix()
	return auth.WithDelegatedPrincipal(context.Background(), auth.EnvelopeClaims{Mode: "grant", Kid: "k1", Iss: "fa", Aud: "fa-eval-grant", Iat: now, Exp: now + 600, ID: strings.Repeat("a", 32), Kind: "batch", Org: batch733Org, User: batch733User, Workspace: "-", Via: "person", Scope: "eval"}, batch733Grant)
}
func batch733Keys() *auth.KeyStore {
	ks := auth.NewKeyStore(config.AuthConfig{})
	ks.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{{Org: batch733Org, User: batch733User, Workspace: "-"}}}, time.Now())
	return ks
}
func batch733Caller(t *testing.T, p *Processor) Caller {
	t.Helper()
	c, err := p.CallerFor(r7Submit("").WithContext(batch733Context()))
	if err != nil {
		t.Fatal(err)
	}
	return c
}
func waitBatch733(t *testing.T, b *Batch) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for b.GetCompletedAt() == nil {
		if time.Now().After(deadline) {
			t.Fatal("batch did not finish")
		}
		time.Sleep(time.Millisecond)
	}
}

// Replaces the retired key transport while retaining refusal, identity and
// caller-address assertions. API auth being disabled is not a bypass.
func TestR7BatchCallerForChecksTheBatchKey(t *testing.T) {
	keys := batch733Keys()
	_, raw := keys.Create("personal", "owner", nil, nil, nil)
	for _, required := range []bool{false, true} {
		p := NewProcessor(nil, WithKeyStore(keys, required))
		defer p.Stop()
		for _, header := range []string{"", raw, "Bearer " + raw, "sk-unknown"} {
			if _, err := p.CallerFor(r7Submit(header)); err == nil || err.Status != 401 || err.Code != "service_envelope_rejected" {
				t.Fatalf("legacy submit: %v", err)
			}
		}
		c := batch733Caller(t, p)
		if c.KeyID != "dlg_"+strings.Repeat("a", 32) || c.OrgID != batch733Org || c.remoteAddr != "203.0.113.7:4242" || c.forwarded.Get("X-Forwarded-For") != "198.51.100.9" {
			t.Fatal("principal/address not preserved")
		}
	}
	p := NewProcessor(nil)
	defer p.Stop()
	if _, err := p.CallerFor(r7Submit("").WithContext(batch733Context())); err == nil {
		t.Fatal("no membership store admitted delegation")
	}
}
func TestR7BatchGetAndCancelAreScopedToTheOrg(t *testing.T) {
	entered := make(chan struct{}, 2)
	p := NewProcessor(func(ctx context.Context, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		entered <- struct{}{}
		<-ctx.Done()
		return nil, nil, ctx.Err()
	}, WithKeyStore(batch733Keys(), true))
	defer p.Stop()
	b := p.SubmitAs(r7Requests(2), 2, "trace-x", batch733Caller(t, p))
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("no item started")
	}
	raw, _ := json.Marshal(b)
	if strings.Contains(string(raw), batch733Grant) || strings.Contains(string(raw), batch733User) || !strings.Contains(string(raw), batch733Org) {
		t.Fatalf("bad batch serialization: %s", raw)
	}
	for _, org := range []string{"other-org", ""} {
		if _, ok := p.GetForOrg(b.ID, org); ok {
			t.Fatal("foreign get succeeded")
		}
		if _, ok := p.CancelForOrg(b.ID, org); ok {
			t.Fatal("foreign cancel succeeded")
		}
	}
	for i := 0; i < 2; i++ {
		if got, ok := p.CancelForOrg(b.ID, batch733Org); !ok || got.GetStatus() != "cancelled" {
			t.Fatal("cancel not idempotent")
		}
	}
	if got, ok := p.GetForOrg(b.ID, batch733Org); !ok || got.GetStatus() != "cancelled" {
		t.Fatal("cancel readback failed")
	}
	waitBatch733(t, b)
}
func TestR7BatchRunAsChatRequestCarriesTheKeyTraceAndAddress(t *testing.T) {
	p := NewProcessor(nil, WithKeyStore(batch733Keys(), true))
	defer p.Stop()
	c := batch733Caller(t, p)
	ctx := withCaller(models.WithTraceID(context.Background(), "trace-of-batch"), c)
	status := http.StatusOK
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || r.URL.Path != ChatPath || r.Header.Get("Authorization") != "" || r.Header.Get(auth.GrantHeader) != "" {
			t.Error("item must be in-process with no bearer or grant header")
		}
		if key := auth.DelegatedPrincipal(r.Context()); key == nil || key.ID != c.KeyID || key.Metadata["org_id"] != batch733Org {
			t.Error("missing principal")
		}
		if token, _, ok := auth.DelegatedGrant(r.Context()); !ok || token != batch733Grant {
			t.Error("missing private grant")
		}
		if r.Header.Get("x-agentcc-trace-id") != "trace-of-batch" || r.RemoteAddr != "203.0.113.7:4242" || r.Header.Get("X-Forwarded-For") != "198.51.100.9" {
			t.Error("trace/address lost")
		}
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		if body["stream"] == true || body["stream_options"] != nil {
			t.Error("batch item streamed")
		}
		w.Header().Set("x-agentcc-request-id", "req-1")
		w.Header().Set("x-agentcc-cost", "0.25")
		if status != http.StatusOK {
			models.WriteError(w, models.ErrUnauthorized("revoked"))
			return
		}
		io.WriteString(w, `{"id":"chatcmpl-1","object":"chat.completion","choices":[]}`)
	})
	run := RunAsChatRequest(func() http.Handler { return handler })
	req := r7Requests(1)[0]
	req.Stream = true
	req.StreamOptions = &models.StreamOptions{IncludeUsage: true}
	resp, meta, err := run(ctx, req)
	if err != nil || resp.ID != "chatcmpl-1" || meta[MetaRequestID] != "req-1" || meta["cost"] != "0.25" || !req.Stream {
		t.Fatalf("run: %v %v", resp, err)
	}
	status = 401
	_, meta, err = run(ctx, req)
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != 401 || meta[MetaRequestID] != "req-1" {
		t.Fatalf("error metadata: %v", err)
	}
}
func TestR7BatchItemsSeeTheirCaller(t *testing.T) {
	var calls atomic.Int32
	p := NewProcessor(func(ctx context.Context, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		c, ok := callerFrom(ctx)
		if !ok || c.OrgID != batch733Org || auth.DelegatedPrincipal(ctx) == nil {
			t.Error("item lost caller")
		}
		calls.Add(1)
		return &models.ChatCompletionResponse{}, nil, nil
	}, WithKeyStore(batch733Keys(), true))
	defer p.Stop()
	b := p.SubmitAs(r7Requests(3), 3, "", batch733Caller(t, p))
	waitBatch733(t, b)
	if calls.Load() != 3 {
		t.Fatalf("calls: %d", calls.Load())
	}
}

// No cancel call is made. While the first item holds the only slot, a synced
// removal/revocation or the two-interval outage tolerance must stop queued items.
func TestDelegated733DroppedCancelStopsQueuedItems(t *testing.T) {
	for _, mode := range []string{"membership", "grant", "stale", "expiry"} {
		t.Run(mode, func(t *testing.T) {
			keys := batch733Keys()
			started := make(chan struct{})
			release := make(chan struct{})
			var calls atomic.Int32
			p := NewProcessor(func(ctx context.Context, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
				if calls.Add(1) == 1 {
					close(started)
					<-release
				}
				return &models.ChatCompletionResponse{}, nil, nil
			}, WithKeyStore(keys, true))
			defer p.Stop()
			caller := batch733Caller(t, p)
			if mode == "stale" { // Fresh at admission, expires while first item is held.
				keys = auth.NewKeyStore(config.AuthConfig{})
				keys.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{{Org: batch733Org, User: batch733User, Workspace: "-"}}}, time.Now().Add(-119*time.Second))
				p.keyCheck.keys = keys
			}
			if mode == "expiry" {
				token, claims, _ := auth.DelegatedGrant(caller.principal)
				claims.Exp = time.Now().Unix() + 1
				caller.principal = auth.WithDelegatedPrincipal(context.Background(), claims, token)
			}
			b := p.SubmitAs(r7Requests(3), 1, "", caller)
			select {
			case <-started:
			case <-time.After(5 * time.Second):
				close(release)
				t.Fatal("first item did not start")
			}
			switch mode {
			case "membership":
				keys.SyncDelegations(&auth.DelegationSnapshot{Complete: true}, time.Now())
			case "grant":
				keys.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{{Org: batch733Org, User: batch733User, Workspace: "-"}}, RevokedGrants: []string{strings.Repeat("a", 32)}}, time.Now())
			default:
				time.Sleep(2100 * time.Millisecond)
			}
			close(release)
			waitBatch733(t, b)
			if calls.Load() != 1 || b.GetStatus() != "cancelled" || b.Summary().Cancelled != 2 {
				t.Fatalf("revoked items ran: calls=%d status=%s summary=%+v", calls.Load(), b.GetStatus(), b.Summary())
			}
		})
	}
}
