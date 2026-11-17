package mcpmock

import (
	"net/http"
	"testing"
	"time"
)

func getStream(t *testing.T, url, sid, lastEventID string) (<-chan sseEvent, *http.Response, func()) {
	t.Helper()
	req, _ := http.NewRequest(http.MethodGet, url, nil)
	req.Header.Set("Accept", "text/event-stream")
	req.Header.Set("Mcp-Session-Id", sid)
	if lastEventID != "" {
		req.Header.Set("Last-Event-ID", lastEventID)
	}
	return sseStream(t, req)
}

// waitFor polls cond until it holds or the deadline passes.
func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

// TestLastEventIDReplaysMissedEvents: a legacy session's GET stream numbers
// its events; after a disconnect, a GET with Last-Event-ID gets exactly the
// events it missed, then live ones.
func TestLastEventIDReplaysMissedEvents(t *testing.T) {
	m, srv := testMock(t, V0618, "none")
	url := srv.URL + "/mcp"
	sid := initLegacy(t, url, V0618, nil)

	events, resp, cancel := getStream(t, url, sid, "")
	if resp.StatusCode != 200 || resp.Header.Get("Content-Type") != "text/event-stream" {
		t.Fatalf("GET stream = %d %s", resp.StatusCode, resp.Header.Get("Content-Type"))
	}
	postControl(t, srv.URL, "/__control/tools", `[{"name":"only_tool"}]`)
	first := nextEvent(t, events, 5*time.Second)
	if first.id != "1" || decodeMessage(t, first.data).Method != "notifications/tools/list_changed" {
		t.Fatalf("first event = %+v, want id 1 list_changed", first)
	}
	cancel()
	waitFor(t, "the stream to detach", func() bool {
		s := m.sessions.get(sid)
		s.mu.Lock()
		defer s.mu.Unlock()
		return s.live == nil
	})

	// Two events while the client is away.
	postControl(t, srv.URL, "/__control/behavior", `{"notify":["resources/list_changed","prompts/list_changed"]}`)

	replay, _, cancel2 := getStream(t, url, sid, first.id)
	defer cancel2()
	got := []string{}
	for _, wantID := range []string{"2", "3"} {
		ev := nextEvent(t, replay, 5*time.Second)
		if ev.id != wantID {
			t.Fatalf("replayed event id %q, want %q", ev.id, wantID)
		}
		got = append(got, decodeMessage(t, ev.data).Method)
	}
	if got[0] != "notifications/resources/list_changed" || got[1] != "notifications/prompts/list_changed" {
		t.Fatalf("replayed %v", got)
	}
	// Live events continue on the resumed stream, numbered on.
	postControl(t, srv.URL, "/__control/behavior", `{"notify":["tools/list_changed"]}`)
	if ev := nextEvent(t, replay, 5*time.Second); ev.id != "4" {
		t.Fatalf("live event after replay has id %q, want 4", ev.id)
	}
}

// TestSessionEndsWithDeleteAndDropSession: DELETE ends a session and a later
// request on it is 404; drop_session does the same to every session; a GET
// without a session is 405.
func TestSessionEndsWithDeleteAndDropSession(t *testing.T) {
	_, srv := testMock(t, V1125, "none")
	url := srv.URL + "/mcp"
	sid := initLegacy(t, url, V1125, nil)
	req, _ := http.NewRequest(http.MethodDelete, url, nil)
	req.Header.Set("Mcp-Session-Id", sid)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("DELETE = %d", resp.StatusCode)
	}
	if r := rpc(t, url, "tools/list", 2, nil, "Mcp-Session-Id: "+sid); r.status != http.StatusNotFound {
		t.Fatalf("a request on a deleted session = %d, want 404", r.status)
	}

	a, b := initLegacy(t, url, V1125, nil), initLegacy(t, url, V1125, nil)
	out := postControl(t, srv.URL, "/__control/behavior", `{"drop_session":true}`)
	if out["dropped_sessions"].(float64) != 2 {
		t.Fatalf("drop_session dropped %v, want 2", out["dropped_sessions"])
	}
	for _, s := range []string{a, b} {
		if r := rpc(t, url, "tools/list", 3, nil, "Mcp-Session-Id: "+s); r.status != http.StatusNotFound {
			t.Fatalf("a dropped session answered %d, want 404", r.status)
		}
	}
	// A new initialize works after the drop (the client's recovery path).
	initLegacy(t, url, V1125, nil)

	req, _ = http.NewRequest(http.MethodGet, url, nil)
	req.Header.Set("Accept", "text/event-stream")
	resp, err = http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Fatalf("GET without a session = %d, want 405", resp.StatusCode)
	}
	if r := rpc(t, url, "tools/list", 4, nil, "Mcp-Session-Id: "+initLegacy(t, url, V1125, nil),
		"MCP-Protocol-Version: "+V0618); r.status != http.StatusBadRequest {
		t.Fatalf("a MCP-Protocol-Version other than the session's = %d, want 400", r.status)
	}
}

// TestPaginationReturnsNextCursor: with page_size armed, every list method
// pages with an opaque nextCursor until the last page, in both eras.
func TestPaginationReturnsNextCursor(t *testing.T) {
	_, srv := testMock(t, "dual", "none")
	url := srv.URL + "/mcp"
	postControl(t, srv.URL, "/__control/behavior", `{"page_size":2}`)

	sid := initLegacy(t, url, V1125, nil)
	page1 := resultMap(t, rpc(t, url, "tools/list", 2, nil, "Mcp-Session-Id: "+sid))
	cursor, _ := page1["nextCursor"].(string)
	if len(page1["tools"].([]any)) != 2 || cursor == "" {
		t.Fatalf("page 1 = %v", page1)
	}
	page2 := resultMap(t, rpc(t, url, "tools/list", 3, map[string]any{"cursor": cursor}, "Mcp-Session-Id: "+sid))
	if len(page2["tools"].([]any)) != 1 || page2["nextCursor"] != nil {
		t.Fatalf("page 2 = %v", page2)
	}
	names := []string{}
	for _, page := range []map[string]any{page1, page2} {
		for _, tool := range page["tools"].([]any) {
			names = append(names, tool.(map[string]any)["name"].(string))
		}
	}
	if len(names) != 3 || names[0] != "echo" || names[1] != "get_weather" || names[2] != "delete_file" {
		t.Fatalf("pages hold %v, want the fixture order", names)
	}
	if r := rpc(t, url, "tools/list", 4, map[string]any{"cursor": "bogus"}, "Mcp-Session-Id: "+sid); r.msg.Error == nil || r.msg.Error.Code != CodeInvalidParams {
		t.Fatalf("a bogus cursor = %s, want -32602", r.body)
	}

	p1 := resultMap(t, call2026(t, url, "tools/list", nil))
	if p1["nextCursor"] == nil || len(p1["tools"].([]any)) != 2 {
		t.Fatalf("2026 page 1 = %v", p1)
	}
	p2 := resultMap(t, call2026(t, url, "tools/list", map[string]any{"cursor": p1["nextCursor"]}))
	if p2["nextCursor"] != nil || len(p2["tools"].([]any)) != 1 {
		t.Fatalf("2026 page 2 = %v", p2)
	}
	// Mock-only fixture keys never reach a listing.
	for _, tool := range p1["tools"].([]any) {
		if _, ok := tool.(map[string]any)["price_usd"]; ok {
			t.Fatalf("price_usd leaked into a tool definition: %v", tool)
		}
	}
}
