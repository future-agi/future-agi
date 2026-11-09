package mcp

import (
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"testing"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The pinning tests (M20) run the real list and call chains on an org pool,
// as the wiring installs them (newW3BOrgFixture): org A saved the server
// github, whose upstream lists echo. Each test sets what the pool last listed
// (pinTools) and the org's config, then lists and calls as org A.

const echoV1 = `{"name":"echo","description":"fake echo","inputSchema":{"type":"object"}}`

// pinTool is a listed tool as an upstream sent it, raw bytes kept.
func pinTool(t *testing.T, raw string) Tool {
	t.Helper()
	var tl Tool
	if err := json.Unmarshal([]byte(raw), &tl); err != nil {
		t.Fatal(err)
	}
	return tl
}

func mustRevision(t *testing.T, raw string) string {
	t.Helper()
	rev, err := ToolRevision(json.RawMessage(raw))
	if err != nil {
		t.Fatal(err)
	}
	return rev
}

type pinFixture struct {
	*w3bOrgFixture
	ten   *w3bTenancy
	pool  *w3bPool
	orgUp *fakeUpstream
}

// newPinFixture is org A's pool with github, served through OrgPins (the
// wiring's Deps.Pins) unless pins is false, which keeps the default.
func newPinFixture(t *testing.T, pins bool) *pinFixture {
	t.Helper()
	static := newFakeUpstream(t, "/mcp", nil)
	orgUp := newFakeUpstream(t, "/mcp", nil)
	orgClient := connectFake(t, orgUp, ClientConfig{ServerID: "github"})
	pool := &w3bPool{org: "org-a",
		servers: map[string]PoolServer{"github": {ID: "github", Source: "org", Healthy: true, Tools: []Tool{pinTool(t, echoV1)}}},
		clients: map[string]UpstreamClient{"github": AdaptClient(orgClient)}}
	ten := &w3bTenancy{
		cfgs:  map[string]*tenant.OrgConfig{"org-a": pinConfig(t, `{}`)},
		pools: map[string]TenantPool{"org-a": pool, "": &w3bPool{}},
	}
	f := &pinFixture{w3bOrgFixture: newW3BOrgFixture(t, ten, static), ten: ten, pool: pool, orgUp: orgUp}
	if pins {
		f.SetDeps(Deps{Pins: OrgPins{}})
	}
	f.principal = Principal{Kind: PrincipalKey, KeyID: "ka", OrgID: "org-a"}
	return f
}

// pinConfig is org A's config with github and the given mcp.pinning.
func pinConfig(t *testing.T, pinning string) *tenant.OrgConfig {
	t.Helper()
	return orgConfig(t, `{"mcp":{"enabled":true,"access":{"default":"allow"},"servers":{"github":{"url":"https://org.example/mcp"}},"pinning":`+pinning+`}}`)
}

func pinsJSON(pins map[string]string) string {
	b, _ := json.Marshal(map[string]any{"pins": pins})
	return string(b)
}

// setTools makes raws what the pool last listed for github.
func (f *pinFixture) setTools(t *testing.T, source string, raws ...string) {
	t.Helper()
	tools := make([]Tool, len(raws))
	for i, raw := range raws {
		tools[i] = pinTool(t, raw)
	}
	ps := f.pool.servers["github"]
	ps.Tools, ps.Source = tools, source
	ps.Managed = source == "managed"
	f.pool.servers["github"] = ps
}

// listed returns each listed tool's _meta by name.
func (f *pinFixture) listed(t *testing.T) map[string]map[string]json.RawMessage {
	t.Helper()
	vf := &vsFixture{Server: f.Server, session: f.session}
	resp := decodeResponse(t, vf.post(t, "", MethodToolsList, struct{}{}, nil))
	if resp.Error != nil {
		t.Fatalf("tools/list: %+v", resp.Error)
	}
	var res struct {
		Tools []struct {
			Name string                     `json:"name"`
			Meta map[string]json.RawMessage `json:"_meta"`
		} `json:"tools"`
	}
	if err := json.Unmarshal(resp.Result, &res); err != nil {
		t.Fatal(err)
	}
	out := map[string]map[string]json.RawMessage{}
	for _, tl := range res.Tools {
		out[tl.Name] = tl.Meta
	}
	return out
}

func (f *pinFixture) upstreamCalls() int { return len(f.orgUp.requests(MethodToolsCall)) }

// quarantined asserts an in-band tool_quarantined answer with exactly the
// given metadata (mcp-denial-v1).
func quarantined(t *testing.T, resp *Message, want map[string]any) {
	t.Helper()
	obj := denial(t, resp)
	if obj == nil || obj.Code != "tool_quarantined" {
		t.Fatalf("answer %s, want an in-band tool_quarantined", resp.Result)
	}
	got, _ := json.Marshal(obj.Metadata)
	exp, _ := json.Marshal(want)
	if string(got) != string(exp) {
		t.Fatalf("tool_quarantined metadata = %s, want %s", got, exp)
	}
}

// TestRevisionInListMeta: every tool a virtual server lists carries its
// revision in _meta["io.futureagi/revision"], and a pinned tool at that
// revision is listed and called.
func TestRevisionInListMeta(t *testing.T) {
	f := newPinFixture(t, true)
	rev := mustRevision(t, echoV1)
	f.ten.cfgs["org-a"] = pinConfig(t, pinsJSON(map[string]string{"github/echo": rev}))
	meta, ok := f.listed(t)["github__echo"]
	if !ok {
		t.Fatal("the pinned tool is not listed")
	}
	if got := string(meta[MetaKeyRevision]); got != `"`+rev+`"` {
		t.Fatalf("_meta revision = %s, want %q", got, rev)
	}
	if resp := f.call(t, "github__echo"); resp.Error != nil || denial(t, resp) != nil || f.upstreamCalls() != 1 {
		t.Fatalf("the pinned tool's call: %+v %s, %d upstream calls", resp.Error, resp.Result, f.upstreamCalls())
	}
}

// TestChangedToolQuarantined: after an admin accepted echo, the server
// changes its description, then its input schema. Each time the changed tool
// is not listed, and a call to it is refused with tool_quarantined {reason:
// changed, revision_expected, revision_seen} before it reaches the upstream.
func TestChangedToolQuarantined(t *testing.T) {
	f := newPinFixture(t, true)
	rev1 := mustRevision(t, echoV1)
	f.ten.cfgs["org-a"] = pinConfig(t, pinsJSON(map[string]string{"github/echo": rev1}))
	for _, changed := range []string{
		`{"name":"echo","description":"fake echo, second wording","inputSchema":{"type":"object"}}`,
		`{"name":"echo","description":"fake echo","inputSchema":{"type":"object","properties":{"extra":{"type":"string"}}}}`,
	} {
		f.setTools(t, "org", changed)
		if _, listed := f.listed(t)["github__echo"]; listed {
			t.Fatalf("a changed tool is listed: %s", changed)
		}
		quarantined(t, f.call(t, "github__echo"), map[string]any{
			"reason": "changed", "revision_expected": rev1, "revision_seen": mustRevision(t, changed)})
		if n := f.upstreamCalls(); n != 0 {
			t.Fatalf("a changed tool's call reached the upstream (%d calls)", n)
		}
	}
}

// TestNewToolOnPinnedServerQuarantined: a tool the server adds after its
// tools were accepted is not listed and its call is refused {reason: new};
// the accepted tool is still served.
func TestNewToolOnPinnedServerQuarantined(t *testing.T) {
	f := newPinFixture(t, true)
	rev1 := mustRevision(t, echoV1)
	f.ten.cfgs["org-a"] = pinConfig(t, pinsJSON(map[string]string{"github/echo": rev1}))
	wipe := `{"name":"wipe","description":"A second tool.","inputSchema":{"type":"object"}}`
	f.setTools(t, "org", echoV1, wipe)
	got := f.listed(t)
	if _, listed := got["github__wipe"]; listed {
		t.Fatal("a new tool on a pinned server is listed")
	}
	if _, listed := got["github__echo"]; !listed {
		t.Fatal("the accepted tool is no longer listed")
	}
	quarantined(t, f.call(t, "github__wipe"), map[string]any{"reason": "new", "revision_seen": mustRevision(t, wipe)})
	if n := f.upstreamCalls(); n != 0 {
		t.Fatalf("a new tool's call reached the upstream (%d calls)", n)
	}
}

// TestAcceptedRevisionReleasesTheTool: once the config pins the changed
// revision (an admin accepted it, M21), the tool is listed and called again,
// and the old revision is the one now held.
func TestAcceptedRevisionReleasesTheTool(t *testing.T) {
	f := newPinFixture(t, true)
	v2 := `{"name":"echo","description":"fake echo, v2","inputSchema":{"type":"object"}}`
	f.setTools(t, "org", v2)
	f.ten.cfgs["org-a"] = pinConfig(t, pinsJSON(map[string]string{"github/echo": mustRevision(t, echoV1)}))
	if _, listed := f.listed(t)["github__echo"]; listed {
		t.Fatal("held before the accept")
	}
	f.ten.cfgs["org-a"] = pinConfig(t, pinsJSON(map[string]string{"github/echo": mustRevision(t, v2)}))
	if _, listed := f.listed(t)["github__echo"]; !listed {
		t.Fatal("the accepted revision is not listed")
	}
	if resp := f.call(t, "github__echo"); denial(t, resp) != nil || f.upstreamCalls() != 1 {
		t.Fatalf("the accepted revision's call: %s", resp.Result)
	}
	f.setTools(t, "org", echoV1)
	if _, listed := f.listed(t)["github__echo"]; listed {
		t.Fatal("a revert to the earlier definition is served without an accept")
	}
}

// TestAutoBaselineFirstListing: a server with no pins (one saved without a
// listing) is served as its first listing, its baseline: every tool is
// listed and called, and the admin API reports each tool's revision, none
// held, so Django can pin them (M21).
func TestAutoBaselineFirstListing(t *testing.T) {
	f := newPinFixture(t, true)
	if _, listed := f.listed(t)["github__echo"]; !listed {
		t.Fatal("a server with no pins is not served")
	}
	if resp := f.call(t, "github__echo"); denial(t, resp) != nil {
		t.Fatalf("a call on a server with no pins: %s", resp.Result)
	}
	tools, err := f.AdminTools("org-a", "")
	if err != nil || len(tools.Tools) != 1 {
		t.Fatalf("AdminTools = %+v, %v", tools, err)
	}
	if tl := tools.Tools[0]; tl.Revision == nil || *tl.Revision != mustRevision(t, echoV1) || tl.Quarantined {
		t.Fatalf("admin tool = %+v, want its revision and not quarantined", tl)
	}
	if revs, err := f.AdminRevisions("org-a"); err != nil || len(revs) != 0 {
		t.Fatalf("AdminRevisions on a baseline = %+v, %v; want none", revs, err)
	}
	// With auto_baseline off, a server with no pins is new throughout.
	f.ten.cfgs["org-a"] = pinConfig(t, `{"auto_baseline":false}`)
	if _, listed := f.listed(t)["github__echo"]; listed {
		t.Fatal("auto_baseline off: a server with no pins is served")
	}
}

// TestWarnModeLists: in mode warn a changed tool is listed, marked with
// _meta["io.futureagi/pin"] {reason, revision_expected}, and called.
func TestWarnModeLists(t *testing.T) {
	f := newPinFixture(t, true)
	rev1 := mustRevision(t, echoV1)
	f.ten.cfgs["org-a"] = pinConfig(t, `{"mode":"warn","pins":{"github/echo":"`+rev1+`"}}`)
	f.setTools(t, "org", `{"name":"echo","description":"changed","inputSchema":{"type":"object"}}`)
	meta, listed := f.listed(t)["github__echo"]
	if !listed {
		t.Fatal("warn mode hid a changed tool")
	}
	if got := string(meta["io.futureagi/pin"]); got != `{"reason":"changed","revision_expected":"`+rev1+`"}` {
		t.Fatalf("_meta pin = %s", got)
	}
	if resp := f.call(t, "github__echo"); denial(t, resp) != nil || f.upstreamCalls() != 1 {
		t.Fatalf("warn mode refused the call: %s", resp.Result)
	}
}

// TestOverridesDoNotChangeRevision: a virtual server's override of a tool's
// name and description changes what clients see, never its revision, which
// is the upstream's own definition's: the pinned tool is served under the
// override.
func TestOverridesDoNotChangeRevision(t *testing.T) {
	f := newPinFixture(t, true)
	rev := mustRevision(t, echoV1)
	f.ten.cfgs["org-a"] = orgConfig(t, `{"mcp":{"enabled":true,"servers":{"github":{"url":"https://org.example/mcp"}},
		"pinning":{"pins":{"github/echo":"`+rev+`"}},
		"virtual_servers":{"eng":{"id":"vs_01M4ZEB9E0J3AADR85Z4T4428X","members":["github"],"access":{"default_for_org":true},
		"overrides":{"github/echo":{"name":"say","description":"Says it back."}}}}}}`)
	got := f.listed(t)
	meta, ok := got["say"]
	if !ok {
		t.Fatalf("listed %v, want the overridden name", got)
	}
	if string(meta[MetaKeyRevision]) != `"`+rev+`"` {
		t.Fatalf("the override changed the revision: %s, want %s", meta[MetaKeyRevision], rev)
	}
	if resp := f.call(t, "say"); denial(t, resp) != nil {
		t.Fatalf("the pinned, overridden tool's call: %s", resp.Result)
	}
}

// TestOperatorServersNotPinned: pinning holds only the org's own servers; a
// managed server's tools (the operator's, changed only with a deploy) are
// served whatever the org's pins say. And with the default Deps.Pins (no
// wiring) nothing is pinned.
func TestOperatorServersNotPinned(t *testing.T) {
	f := newPinFixture(t, true)
	f.ten.cfgs["org-a"] = pinConfig(t, pinsJSON(map[string]string{"github/echo": mustRevision(t, `{"name":"echo"}`)}))
	f.setTools(t, "managed", echoV1)
	if _, listed := f.listed(t)["github__echo"]; !listed {
		t.Fatal("a managed server's tool is held by the org's pins")
	}
	unwired := newPinFixture(t, false)
	unwired.ten.cfgs["org-a"] = f.ten.cfgs["org-a"]
	if _, listed := unwired.listed(t)["github__echo"]; !listed {
		t.Fatal("the default Deps.Pins held a tool")
	}
}

// TestRevisionRecordedOnTheCall: the call's record carries the revision of
// the tool it ran (mcp-tool-call-v1 tool_revision).
func TestRevisionRecordedOnTheCall(t *testing.T) {
	f := newPinFixture(t, true)
	var mu sync.Mutex
	var seen string
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookEvidenceMeta {
			mu.Lock()
			seen = cc.Tool.Revision
			mu.Unlock()
		}
	}
	if resp := f.call(t, "github__echo"); denial(t, resp) != nil {
		t.Fatalf("call: %s", resp.Result)
	}
	mu.Lock()
	defer mu.Unlock()
	if seen != mustRevision(t, echoV1) {
		t.Fatalf("the call's revision = %q, want echo's", seen)
	}
}

// TestPendingDiffServed: GET /-/mcp/revisions lists a changed tool with the
// accepted revision, both definitions and a field-level diff (the accepted
// one was listed here first), and a new tool with the fields it arrives with;
// GET /-/mcp/tools marks both quarantined. Each record matches
// mcp-revision-v1, and another org sees none of them.
func TestPendingDiffServed(t *testing.T) {
	f := newPinFixture(t, true)
	rev1 := mustRevision(t, echoV1)
	f.ten.cfgs["org-a"] = pinConfig(t, pinsJSON(map[string]string{"github/echo": rev1}))
	f.listed(t) // the gateway lists the accepted definition
	changed := `{"name":"echo","description":"fake echo, third wording","inputSchema":{"type":"object"}}`
	wipe := `{"name":"wipe","inputSchema":{"type":"object"}}`
	f.setTools(t, "org", changed, wipe)

	revs, err := f.AdminRevisions("org-a")
	if err != nil || len(revs) != 2 {
		t.Fatalf("AdminRevisions = %+v, %v; want echo and wipe", revs, err)
	}
	echo, nw := revs[0], revs[1]
	if echo.Tool != "echo" || echo.Reason != "changed" || echo.Revision != mustRevision(t, changed) ||
		echo.PreviousRevision == nil || *echo.PreviousRevision != rev1 || echo.ServerID != "github" || echo.OrgID != "org-a" {
		t.Fatalf("echo's record = %+v", echo)
	}
	if echo.PreviousDefinition["description"] != "fake echo" || echo.Definition["description"] != "fake echo, third wording" {
		t.Fatalf("echo's definitions = %v / %v", echo.PreviousDefinition, echo.Definition)
	}
	if len(echo.Diff) != 1 || echo.Diff[0].Field != "description" || echo.Diff[0].Before != "fake echo" || echo.Diff[0].After != "fake echo, third wording" {
		t.Fatalf("echo's diff = %s", jsonText(echo.Diff))
	}
	if nw.Tool != "wipe" || nw.Reason != "new" || nw.PreviousRevision != nil || len(nw.Diff) != 2 || nw.Diff[0].Field != "name" || nw.Diff[0].Before != nil {
		t.Fatalf("wipe's record = %s", jsonText(nw))
	}
	for _, r := range revs {
		dec := json.NewDecoder(strings.NewReader(jsonText(r)))
		dec.DisallowUnknownFields()
		var back gatewayadmin.MCPRevision
		if err := dec.Decode(&back); err != nil || back.FirstSeenAt == "" {
			t.Fatalf("%s does not match mcp-revision-v1: %v", jsonText(r), err)
		}
	}
	tools, err := f.AdminTools("org-a", "")
	if err != nil {
		t.Fatal(err)
	}
	for _, tl := range tools.Tools {
		if !tl.Quarantined {
			t.Fatalf("admin tool %s is not marked quarantined", tl.Name)
		}
	}
	if _, err := f.AdminRevisions("org-zzz"); err == nil {
		t.Fatal("an unknown org's revisions were answered")
	}
}

func jsonText(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprint(v)
	}
	return string(b)
}
