package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"slices"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
)

// L1 joins the admin receipt, real main's cache construction and the chat
// handler's org TTL override. Node enablement is an operator restart, NOT an
// org cache.enabled edit or a /-/reload (which only rebuilds routing). The
// process-local tenant store is rehydrated through the same admin PUT as Django
// uses, with the exact receipt saved while the old process had no cache plugin.
// This is not a browser/Django delivery test: that transport remains separate.
func TestSavedOrgCacheAppliesAfterNodeEnableWithTTL(t *testing.T) {
	const ttl = 4 * time.Second
	const orgPath = "/-/orgs/org-nothing/config"
	var calls atomic.Int64
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" || r.Method != http.MethodPost {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		_, _ = io.Copy(io.Discard, r.Body)
		n := calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id": fmt.Sprintf("l1-upstream-%d", n), "object": "chat.completion",
			"created": 1, "model": "gpt-4o",
			"choices": []any{map[string]any{
				"index": 0, "finish_reason": "stop",
				"message": map[string]string{"role": "assistant", "content": fmt.Sprintf("answer %d", n)},
			}},
			"usage": map[string]int{"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
		})
	}))
	t.Cleanup(provider.Close)

	// Only the node switch differs between launches. The 1h node default is
	// deliberately much longer than the org's 4s, and semantic/edge tiers are off.
	nodeConfig := func(enabled bool) string {
		return `server:
  host: 127.0.0.1
  port: %d
  trusted_proxies: ["192.0.2.255/32"]
providers:
  openai:
    base_url: ` + provider.URL + `
    api_key: fake-operator-key
    api_format: openai
    models: [gpt-4o]
auth:
  enabled: true
  keys:
` + r7OrgKeys() + `admin:
  token: ` + testAdminToken + `
cache:
  enabled: ` + fmt.Sprint(enabled) + `
  backend: memory
  default_ttl: 1h
  semantic: {enabled: false}
  edge: {enabled: false}
`
	}
	readSaved := func(t *testing.T, g *readinessGateway) (string, map[string]any) {
		t.Helper()
		code, body := r7Admin(t, g, http.MethodGet, orgPath, "")
		if code != http.StatusOK {
			t.Fatalf("read saved org config: status %d, want 200", code)
		}
		var saved map[string]any
		if err := json.Unmarshal([]byte(body), &saved); err != nil {
			t.Fatal(err)
		}
		want := map[string]any{"enabled": true, "default_ttl": ttl.Seconds()}
		if !reflect.DeepEqual(saved["cache"], want) {
			t.Fatalf("saved org cache = %v, want %v", saved["cache"], want)
		}
		return body, saved
	}
	put := func(t *testing.T, g *readinessGateway, body string) {
		t.Helper()
		if code, _ := r7Admin(t, g, http.MethodPut, orgPath, body); code != http.StatusOK {
			t.Fatalf("push saved org config: status %d, want 200", code)
		}
	}

	var savedBody string
	var savedConfig map[string]any
	if !t.Run("save_while_node_has_no_cache", func(t *testing.T) {
		g := startReadinessGateway(t, nodeConfig(false))
		processEventually(t, func() bool { return g.get("/readyz") == http.StatusOK })
		if slices.Contains(r7Sections(t, g), "cache") {
			t.Fatal("off phase unexpectedly enforces response caching")
		}
		put(t, g, `{"cache":{"enabled":true,"default_ttl":4}}`)
		savedBody, savedConfig = readSaved(t, g)
		first := l1Chat(t, g, "", 1, &calls)
		second := l1Chat(t, g, "", 2, &calls)
		if first == second {
			t.Fatal("node without cache reused an upstream answer")
		}
	}) {
		return
	}
	// The off subtest's cleanup has killed and waited for the old process.
	// There is no inherited in-memory tenant store or cache at this point.
	t.Run("restart_enable_and_rehydrate_unchanged_org", func(t *testing.T) {
		g := startReadinessGateway(t, nodeConfig(true))
		processEventually(t, func() bool { return g.get("/readyz") == http.StatusOK })
		if !slices.Contains(r7Sections(t, g), "cache") {
			t.Fatal("enabled node did not install response caching")
		}
		if code, _ := r7Admin(t, g, http.MethodGet, orgPath, ""); code != http.StatusNotFound {
			t.Fatalf("fresh process org config: status %d, want 404 before rehydration", code)
		}
		put(t, g, savedBody)

		first := l1Chat(t, g, "MISS", 3, &calls)
		filled := time.Now()
		if hit := l1Chat(t, g, "HIT", 3, &calls); hit != first {
			t.Fatal("cache hit changed the upstream response id")
		}
		// The production memory store has no injectable clock: Get and Set
		// call time.Now directly. Keep the real startup/cache path, rather than
		// substitute a fake backend. Wait from the completed fill so expiry is
		// certainly after insertion, but far earlier than the 1h node default.
		// A second hit at 3s also rejects an incorrectly shortened 1s TTL;
		// expiry at 4.1s proves that a hit does not slide the insertion TTL.
		time.Sleep(time.Until(filled.Add(ttl - time.Second)))
		if hit := l1Chat(t, g, "HIT", 3, &calls); hit != first {
			t.Fatal("entry expired before the saved org TTL")
		}
		time.Sleep(time.Until(filled.Add(ttl + 100*time.Millisecond)))
		if expired := l1Chat(t, g, "MISS", 4, &calls); expired == first {
			t.Fatal("expired org entry reused the old upstream response id")
		}
		_, after := readSaved(t, g)
		if !reflect.DeepEqual(after, savedConfig) {
			t.Fatal("restart or cache use changed the saved org config")
		}
	})
}

// No per-request cache headers or caller metadata: only the authenticated
// org's saved settings can opt this request in or override the node TTL.
func l1Chat(t *testing.T, g *readinessGateway, wantStatus string, wantCalls int64, calls *atomic.Int64) string {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, g.url+"/v1/chat/completions",
		strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"l1 same org same prompt"}]}`))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+r7KeyNothing)
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("chat status %d, want 200", resp.StatusCode)
	}
	if got := resp.Header.Get(cacheplugin.HeaderCacheStatus); got != wantStatus {
		t.Fatalf("cache status = %q, want %q after node transition", got, wantStatus)
	}
	var body models.ChatCompletionResponse
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if got := calls.Load(); got != wantCalls {
		t.Fatalf("upstream calls = %d, want %d (cache status %q)", got, wantCalls, wantStatus)
	}
	if body.ID != fmt.Sprintf("l1-upstream-%d", wantCalls) {
		t.Fatalf("response id = %q, want upstream answer %d", body.ID, wantCalls)
	}
	return body.ID
}
