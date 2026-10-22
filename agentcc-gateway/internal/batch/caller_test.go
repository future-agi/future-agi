package batch

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func batchRequests(n int) []*models.ChatCompletionRequest {
	reqs := make([]*models.ChatCompletionRequest, n)
	for i := range reqs {
		reqs[i] = &models.ChatCompletionRequest{Model: "gpt-4o-mini", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}}
	}
	return reqs
}

func batchSubmit(keyHeader string) *http.Request {
	r := httptest.NewRequest(http.MethodPost, "/-/batches", nil)
	r.RemoteAddr = "203.0.113.7:4242"
	r.Header.Set("X-Forwarded-For", "198.51.100.9")
	if keyHeader != "" {
		r.Header.Set(KeyHeader, keyHeader)
	}
	return r
}

func TestBatchCallerForChecksTheBatchKey(t *testing.T) {
	keys := auth.NewKeyStore(config.AuthConfig{})
	key, raw := keys.Create("console-batch-x", "o", nil, nil, map[string]string{"org_id": "org-x"})
	revoked, revokedRaw := keys.Create("console-batch-r", "o", nil, nil, map[string]string{"org_id": "org-x"})
	keys.Revoke(revoked.ID)

	required := NewProcessor(nil, WithKeyStore(keys, true))
	defer required.Stop()
	optional := NewProcessor(nil, WithKeyStore(keys, false))
	defer optional.Stop()
	none := NewProcessor(nil)
	defer none.Stop()

	if _, err := required.CallerFor(batchSubmit("")); err == nil || err.Status != http.StatusBadRequest || err.Code != ErrCodeBatchKeyRequired {
		t.Fatalf("required, no key: %+v; want 400 %s", err, ErrCodeBatchKeyRequired)
	}
	for name, h := range map[string]string{"unknown": "sk-agentcc-unknown", "revoked": revokedRaw, "bearer unknown": "Bearer sk-agentcc-unknown"} {
		for pname, p := range map[string]*Processor{"required": required, "optional": optional, "no key store": none} {
			if _, err := p.CallerFor(batchSubmit(h)); err == nil || err.Status != http.StatusUnauthorized || err.Code != "invalid_api_key" {
				t.Errorf("%s, %s key: %+v; want 401 invalid_api_key", pname, name, err)
			}
		}
	}
	for _, h := range []string{raw, "Bearer " + raw} {
		c, err := required.CallerFor(batchSubmit(h))
		if err != nil || c.KeyID != key.ID || c.OrgID != "org-x" || c.key != raw {
			t.Fatalf("valid key %q: caller %+v err %+v", h, c, err)
		}
		if c.remoteAddr != "203.0.113.7:4242" || c.forwarded.Get("X-Forwarded-For") != "198.51.100.9" {
			t.Fatalf("caller address %q %v; want the submit's", c.remoteAddr, c.forwarded)
		}
	}
	c, err := optional.CallerFor(batchSubmit(""))
	if err != nil || c.KeyID != "" || c.OrgID != "" || c.key != "" || c.remoteAddr != "203.0.113.7:4242" {
		t.Fatalf("optional, no key: caller %+v err %+v; want a keyless caller with the submit's address", c, err)
	}
}

// A batch of one org is neither found nor cancelled for another org (or for
// none), and the raw key never reaches the Batch or its JSON.
func TestBatchGetAndCancelAreScopedToTheOrg(t *testing.T) {
	release := make(chan struct{})
	p := NewProcessor(func(ctx context.Context, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		select {
		case <-release:
		case <-ctx.Done():
			return nil, nil, ctx.Err()
		}
		return &models.ChatCompletionResponse{ID: "ok"}, nil, nil
	})
	defer p.Stop()
	const rawKey = "sk-agentcc-raw-key-never-serialized"
	b := p.SubmitAs(batchRequests(2), 2, "trace-x", Caller{KeyID: "key_x", OrgID: "org-x", key: rawKey})

	if raw, _ := json.Marshal(b); strings.Contains(string(raw), rawKey) || !strings.Contains(string(raw), `"org_id":"org-x"`) {
		t.Fatalf("batch JSON %s: must carry org_id and never the raw key", raw)
	}
	for _, org := range []string{"org-y", ""} {
		if _, ok := p.GetForOrg(b.ID, org); ok {
			t.Errorf("GetForOrg(%q) found org-x's batch", org)
		}
		if _, ok := p.CancelForOrg(b.ID, org); ok {
			t.Errorf("CancelForOrg(%q) cancelled org-x's batch", org)
		}
	}
	if got, ok := p.GetForOrg(b.ID, "org-x"); !ok || got.GetStatus() != "processing" || got.KeyID != "key_x" {
		t.Fatalf("org-x's own get: %v %v", got, ok)
	}
	if got, ok := p.CancelForOrg(b.ID, "org-x"); !ok || got.GetStatus() != "cancelled" {
		t.Fatalf("org-x's own cancel: %v %v", got, ok)
	}
	close(release)

	keyless := p.Submit(batchRequests(1), 1)
	if _, ok := p.GetForOrg(keyless.ID, ""); !ok {
		t.Fatal("a batch with no key is found with no org")
	}
	if _, ok := p.GetForOrg(keyless.ID, "org-x"); ok {
		t.Fatal("a batch with no key was found for org-x")
	}
}

// RunAsChatRequest serves each item as POST /v1/chat/completions with the
// batch key, the batch's trace id and the submit's client address; it never
// streams; the result names the request id and cost the handler answered with;
// a refusal comes back as the gateway's APIError.
func TestBatchRunAsChatRequestCarriesTheKeyTraceAndAddress(t *testing.T) {
	var mu sync.Mutex
	var seen []*http.Request
	var bodies []map[string]any
	status := http.StatusOK
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(b, &body)
		mu.Lock()
		seen = append(seen, r)
		bodies = append(bodies, body)
		code := status
		mu.Unlock()
		w.Header().Set("x-agentcc-request-id", "req-1")
		w.Header().Set("x-agentcc-cost", "0.25")
		if code != http.StatusOK {
			models.WriteError(w, models.ErrUnauthorized("API key has been revoked"))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"id":"chatcmpl-1","object":"chat.completion","model":"gpt-4o-mini","choices":[]}`)
	})
	run := RunAsChatRequest(func() http.Handler { return handler })
	ctx := withCaller(models.WithTraceID(context.Background(), "trace-of-batch"), Caller{
		KeyID: "key_x", OrgID: "org-x", key: "sk-raw", remoteAddr: "203.0.113.7:4242",
		forwarded: http.Header{"X-Forwarded-For": {"198.51.100.9"}},
	})
	req := batchRequests(1)[0]
	req.Stream = true
	req.StreamOptions = &models.StreamOptions{IncludeUsage: true}

	resp, meta, err := run(ctx, req)
	if err != nil || resp == nil || resp.ID != "chatcmpl-1" {
		t.Fatalf("run: %v %v", resp, err)
	}
	if meta[MetaRequestID] != "req-1" || meta["cost"] != "0.25" {
		t.Fatalf("meta %v; want the handler's request id and cost", meta)
	}
	r := seen[0]
	if r.Method != http.MethodPost || r.URL.Path != ChatPath {
		t.Fatalf("item served as %s %s", r.Method, r.URL.Path)
	}
	if r.Header.Get("Authorization") != "Bearer sk-raw" || r.Header.Get("x-agentcc-trace-id") != "trace-of-batch" {
		t.Fatalf("item headers %v; want the batch key and trace id", r.Header)
	}
	if r.RemoteAddr != "203.0.113.7:4242" || r.Header.Get("X-Forwarded-For") != "198.51.100.9" {
		t.Fatalf("item address %q %v; want the submit's", r.RemoteAddr, r.Header)
	}
	if bodies[0]["stream"] == true || bodies[0]["stream_options"] != nil {
		t.Fatalf("item body %v streams", bodies[0])
	}
	if !req.Stream {
		t.Fatal("RunAsChatRequest changed the caller's request")
	}

	mu.Lock()
	status = http.StatusUnauthorized
	mu.Unlock()
	_, meta, err = run(ctx, req)
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != http.StatusUnauthorized || apiErr.Code != "invalid_api_key" || err.Error() != "invalid_api_key: API key has been revoked" {
		t.Fatalf("refused item: %v; want the gateway's 401 invalid_api_key", err)
	}
	if meta[MetaRequestID] != "req-1" {
		t.Fatalf("a refused item's meta %v; want its request id", meta)
	}
}

// Every item's handler gets the batch's caller on its context: SubmitAs hands
// it to each item, which is how RunAsChatRequest finds the key to send.
func TestBatchItemsSeeTheirCaller(t *testing.T) {
	var mu sync.Mutex
	var got []Caller
	p := NewProcessor(func(ctx context.Context, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		c, _ := callerFrom(ctx)
		mu.Lock()
		got = append(got, c)
		mu.Unlock()
		return &models.ChatCompletionResponse{}, nil, nil
	})
	defer p.Stop()
	b := p.SubmitAs(batchRequests(3), 3, "", Caller{KeyID: "key_x", OrgID: "org-x", key: "sk-raw"})
	for deadline := time.Now().Add(5 * time.Second); b.GetStatus() != "completed"; time.Sleep(5 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatal("batch never completed")
		}
	}
	mu.Lock()
	defer mu.Unlock()
	if len(got) != 3 {
		t.Fatalf("handler ran %d times", len(got))
	}
	for _, c := range got {
		if c.key != "sk-raw" || c.OrgID != "org-x" {
			t.Fatalf("an item ran as %+v", c)
		}
	}
}
