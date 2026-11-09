package restmcp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// listPage is one tools/list page.
type listPage struct {
	Tools []struct {
		Name         string          `json:"name"`
		Title        string          `json:"title"`
		Description  string          `json:"description"`
		InputSchema  json.RawMessage `json:"inputSchema"`
		OutputSchema json.RawMessage `json:"outputSchema"`
		Annotations  map[string]any  `json:"annotations"`
	} `json:"tools"`
	NextCursor string `json:"nextCursor"`
}

// listAll walks every tools/list page of the app at path on the legacy era, with
// a bearer and an api key, so an app of either kind answers.
func listAll(t *testing.T, rt *runtimeUnder, path string) (pages []listPage) {
	t.Helper()
	cursor := ""
	for i := 0; i < 100; i++ {
		params := "{}"
		if cursor != "" {
			params = `{"cursor":"` + cursor + `"}`
		}
		r := rt.post(t, path, `{"jsonrpc":"2.0","id":7,"method":"tools/list","params":`+params+`}`,
			"Authorization", "Bearer "+testToken, "X-API-Key", testAPIKey, "MCP-Protocol-Version", "2025-11-25")
		var page listPage
		if r.Status != http.StatusOK || r.Error != nil || json.Unmarshal(r.Result, &page) != nil {
			t.Fatalf("tools/list page %d: HTTP %d, %s", i+1, r.Status, r.Raw)
		}
		pages = append(pages, page)
		if cursor = page.NextCursor; cursor == "" {
			return pages
		}
	}
	t.Fatal("tools/list never ended")
	return nil
}

// TestPaginationAndByteCap: tools/list is served in pages whose cursors are
// bound to the app's list (another app's cursor, or a made-up one, is
// refused), and M13's client follows them to the whole list. A vendor's own
// page token goes out as the tool's input and comes back in the result. A
// result over response.maxBytes keeps valid JSON with as many leading items
// as fit plus a notice; a result with no list to shorten is cut with a
// notice; a vendor answer over the runtime's read limit is refused.
func TestPaginationAndByteCap(t *testing.T) {
	var threads atomic.Int64
	fake := newVendorFake(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/gmail/v1/users/me/threads":
			n := int(threads.Load())
			list := make([]map[string]any, n)
			for i := range list {
				list[i] = map[string]any{"id": fmt.Sprintf("t-%04d", i), "snippet": strings.Repeat("s", 30), "historyId": "1"}
			}
			jsonAnswer(200, map[string]any{"threads": list, "nextPageToken": "after-" + r.URL.Query().Get("pageToken"),
				"resultSizeEstimate": n})(w, r)
		case "/gmail/v1/users/me/messages/big":
			jsonAnswer(200, map[string]any{"id": "big", "threadId": "t-1", "labelIds": []string{"INBOX"},
				"payload": map[string]any{"body": map[string]any{"data": strings.Repeat("A", 150_000)}}})(w, r)
		case "/gmail/v1/users/me/messages/huge":
			jsonAnswer(200, map[string]any{"id": "huge", "raw": strings.Repeat("B", 300_000)})(w, r)
		default:
			jsonAnswer(404, map[string]any{"error": map[string]any{"message": "Not Found"}})(w, r)
		}
	})
	cat := cn4Catalog(t)
	gmail, calendar := compileOne(t, cat, "gmail"), compileOne(t, cat, "google-calendar")
	rt := startRuntime(t, []*App{gmail, calendar}, map[string]string{gmailOrigin: fake.URL},
		func(o *Options) { o.PageSize = 3; o.MaxVendorBytes = 256 << 10 })

	// tools/list pages.
	pages := listAll(t, rt, "/gmail/mcp")
	var names []string
	for _, p := range pages {
		if len(p.Tools) > 3 {
			t.Errorf("a page holds %d tools, more than the page size 3", len(p.Tools))
		}
		for _, tool := range p.Tools {
			names = append(names, tool.Name)
		}
	}
	var want []string
	for _, mt := range cat.App("gmail").Providers[0].AllTools() {
		want = append(want, mt.Name)
	}
	if len(pages) != 4 || strings.Join(names, ",") != strings.Join(want, ",") {
		t.Errorf("%d pages listing %v, want 4 pages listing the manifest's %v in order", len(pages), names, want)
	}
	calendarCursor := listAll(t, rt, "/google-calendar/mcp")[0].NextCursor
	for name, cursor := range map[string]string{"another app's": calendarCursor, "a made-up": "djE6MDAwMDowOjk5",
		"a non-base64": "%%%", "a past-the-end": gmail.cursor(len(want))} {
		r := rt.legacyCall(t, "/gmail/mcp", testToken, "tools/list", `{"cursor":"`+cursor+`"}`)
		if r.Error == nil || r.Error.Code != codeInvalidParams {
			t.Errorf("%s cursor: %s, want -32602 invalid cursor", name, r.Raw)
		}
	}
	meta := `"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}`
	r := rt.post(t, "/gmail/mcp", `{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{"cursor":"`+pages[1].NextCursor+`",`+meta+`}}`,
		"Authorization", "Bearer "+testToken, "MCP-Protocol-Version", "2026-07-28", "Mcp-Method", "tools/list")
	var third listPage
	if json.Unmarshal(r.Result, &third) != nil || len(third.Tools) != 3 || third.Tools[0].Name != want[6] {
		t.Errorf("a 2026 tools/list with the second page's cursor: %s", r.Raw)
	}
	c := mcp.NewClient(mcp.ClientConfig{ServerID: "gmail", URL: rt.URL + "/gmail/mcp", TransportType: "http",
		Auth: mcp.AuthConfig{Type: "bearer", Token: testToken}, Era: "2025-11-25"})
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if err := c.Connect(ctx); err != nil {
		t.Fatalf("Connect: %v", err)
	}
	if c.ToolCount() != len(want) {
		t.Errorf("M13's client listed %d tools over 3-tool pages, want %d", c.ToolCount(), len(want))
	}
	c.Close()

	// The vendor's page token, out and back.
	threads.Store(2)
	res := rt.callTool(t, "/gmail/mcp", testToken, "search_threads", map[string]any{"q": "is:unread", "pageToken": "p-7"})
	if res.IsError || !strings.Contains(res.text(), `"nextPageToken":"after-p-7"`) {
		t.Errorf("search_threads with a page token: %q", res.text())
	}
	if got := fake.requests(); got[len(got)-1].Query != "pageToken=p-7&q=is%3Aunread" {
		t.Errorf("the vendor got query %q", got[len(got)-1].Query)
	}

	// response.maxBytes (search_threads: 25,000) over a long list.
	threads.Store(3000)
	res = rt.callTool(t, "/gmail/mcp", testToken, "search_threads", map[string]any{"q": "in:anywhere"})
	if res.IsError || len(res.Content) != 2 {
		t.Fatalf("a long list: isError %v, %d blocks, want the shortened list and a notice", res.IsError, len(res.Content))
	}
	var kept struct {
		Threads       []map[string]any `json:"threads"`
		NextPageToken string           `json:"nextPageToken"`
	}
	if err := json.Unmarshal([]byte(res.Content[0].Text), &kept); err != nil {
		t.Fatalf("the shortened list is not JSON: %v", err)
	}
	total := len(res.Content[0].Text) + len(res.Content[1].Text)
	if len(kept.Threads) < 100 || len(kept.Threads) >= 3000 || total > 25_000 || kept.NextPageToken == "" {
		t.Errorf("kept %d threads in %d bytes (nextPageToken %q), want a prefix within 25,000 bytes", len(kept.Threads), total, kept.NextPageToken)
	}
	if note := res.Content[1].Text; !strings.Contains(note, fmt.Sprintf("keeps the first %d of 3000 items", len(kept.Threads))) ||
		!strings.Contains(note, "response.maxBytes") {
		t.Errorf("the notice: %q", note)
	}
	for _, field := range []string{"historyId"} {
		if _, has := kept.Threads[0][field]; has {
			t.Errorf("response.fields kept %s, which the tool does not project", field)
		}
	}

	// No list to shorten: get_message (maxBytes 100,000) with a large body.
	res = rt.callTool(t, "/gmail/mcp", testToken, "get_message", map[string]any{"id": "big"})
	if res.IsError || len(res.Content) != 1 || len(res.Content[0].Text) > 100_000 ||
		!strings.Contains(res.Content[0].Text, "[truncated: the result is ") {
		t.Errorf("a large object: isError %v, %d blocks, %d bytes", res.IsError, len(res.Content), len(res.text()))
	}

	// The read limit (256 KiB here): the answer is refused, not cut.
	res = rt.callTool(t, "/gmail/mcp", testToken, "get_message", map[string]any{"id": "huge"})
	if !res.IsError || !strings.Contains(res.text(), "more than 262144 bytes") {
		t.Errorf("an answer over the read limit: %q", res.text())
	}
	if !strings.Contains(rt.logs.String(), `"outcome":"too_large"`) {
		t.Error("the refused answer was not logged as too_large")
	}
}

// TestVendorErrorBecomesIsError: a vendor error is a tools/call result with
// isError (never a JSON-RPC error), naming the vendor, the host, the status
// and the vendor's own message, in Google's, Microsoft Graph's and Adyen's
// shapes, Slack's ok: false at HTTP 200, an HTML 500, an unreachable vendor;
// _meta attributes it. Retries follow the provider's retry block: a 429 is
// retried for any tool, a 503 only for an idempotent one, a Retry-After
// longer than a call holds is returned instead.
func TestVendorErrorBecomesIsError(t *testing.T) {
	var labelCalls atomic.Int64
	google := newVendorFake(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/gmail/v1/users/me/threads/missing":
			jsonAnswer(404, map[string]any{"error": map[string]any{"code": 404, "message": "Requested entity was not found.",
				"status": "NOT_FOUND"}})(w, r)
		case "/gmail/v1/users/me/labels":
			if labelCalls.Add(1) <= 2 {
				w.Header().Set("Retry-After", "0")
				jsonAnswer(429, map[string]any{"error": map[string]any{"code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}})(w, r)
				return
			}
			jsonAnswer(200, map[string]any{"labels": []map[string]any{{"id": "INBOX", "name": "INBOX", "type": "system"}}})(w, r)
		case "/gmail/v1/users/me/profile":
			w.Header().Set("Retry-After", "0")
			jsonAnswer(429, map[string]any{"error": map[string]any{"code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}})(w, r)
		case "/gmail/v1/users/me/threads/slow":
			w.Header().Set("Retry-After", "60")
			jsonAnswer(503, map[string]any{"error": map[string]any{"code": 503, "message": "Backend busy"}})(w, r)
		case "/gmail/v1/users/me/messages/send":
			jsonAnswer(503, map[string]any{"error": map[string]any{"code": 503, "message": "Backend Error", "status": "UNAVAILABLE"}})(w, r)
		case "/gmail/v1/users/me/messages/html":
			w.Header().Set("Content-Type", "text/html")
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = w.Write([]byte("<html><body>Internal error</body></html>"))
		default:
			jsonAnswer(404, map[string]any{})(w, r)
		}
	})
	graph := newVendorFake(t, jsonAnswer(404, map[string]any{"error": map[string]any{"code": "ErrorItemNotFound",
		"message": "The specified object was not found in the store."}}))
	slack := newVendorFake(t, jsonAnswer(200, map[string]any{"ok": false, "error": "channel_not_found"}))
	adyen := newVendorFake(t, jsonAnswer(422, map[string]any{"status": 422, "errorCode": "30_102", "title": "Invalid transfer",
		"detail": "Balance account not found"}))
	cat := cn4Catalog(t)
	rt := startRuntime(t, []*App{compileOne(t, cat, "gmail"), compileOne(t, cat, "microsoft-outlook"), compileOne(t, cat, "slack"),
		compileOne(t, cat, "adyen")}, map[string]string{gmailOrigin: google.URL, "https://graph.microsoft.com": graph.URL,
		"https://slack.com": slack.URL, adyenOrigin: adyen.URL})
	countTo := func(path string) int {
		n := 0
		for _, rq := range google.requests() {
			if rq.Path == path {
				n++
			}
		}
		return n
	}
	attributed := func(res callResult, name, host string, status int) {
		t.Helper()
		var a attribution
		if err := json.Unmarshal(res.Meta[MetaVendor], &a); err != nil || a.Name != name || a.Host != host || a.Status != status {
			t.Errorf("_meta[%s] = %s, want {%s %s %d}", MetaVendor, res.Meta[MetaVendor], name, host, status)
		}
	}
	errorCase := func(path, tool string, args any, wantText string) callResult {
		t.Helper()
		params, _ := json.Marshal(map[string]any{"name": tool, "arguments": args})
		r := rt.post(t, path, `{"jsonrpc":"2.0","id":5,"method":"tools/call","params":`+string(params)+`}`,
			"Authorization", "Bearer "+testToken, "X-API-Key", testAPIKey, "MCP-Protocol-Version", "2025-11-25")
		if r.Error != nil {
			t.Fatalf("%s: a JSON-RPC error %+v, want a result with isError", tool, r.Error)
		}
		res := decodeCall(t, r)
		if !res.IsError || !strings.Contains(res.text(), wantText) {
			t.Errorf("%s: isError %v, %q; want %q", tool, res.IsError, res.text(), wantText)
		}
		return res
	}

	res := errorCase("/gmail/mcp", "get_thread", map[string]any{"id": "missing"},
		"Google API error from gmail.googleapis.com: HTTP 404 Not Found: NOT_FOUND: Requested entity was not found.")
	attributed(res, "Google", "gmail.googleapis.com", 404)
	res = errorCase("/microsoft-outlook/mcp", "get_message", map[string]any{"message-id": "AAMk"},
		"Microsoft API error from graph.microsoft.com: HTTP 404 Not Found: ErrorItemNotFound: The specified object was not found in the store.")
	attributed(res, "Microsoft", "graph.microsoft.com", 404)
	res = errorCase("/slack/mcp", "get_conversation", map[string]any{"channel": "C0"},
		"Slack (slack.com) answered HTTP 200 with ok: false: channel_not_found")
	attributed(res, "Slack", "slack.com", 200)
	errorCase("/adyen/mcp", "get_transfer", map[string]any{"id": "tr-1"},
		"Adyen API error from balanceplatform-api-test.adyen.com: HTTP 422 Unprocessable Entity: 30_102: Invalid transfer: Balance account not found")
	errorCase("/gmail/mcp", "get_message", map[string]any{"id": "html"},
		"Google API error from gmail.googleapis.com: HTTP 500 Internal Server Error: <html><body>Internal error</body></html>")

	// A 429 is retried for any tool and the third answer is the result.
	res = rt.callTool(t, "/gmail/mcp", testToken, "list_labels", map[string]any{})
	if res.IsError || countTo("/gmail/v1/users/me/labels") != 3 {
		t.Errorf("two 429s then a 200: isError %v after %d calls, want the 200 after 3", res.IsError, countTo("/gmail/v1/users/me/labels"))
	}
	res = errorCase("/gmail/mcp", "get_profile", map[string]any{}, "HTTP 429 Too Many Requests: RESOURCE_EXHAUSTED: Quota exceeded (Retry-After: 0)")
	if n := countTo("/gmail/v1/users/me/profile"); n != maxAttempts {
		t.Errorf("a 429 every time: %d calls, want %d", n, maxAttempts)
	}
	attributed(res, "Google", "gmail.googleapis.com", 429)
	if !strings.Contains(rt.logs.String(), `"attempts":3`) {
		t.Error("the retried calls' attempts were not logged")
	}
	// A 503 on a send is not retried: the vendor may have sent it.
	errorCase("/gmail/mcp", "send_message", map[string]any{"body": map[string]any{"raw": "UmF3"}}, "HTTP 503 Service Unavailable")
	if n := countTo("/gmail/v1/users/me/messages/send"); n != 1 {
		t.Errorf("a 503 on send_message: %d calls, want 1", n)
	}
	// A wait longer than a call holds is returned to the caller.
	errorCase("/gmail/mcp", "get_thread", map[string]any{"id": "slow"}, "(Retry-After: 60)")
	if n := countTo("/gmail/v1/users/me/threads/slow"); n != 1 {
		t.Errorf("Retry-After: 60: %d calls, want 1", n)
	}

	down := startRuntime(t, []*App{compileOne(t, cat, "gmail")}, map[string]string{gmailOrigin: closedOrigin(t)})
	res = down.callTool(t, "/gmail/mcp", testToken, "get_profile", map[string]any{})
	if !res.IsError || res.text() != "gmail.googleapis.com could not be reached" {
		t.Errorf("an unreachable vendor: %q", res.text())
	}
	attributed(res, "Google", "gmail.googleapis.com", 0)
}

// TestAnnotationsFromManifest: tools/list serves each tool's four
// annotations exactly as the manifest declares them (false values
// included), its title, description and schemas, in the manifest's order,
// for every app of CN4's example catalog and on both eras; the gateway's
// client reads them as declared; and a change to a manifest's annotations
// changes what is served, so nothing is derived or fixed in the runtime.
func TestAnnotationsFromManifest(t *testing.T) {
	cat := cn4Catalog(t)
	apps, err := Compile(cat)
	if err != nil {
		t.Fatal(err)
	}
	rt := startRuntime(t, apps, nil)
	hints := []string{"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
	declared := func(a manifest.Annotations) map[string]bool {
		return map[string]bool{"readOnlyHint": a.ReadOnlyHint, "destructiveHint": a.DestructiveHint,
			"idempotentHint": a.IdempotentHint, "openWorldHint": a.OpenWorldHint}
	}
	seen := map[bool]int{}
	for _, a := range cat.Apps {
		mts := a.Providers[0].AllTools()
		var listed []string
		for _, page := range listAll(t, rt, "/"+a.ID+"/mcp") {
			for _, tool := range page.Tools {
				listed = append(listed, tool.Name)
				var mt *manifest.Tool
				for i := range mts {
					if mts[i].Name == tool.Name {
						mt = &mts[i]
					}
				}
				if mt == nil {
					t.Fatalf("%s lists %s, which its manifest does not declare", a.ID, tool.Name)
				}
				for hint, want := range declared(mt.Annotations) {
					got, present := tool.Annotations[hint]
					if !present || got != want {
						t.Errorf("%s/%s %s = %v (present %v), manifest says %v", a.ID, tool.Name, hint, got, present, want)
					}
					seen[want]++
				}
				if len(tool.Annotations) != len(hints)+map[bool]int{true: 1}[mt.Title != ""] ||
					(mt.Title != "" && tool.Annotations["title"] != mt.Title) {
					t.Errorf("%s/%s annotations %v, want exactly the four hints and the title %q", a.ID, tool.Name, tool.Annotations, mt.Title)
				}
				if tool.Title != mt.Title || tool.Description != mt.Description {
					t.Errorf("%s/%s title or description differs from the manifest", a.ID, tool.Name)
				}
				if strings.Contains(string(tool.InputSchema), `"x-`) || strings.Contains(string(tool.OutputSchema), `"x-`) {
					t.Errorf("%s/%s lists a generator annotation", a.ID, tool.Name)
				}
				if (len(mt.OutputSchema) > 0) != (len(tool.OutputSchema) > 0) {
					t.Errorf("%s/%s outputSchema served %v, declared %v", a.ID, tool.Name, len(tool.OutputSchema) > 0, len(mt.OutputSchema) > 0)
				}
			}
		}
		var want []string
		for _, mt := range mts {
			want = append(want, mt.Name)
		}
		if strings.Join(listed, ",") != strings.Join(want, ",") {
			t.Errorf("%s lists %v, want the manifest's order %v", a.ID, listed, want)
		}
	}
	if seen[true] == 0 || seen[false] == 0 {
		t.Errorf("the catalog exercised only one hint value: %v", seen)
	}

	// The 2026 era and the gateway's client read the same hints.
	c := mcp.NewClient(mcp.ClientConfig{ServerID: "gmail", URL: rt.URL + "/gmail/mcp", TransportType: "http",
		Auth: mcp.AuthConfig{Type: "bearer", Token: testToken}, Era: "2026-07-28"})
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if err := c.Connect(ctx); err != nil {
		t.Fatalf("Connect: %v", err)
	}
	defer c.Close()
	for _, tool := range c.Tools() {
		mt := toolOf(cat.App("gmail").Providers[0], tool.Name)
		an := tool.Annotations
		if an == nil || an.ReadOnlyHint == nil || an.DestructiveHint == nil || an.IdempotentHint == nil || an.OpenWorldHint == nil {
			t.Fatalf("%s: the client read annotations %+v, want all four", tool.Name, an)
		}
		got := manifest.Annotations{ReadOnlyHint: *an.ReadOnlyHint, DestructiveHint: *an.DestructiveHint,
			IdempotentHint: *an.IdempotentHint, OpenWorldHint: *an.OpenWorldHint}
		if got != mt.Annotations || an.Title != mt.Title {
			t.Errorf("%s: the client read %+v %q, the manifest says %+v %q", tool.Name, got, an.Title, mt.Annotations, mt.Title)
		}
	}

	// Change the manifest: the served hints follow.
	a, p := gmailCopy(t)
	trash := toolNamed(t, p, "trash_message")
	trash.Annotations = manifest.Annotations{ReadOnlyHint: true, DestructiveHint: false, IdempotentHint: true, OpenWorldHint: true}
	trash.Title = "Changed title"
	changed, err := Compile(&manifest.Catalog{Apps: []*manifest.App{a}})
	if err != nil {
		t.Fatal(err)
	}
	rt2 := startRuntime(t, changed, nil)
	for _, page := range listAll(t, rt2, "/gmail/mcp") {
		for _, tool := range page.Tools {
			if tool.Name != "trash_message" {
				continue
			}
			want := map[string]any{"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": true,
				"title": "Changed title"}
			if fmt.Sprint(tool.Annotations) != fmt.Sprint(want) {
				t.Errorf("after the manifest change trash_message lists %v, want %v", tool.Annotations, want)
			}
		}
	}
}

func toolOf(p *manifest.Provider, name string) manifest.Tool {
	for _, mt := range p.AllTools() {
		if mt.Name == name {
			return mt
		}
	}
	return manifest.Tool{}
}

// TestVendorClientReusesConnections: concurrent calls to one vendor host
// reuse idle connections: the vendor sees about one connection per
// concurrent caller, not one per call.
func TestVendorClientReusesConnections(t *testing.T) {
	var conns atomic.Int64
	fake := newVendorFake(t, jsonAnswer(200, map[string]any{"emailAddress": "someone@example.test"}))
	fake.Config.ConnState = func(_ net.Conn, s http.ConnState) {
		if s == http.StateNew {
			conns.Add(1)
		}
	}
	rt := startRuntime(t, []*App{compileOne(t, cn4Catalog(t), "gmail")}, map[string]string{gmailOrigin: fake.URL})
	const workers, rounds = 8, 10
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < rounds; i++ {
				rt.callTool(t, "/gmail/mcp", testToken, "get_profile", map[string]any{})
			}
		}()
	}
	wg.Wait()
	if n := len(fake.requests()); n != workers*rounds {
		t.Fatalf("the vendor got %d calls, want %d", n, workers*rounds)
	}
	// Ideally one connection per caller; a call can start just before a
	// finished one returns its connection to the pool, so allow some slack.
	// Go's default (two idle per host) opens dozens here.
	if n := conns.Load(); n > 2*workers {
		t.Errorf("%d calls from %d concurrent callers opened %d vendor connections; idle connections were not reused", workers*rounds, workers, n)
	}
}

// TestVendorTimeoutIsAnIsError: a vendor that accepts the call and then
// stalls past the vendor timeout (--vendor-timeout) gets an isError naming
// the host and the bound, attributed, logged as transport_error, and is not
// retried (the vendor may have acted).
func TestVendorTimeoutIsAnIsError(t *testing.T) {
	stall := newVendorFake(t, func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
		case <-time.After(2 * time.Second):
		}
		jsonAnswer(200, map[string]any{"emailAddress": "late@example.test"})(w, r)
	})
	rt := startRuntime(t, []*App{compileOne(t, cn4Catalog(t), "gmail")}, map[string]string{gmailOrigin: stall.URL},
		func(o *Options) { o.VendorTimeout = 50 * time.Millisecond })
	start := time.Now()
	res := rt.callTool(t, "/gmail/mcp", testToken, "get_profile", map[string]any{})
	if !res.IsError || res.text() != "gmail.googleapis.com did not answer within 50ms" {
		t.Errorf("a stalled vendor: isError %v, %q", res.IsError, res.text())
	}
	if took := time.Since(start); took > time.Second {
		t.Errorf("the call took %s; the 50ms vendor timeout did not bound it", took)
	}
	var a attribution
	if err := json.Unmarshal(res.Meta[MetaVendor], &a); err != nil || a.Host != "gmail.googleapis.com" {
		t.Errorf("_meta[%s] = %s", MetaVendor, res.Meta[MetaVendor])
	}
	if n := len(stall.requests()); n != 1 {
		t.Errorf("the stalled vendor got %d requests, want 1 (no retry)", n)
	}
	if !strings.Contains(rt.logs.String(), `"outcome":"transport_error"`) {
		t.Errorf("not logged as transport_error: %s", rt.logs.String())
	}
}

// TestVendorTimeoutIsTheOnlyBound: the vendor client the runtime builds on
// the egress guard's client has no bound of its own on a vendor's answer,
// so --vendor-timeout (do's per-attempt context) alone decides how long a
// call waits, above the guard's 30s wait for headers too.
func TestVendorTimeoutIsTheOnlyBound(t *testing.T) {
	for _, timeout := range []time.Duration{time.Second, DefaultVendorTimeout, 5 * time.Minute} {
		vc, err := newVendorClient(Options{VendorTimeout: timeout})
		if err != nil {
			t.Fatal(err)
		}
		tr, ok := vc.http.Transport.(*http.Transport)
		if !ok {
			t.Fatalf("the vendor client's transport is a %T", vc.http.Transport)
		}
		if vc.timeout != timeout || vc.http.Timeout != 0 || tr.ResponseHeaderTimeout != 0 {
			t.Errorf("--vendor-timeout %s: the call is bounded at %s, but the client at %s and its transport waits %s for headers",
				timeout, vc.timeout, vc.http.Timeout, tr.ResponseHeaderTimeout)
		}
	}
}

// TestVendorSlowerThan30sWithinTheTimeout: a vendor that answers after 31s
// (past the egress guard's own 30s header wait) on a runtime with a 45s
// vendor timeout gets its answer through. It takes 31s, so -short skips it.
func TestVendorSlowerThan30sWithinTheTimeout(t *testing.T) {
	if testing.Short() {
		t.Skip("takes 31s")
	}
	t.Parallel()
	slow := newVendorFake(t, func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
			return
		case <-time.After(31 * time.Second):
		}
		jsonAnswer(200, map[string]any{"emailAddress": "slow@example.test"})(w, r)
	})
	rt := startRuntime(t, []*App{compileOne(t, cn4Catalog(t), "gmail")}, map[string]string{gmailOrigin: slow.URL},
		func(o *Options) { o.VendorTimeout = 45 * time.Second })
	res := rt.callTool(t, "/gmail/mcp", testToken, "get_profile", map[string]any{})
	if res.IsError || !strings.Contains(res.text(), "slow@example.test") {
		t.Errorf("a vendor answering at 31s under a 45s vendor timeout: isError %v, %q", res.IsError, res.text())
	}
}

// fakeTimeout is a net.Error that timed out, as a dial does.
type fakeTimeout struct{}

func (fakeTimeout) Error() string   { return "dial tcp 192.0.2.1:443: i/o timeout" }
func (fakeTimeout) Timeout() bool   { return true }
func (fakeTimeout) Temporary() bool { return true }

// TestTransportFailureNamesTheBoundThatRanOut: "did not answer within" is
// said only when the vendor timeout ran out; a bound inside the call that
// ran out first is named as that (connecting, before a connection was
// made), never as the vendor timeout. No message holds the URL or the
// address.
func TestTransportFailureNamesTheBoundThatRanOut(t *testing.T) {
	expired, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancel()
	cancelled, stop := context.WithCancel(context.Background())
	stop()
	wrap := func(err error) error { return &url.Error{Op: "Get", URL: "https://h.example/secret-path", Err: err} }
	for _, c := range []struct {
		name      string
		ctx       context.Context
		connected bool
		err       error
		want      string
	}{
		{"the vendor timeout", expired, true, wrap(context.DeadlineExceeded), "h.example did not answer within 1m0s"},
		{"the vendor timeout while connecting", expired, false, wrap(context.DeadlineExceeded), "h.example did not answer within 1m0s"},
		{"a TLS handshake bound", context.Background(), false, wrap(context.DeadlineExceeded),
			"h.example did not accept a connection within " + connectTimeout.String()},
		{"a dial timeout", context.Background(), false, wrap(&net.OpError{Op: "dial", Net: "tcp", Err: fakeTimeout{}}),
			"h.example did not accept a connection within " + connectTimeout.String()},
		{"a timeout once connected", context.Background(), true, wrap(&net.OpError{Op: "read", Net: "tcp", Err: fakeTimeout{}}),
			"the connection to h.example timed out"},
		{"a cancelled call", cancelled, true, wrap(context.Canceled), "the call was cancelled"},
		{"a refused connection", context.Background(), false, wrap(&net.OpError{Op: "dial", Net: "tcp", Err: errors.New("connection refused")}),
			"h.example could not be reached"},
	} {
		outcome, failure := describeTransport(c.ctx, "h.example", time.Minute, c.connected, c.err)
		if outcome != outcomeTransport || failure != c.want {
			t.Errorf("%s: %s, %q; want %q", c.name, outcome, failure, c.want)
		}
	}
}

// TestRetryAfterBounds: Retry-After is delay-seconds (digits only) or an
// HTTP-date. A delay too long for a time.Duration is still a long wait, so
// it is returned to the caller, never retried at once; anything else is no
// Retry-After.
func TestRetryAfterBounds(t *testing.T) {
	now := time.Date(2026, 10, 21, 12, 0, 0, 0, time.UTC)
	longest := time.Duration(math.MaxInt64)
	for _, c := range []struct {
		value string
		want  time.Duration
		named bool
	}{
		{"", 0, false},
		{"0", 0, true},
		{" 5 ", 5 * time.Second, true},
		{"60", time.Minute, true},
		{"9223372036", 9223372036 * time.Second, true},
		{"9223372037", longest, true},
		{"9999999999", longest, true},
		{"99999999999999999", longest, true},
		{"99999999999999999999999999", longest, true},
		{"-5", 0, false},
		{"+5", 0, false},
		{"5s", 0, false},
		{"Wed, 21 Oct 2026 12:00:30 GMT", 30 * time.Second, true},
		{"Wed, 21 Oct 2026 11:00:00 GMT", 0, true},
	} {
		d, named := retryAfter(c.value, now)
		if d != c.want || named != c.named {
			t.Errorf("Retry-After %q: %s, %v; want %s, %v", c.value, d, named, c.want, c.named)
		}
	}
	p := retryPolicy{on: []int{429}, honorRetryAfter: true}
	for value, wantOK := range map[string]bool{"3": true, "60": false, "9999999999": false, "99999999999999999999999999": false} {
		h := http.Header{"Retry-After": {value}}
		if wait, ok := p.wait(h, 1, time.Millisecond); ok != wantOK || wait < 0 {
			t.Errorf("Retry-After %q: wait %s, ok %v; want ok %v and no negative wait", value, wait, ok, wantOK)
		}
	}
}
