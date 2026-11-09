package server

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// TestMCPRevisionsRoute (M20): GET /-/mcp/revisions needs the admin token and
// an org_id (400 without one, 404 for an unknown org) and answers from that
// org's pool only. Org A pinned github's search, so wipe, which github also
// lists, is held as new and reported, and /-/mcp/tools marks it quarantined
// with its revision; org B pinned nothing, so its github is served as its
// baseline and nothing is reported. Each record is mcp-revision-v1.
func TestMCPRevisionsRoute(t *testing.T) {
	a := &adminUpstream{tools: []string{"search", "wipe"}}
	b := &adminUpstream{tools: []string{"search", "wipe"}}
	srv := newTenancyServer(t, map[string]*adminUpstream{"http://a.example": a, "http://b.example": b})
	m := srv.MCP()
	deps := *m.Deps()
	deps.Pins = mcp.OrgPins{} // as initMCPPins wires it
	m.SetDeps(deps)
	searchRev, err := mcp.ToolRevision(json.RawMessage(`{"name":"search","inputSchema":{"type":"object"}}`))
	if err != nil {
		t.Fatal(err)
	}
	putOrg(t, srv, "org-a", `{"mcp":{"enabled":true,"servers":{"github":{"url":"http://a.example"}},"pinning":{"pins":{"github/search":"`+searchRev+`"}}}}`)
	putOrg(t, srv, "org-b", orgWithGithub("http://b.example", ""))

	tools := func(org string) []gatewayadmin.MCPAdminTool {
		var body struct {
			Tools []gatewayadmin.MCPAdminTool `json:"tools"`
		}
		w := serve(srv, "GET", "/-/mcp/tools?org_id="+org, "", "Bearer mcp-admin")
		if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
			t.Fatalf("tools: %s", w.Body.String())
		}
		return body.Tools
	}
	deadline := time.Now().Add(3 * time.Second)
	for len(tools("org-a")) != 2 || len(tools("org-b")) != 2 {
		if time.Now().After(deadline) {
			t.Fatalf("the pools never listed github's tools: %v %v", tools("org-a"), tools("org-b"))
		}
		time.Sleep(10 * time.Millisecond)
	}

	if w := serve(srv, "GET", "/-/mcp/revisions", "", "Bearer mcp-admin"); w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "missing_org_id") {
		t.Errorf("without org_id = %d %s, want 400 missing_org_id", w.Code, w.Body.String())
	}
	if w := serve(srv, "GET", "/-/mcp/revisions?org_id=org-zzz", "", "Bearer mcp-admin"); w.Code != http.StatusNotFound {
		t.Errorf("an unknown org = %d, want 404", w.Code)
	}
	if w := serve(srv, "GET", "/-/mcp/revisions?org_id=org-a", "", ""); w.Code != http.StatusUnauthorized {
		t.Errorf("without the admin token = %d, want 401", w.Code)
	}

	revisions := func(org string) []json.RawMessage {
		w := serve(srv, "GET", "/-/mcp/revisions?org_id="+org, "", "Bearer mcp-admin")
		var body struct {
			Revisions []json.RawMessage `json:"revisions"`
		}
		if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil || w.Code != http.StatusOK || body.Revisions == nil {
			t.Fatalf("revisions for %s = %d %s", org, w.Code, w.Body.String())
		}
		return body.Revisions
	}
	got := revisions("org-a")
	if len(got) != 1 {
		t.Fatalf("org-a revisions = %s, want wipe only", got)
	}
	dec := json.NewDecoder(strings.NewReader(string(got[0])))
	dec.DisallowUnknownFields()
	var rec gatewayadmin.MCPRevision
	if err := dec.Decode(&rec); err != nil {
		t.Fatalf("%s is not mcp-revision-v1: %v", got[0], err)
	}
	if rec.OrgID != "org-a" || rec.ServerID != "github" || rec.Tool != "wipe" || rec.Reason != "new" || rec.PreviousRevision != nil || len(rec.Diff) == 0 {
		t.Fatalf("org-a's record = %s", got[0])
	}
	for _, tl := range tools("org-a") {
		if tl.Revision == nil || len(*tl.Revision) != 64 || tl.Quarantined != (tl.UpstreamName == "wipe") {
			t.Fatalf("org-a admin tool %+v: want a revision, and only wipe quarantined", tl)
		}
	}
	if got := revisions("org-b"); len(got) != 0 {
		t.Fatalf("org-b (no pins: its baseline) revisions = %s, want none", got)
	}
	for _, tl := range tools("org-b") {
		if tl.Quarantined || tl.Revision == nil {
			t.Fatalf("org-b admin tool %+v: want its revision, not quarantined", tl)
		}
	}
}
