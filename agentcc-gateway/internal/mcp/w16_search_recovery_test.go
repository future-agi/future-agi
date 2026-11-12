package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Local route double only. Its quota is not evidence for Redis admission or
// accounting. W16SyntheticPort separately binds those independent witnesses.
type w16SearchRoute struct {
	mu       sync.Mutex
	calls    int
	attempts int
}

func (r *w16SearchRoute) Embed(_ context.Context, cc *CallContext, req *models.EmbeddingRequest) (*models.EmbeddingResponse, string, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.attempts++
	if cc.Principal.OrgID != "org-a" || req.Model != "synthetic/embed" || r.calls >= 2 {
		return nil, "", errors.New("synthetic admission refusal")
	}
	var texts []string
	if err := json.Unmarshal(req.Input, &texts); err != nil {
		return nil, "", err
	}
	r.calls++
	out := &models.EmbeddingResponse{Model: req.Model}
	for i, text := range texts {
		vector := json.RawMessage(`[1,0]`)
		if strings.Contains(text, "b_read") || text == "read record" {
			vector = json.RawMessage(`[0,1]`)
		}
		out.Data = append(out.Data, models.EmbeddingData{Index: i, Embedding: vector})
	}
	return out, "synthetic@2:float:default", nil
}

func (r *w16SearchRoute) counts() (int, int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.calls, r.attempts
}

func w16Install(t *testing.T, s *Server, route *w16SearchRoute) {
	t.Helper()
	method := reflect.ValueOf(s).MethodByName("SetSearchEmbeddingRoute")
	if !method.IsValid() {
		t.Fatal("hybrid embedding route is not installed in this assembly")
	}
	if method.Type().NumIn() != 1 || !reflect.TypeOf(route).AssignableTo(method.Type().In(0)) {
		t.Fatal("incompatible SearchEmbeddingRoute contract")
	}
	method.Call([]reflect.Value{reflect.ValueOf(route)})
}

func w16Configure(t *testing.T, f *vsFixture, search string) {
	t.Helper()
	raw, err := json.Marshal(f.cfg)
	if err != nil {
		t.Fatal(err)
	}
	var cfg map[string]any
	if err := json.Unmarshal(raw, &cfg); err != nil {
		t.Fatal(err)
	}
	mcp := cfg["mcp"].(map[string]any)
	delete(mcp, "search")
	if search != "" {
		var value any
		if err := json.Unmarshal([]byte(search), &value); err != nil {
			t.Fatal(err)
		}
		mcp["search"] = value
	}
	raw, err = json.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	f.cfg = orgConfig(t, string(raw))
}

func TestW16SyntheticSearchControls(t *testing.T) {
	f := newVSFixture(t, searchVS(`["docs"]`, `"access":{"default_for_org":true}`))
	f.Registry().RegisterServer("docs", []Tool{
		{Name: "a_read", Description: "read record"},
		{Name: "b_read", Description: "read record"},
	})
	route := &w16SearchRoute{}
	w16Install(t, f.Server, route)
	query := func() string {
		t.Helper()
		w := f.direct(t, "", "search_tools", map[string]any{"query": "read record", "limit": 1}, nil)
		response := decodeResponse(t, w)
		if response.Error != nil {
			t.Fatalf("search failed: %+v", response.Error)
		}
		var result struct {
			StructuredContent struct {
				Tools []searchHit `json:"tools"`
			} `json:"structuredContent"`
		}
		if err := json.Unmarshal(response.Result, &result); err != nil {
			t.Fatal(err)
		}
		if len(result.StructuredContent.Tools) != 1 {
			t.Fatalf("unexpected search result: %s", response.Result)
		}
		return result.StructuredContent.Tools[0].Name
	}
	for _, config := range []string{"", `{"enabled":true}`} {
		w16Configure(t, f, config)
		f.toolNames(t, "", nil)
		if query() != "docs__a_read" {
			t.Fatal("lexical fallback changed")
		}
		if calls, attempts := route.counts(); calls != 0 || attempts != 0 {
			t.Fatal("dispatched while absent/incomplete")
		}
	}
	w16Configure(t, f, `{"enabled":true,"model":"synthetic/embed","calls_per_minute":2,"timeout_ms":500}`)
	f.toolNames(t, "", nil)
	if calls, _ := route.counts(); calls != 1 {
		t.Fatal("discovery did not build the synthetic index")
	}
	if query() != "docs__b_read" {
		t.Fatal("complete opt-in did not rerank")
	}
	if calls, _ := route.counts(); calls != 2 {
		t.Fatal("expected index plus query")
	}
	if query() != "docs__a_read" {
		t.Fatal("refused third embedding did not retain lexical answer")
	}
	if calls, attempts := route.counts(); calls != 2 || attempts != 3 {
		t.Fatal("third admission was not refused")
	}
	// No foreign tenant may borrow the existing index, even with the same config.
	f.principal.OrgID = "org-b"
	if query() != "docs__a_read" {
		t.Fatal("org B borrowed org A vectors")
	}
	if calls, attempts := route.counts(); calls != 2 || attempts != 3 {
		t.Fatal("org B dispatched")
	}
	f.principal.OrgID = "org-a"
	w16Configure(t, f, `{"enabled":false,"model":"synthetic/embed","calls_per_minute":2}`)
	f.toolNames(t, "", nil)
	if query() != "docs__a_read" {
		t.Fatal("disable lost lexical answer")
	}
	if calls, attempts := route.counts(); calls != 2 || attempts != 3 {
		t.Fatal("disable dispatched")
	}
}
