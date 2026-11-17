package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const k2BoundaryToken = "k2-boundary-synthetic-upstream-token"

// These fixtures run every production MCP wiring init, every afterServer
// callback and the real HTTP tools/call chain. Only the authenticated caller,
// upstream transport and token service are synthetic external dependencies.
// The final CredentialResolver is NEVER replaced, wrapped or called directly
// by a test. No Redis, live provider or hosted service is needed.
type k2BoundaryFixture struct {
	srv  *mcp.Server
	real *k2ConstructionFixture
}

// Copy records instead of changing the cached embedded catalog. Tests here
// are deliberately nonparallel: restore this loader before another test runs.
func k2BoundaryCatalog(t *testing.T, sources []string) *manifest.Catalog {
	return k2ConstructionCatalog(t, func(_ string, record map[string]any) {
		record["auth"].(map[string]any)["oauth2"].(map[string]any)["clientSources"] = sources
	})
}

func newK2BoundaryFixture(t *testing.T, runtimeSet bool, ref, app string) *k2BoundaryFixture {
	real := newK2ConstructionFixture(t, runtimeSet, app, ref, "per_user", "")
	f := &k2BoundaryFixture{srv: real.srv, real: real}
	// The inventory adapter lets stale/unknown refs return their typed denial
	// through tools/call, without discovering upstream or replacing construction.
	ten := &k2BoundaryTenancy{Tenancy: real.srv.Tenancy()}
	real.srv.SetTenancy(ten)
	sep := real.srv.Registry().Separator()
	real.srv.Registry().SetOrgSource(func(org string) mcp.MemberSource {
		_, pool, err := ten.Resolve(org)
		if err != nil {
			return nil
		}
		return mcp.PoolMembers(pool, sep)
	})
	return f
}

type k2BoundaryCaller struct{}

func (k2BoundaryCaller) Verify(_ context.Context, token, _ string) (*mcp.Principal, error) {
	if token != "k2-boundary-synthetic-caller" {
		return nil, nil
	}
	return &mcp.Principal{Kind: mcp.PrincipalOAuth, OrgID: "org-a", UserID: "user-a",
		Scopes: []string{"mcp:tools"}, AllowedTools: []string{"*"}}, nil
}

type k2BoundaryTenancy struct{ mcp.Tenancy }

func (s *k2BoundaryTenancy) Resolve(org string) (*tenant.OrgConfig, mcp.TenantPool, error) {
	cfg, pool, err := s.Tenancy.Resolve(org)
	if err != nil || pool == nil {
		return cfg, pool, err
	}
	return cfg, &k2BoundaryPool{TenantPool: pool}, nil
}

type k2BoundaryPool struct{ mcp.TenantPool }

func (p *k2BoundaryPool) Server(id string) (mcp.PoolServer, bool) {
	s, ok := p.TenantPool.Server(id)
	if ok {
		s.Tools = []mcp.Tool{{Name: "echo", Raw: json.RawMessage(`{"name":"echo","inputSchema":{"type":"object"}}`)}}
	}
	return s, ok
}
func (p *k2BoundaryPool) Servers() []mcp.PoolServer {
	servers := p.TenantPool.Servers()
	for i := range servers {
		servers[i], _ = p.Server(servers[i].ID)
	}
	return servers
}
func (p *k2BoundaryPool) ClientAdmission(id string) *mcp.Denial {
	return p.TenantPool.(mcp.ClientAdmissionPool).ClientAdmission(id)
}

type k2BoundaryAnswer struct {
	IsError bool                       `json:"isError"`
	Meta    map[string]json.RawMessage `json:"_meta"`
	Denial  mcp.ErrorObject            `json:"-"`
}

func (f *k2BoundaryFixture) call(t *testing.T) k2BoundaryAnswer {
	t.Helper()
	post := func(session, method string, params any) *httptest.ResponseRecorder {
		raw, err := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
		if err != nil {
			t.Fatal(err)
		}
		r := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(raw))
		r.Header.Set("Authorization", "Bearer k2-boundary-synthetic-caller")
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Accept", "application/json, text/event-stream")
		if session != "" {
			r.Header.Set("MCP-Session-Id", session)
		}
		w := httptest.NewRecorder()
		f.srv.HandlePost(w, r)
		return w
	}
	w := post("", "initialize", map[string]any{"protocolVersion": mcp.ProtocolVersion,
		"capabilities": map[string]any{}, "clientInfo": map[string]any{"name": "k2-boundary", "version": "1"}})
	session := w.Header().Get("MCP-Session-Id")
	if session == "" {
		t.Fatalf("initialize failed: HTTP %d", w.Code)
	}
	w = post(session, "tools/call", map[string]any{"name": "service__echo", "arguments": map[string]any{}})
	var envelope struct {
		Result json.RawMessage `json:"result"`
		Error  json.RawMessage `json:"error"`
	}
	if json.Unmarshal(w.Body.Bytes(), &envelope) != nil || len(envelope.Error) > 0 || len(envelope.Result) == 0 {
		t.Fatalf("tools/call protocol failure: HTTP %d", w.Code)
	}
	var answer k2BoundaryAnswer
	if err := json.Unmarshal(envelope.Result, &answer); err != nil {
		t.Fatal(err)
	}
	if raw := answer.Meta[mcp.MetaKeyError]; len(raw) > 0 {
		if err := json.Unmarshal(raw, &answer.Denial); err != nil {
			t.Fatal(err)
		}
	}
	return answer
}
