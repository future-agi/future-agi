package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/contentpolicy"
)

type wp03R2ListKind struct {
	method, key, item string
	refresh           func(*Client, context.Context, Transport) error
}

func wp03R2Kinds() []wp03R2ListKind {
	return []wp03R2ListKind{
		{MethodToolsList, "tools", `{"name":"%s","inputSchema":{},"extension":{"kept":true}}`, (*Client).discoverTools},
		{MethodResourcesList, "resources", `{"name":"%s","uri":"synthetic://resource","extension":{"kept":true}}`, (*Client).discoverResources},
		{MethodPromptsList, "prompts", `{"name":"%s","arguments":[],"extension":{"kept":true}}`, (*Client).discoverPrompts},
	}
}

func wp03R2Page(key, value string, index, total int) string {
	cursor := ""
	if index+1 < total {
		cursor = fmt.Sprintf(`,"nextCursor":"synthetic-page-%d"`, index+1)
	}
	return `{"` + key + `":` + value + cursor + `}`
}

func wp03R2Pages(kind wp03R2ListKind) []string {
	pages := make([]string, 3)
	for i := range pages {
		item := fmt.Sprintf(kind.item, fmt.Sprintf("synthetic-new-%d", i))
		pages[i] = wp03R2Page(kind.key, "["+item+"]", i, len(pages))
	}
	return pages
}

func wp03R3CursorPage(kind wp03R2ListKind, index int, cursor string) string {
	item := fmt.Sprintf(kind.item, fmt.Sprintf("synthetic-new-%d", index))
	return `{"` + kind.key + `":[` + item + `],"nextCursor":` + cursor + `}`
}

// This fixture follows the real Client exchange/send/listAll/discovery path.
// Checking before every response detects publication before the final page.
type wp03R2Transport struct {
	t      *testing.T
	method string
	pages  []string
	before func()
	calls  int
}

func (tr *wp03R2Transport) Send(_ context.Context, req *Message) (*Message, error) {
	tr.t.Helper()
	if req.Method != tr.method {
		tr.t.Fatalf("method = %q, want %q", req.Method, tr.method)
	}
	var params struct {
		Cursor string `json:"cursor"`
	}
	if len(req.Params) > 0 {
		if err := json.Unmarshal(req.Params, &params); err != nil {
			tr.t.Fatal(err)
		}
	}
	want := ""
	if tr.calls > 0 {
		want = fmt.Sprintf("synthetic-page-%d", tr.calls)
	}
	if params.Cursor != want {
		tr.t.Fatalf("cursor = %q, want %q", params.Cursor, want)
	}
	if tr.before != nil {
		tr.before()
	}
	if tr.calls >= len(tr.pages) {
		tr.t.Fatal("fetched beyond the final synthetic page")
	}
	raw := tr.pages[tr.calls]
	tr.calls++
	return &Message{JSONRPC: "2.0", ID: req.ID, Result: json.RawMessage(raw)}, nil
}
func (*wp03R2Transport) Close() error  { return nil }
func (*wp03R2Transport) Healthy() bool { return true }

func wp03R2Cached(c *Client, key string) []json.RawMessage {
	var out []json.RawMessage
	switch key {
	case "tools":
		for _, v := range c.Tools() {
			out = append(out, v.Raw)
		}
	case "resources":
		for _, v := range c.Resources() {
			out = append(out, v.Raw)
		}
	case "prompts":
		for _, v := range c.Prompts() {
			out = append(out, v.Raw)
		}
	}
	return out
}

func wp03R2SameRaw(a, b []json.RawMessage) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if string(a[i]) != string(b[i]) {
			return false
		}
	}
	return true
}

func wp03R2Seed(t *testing.T, c *Client) {
	t.Helper()
	for _, kind := range wp03R2Kinds() {
		page := wp03R2Page(kind.key, "["+fmt.Sprintf(kind.item, "synthetic-old")+"]", 0, 1)
		if err := kind.refresh(c, context.Background(), &wp03Transport{replies: []string{page}}); err != nil {
			t.Fatalf("seed %s: %v", kind.key, err)
		}
	}
}

// R3 extends the accepted R2 table without replacing its cache/stop assertions.
func TestWP03R2EveryListPageRejectsMalformed(t *testing.T) {
	cases := []struct {
		name  string
		value func(wp03R2ListKind, int) string
		want  error
	}{
		{"empty-object", func(_ wp03R2ListKind, _ int) string { return `{}` }, contentpolicy.ErrShape},
		{"missing-member", func(kind wp03R2ListKind, i int) string {
			other := "tools"
			if kind.key == other {
				other = "resources"
			}
			return wp03R2Page(other, "[]", i, 3)
		}, contentpolicy.ErrShape},
		{"null-member", func(kind wp03R2ListKind, i int) string { return wp03R2Page(kind.key, `null`, i, 3) }, contentpolicy.ErrShape},
		{"wrong-object", func(kind wp03R2ListKind, i int) string { return wp03R2Page(kind.key, `{}`, i, 3) }, contentpolicy.ErrShape},
		{"wrong-string", func(kind wp03R2ListKind, i int) string { return wp03R2Page(kind.key, `"synthetic"`, i, 3) }, contentpolicy.ErrShape},
		{"wrong-number", func(kind wp03R2ListKind, i int) string { return wp03R2Page(kind.key, `7`, i, 3) }, contentpolicy.ErrShape},
		{"wrong-bool", func(kind wp03R2ListKind, i int) string { return wp03R2Page(kind.key, `true`, i, 3) }, contentpolicy.ErrShape},
		{"null-result", func(_ wp03R2ListKind, _ int) string { return `null` }, contentpolicy.ErrShape},
		{"array-result", func(_ wp03R2ListKind, _ int) string { return `[]` }, contentpolicy.ErrShape},
		{"string-result", func(_ wp03R2ListKind, _ int) string { return `"synthetic"` }, contentpolicy.ErrShape},
		{"number-result", func(_ wp03R2ListKind, _ int) string { return `7` }, contentpolicy.ErrShape},
		{"bool-result", func(_ wp03R2ListKind, _ int) string { return `true` }, contentpolicy.ErrShape},
		{"cursor-null", func(kind wp03R2ListKind, i int) string { return wp03R3CursorPage(kind, i, `null`) }, contentpolicy.ErrShape},
		{"cursor-number", func(kind wp03R2ListKind, i int) string { return wp03R3CursorPage(kind, i, `7`) }, contentpolicy.ErrShape},
		{"cursor-object", func(kind wp03R2ListKind, i int) string { return wp03R3CursorPage(kind, i, `{}`) }, contentpolicy.ErrShape},
		{"cursor-array", func(kind wp03R2ListKind, i int) string { return wp03R3CursorPage(kind, i, `[]`) }, contentpolicy.ErrShape},
		{"cursor-bool", func(kind wp03R2ListKind, i int) string { return wp03R3CursorPage(kind, i, `true`) }, contentpolicy.ErrShape},
		{"oversized-page", func(kind wp03R2ListKind, i int) string {
			page := wp03R2Pages(kind)[i]
			// Oversize the page, not its items, to isolate ingress byte bounds.
			return strings.TrimSuffix(page, "}") + `,"padding":"` + strings.Repeat("x", 2048) + `"}`
		}, ErrResultTooLarge},
	}
	for _, kind := range wp03R2Kinds() {
		for badIndex, position := range []string{"first", "middle", "last"} {
			for _, tc := range cases {
				t.Run(kind.key+"/"+position+"/"+tc.name, func(t *testing.T) {
					c := NewClient(ClientConfig{Call: CallSettings{MaxResultBytes: 1024}})
					defer c.Close()
					wp03R2Seed(t, c)
					oldTools, oldResources, oldPrompts := c.tools.Load(), c.resources.Load(), c.prompts.Load()
					oldRaw := wp03R2Cached(c, kind.key)
					notified := 0
					c.SetOnToolsChanged(func(string, []Tool) { notified++ })
					assertRetained := func() {
						t.Helper()
						if c.tools.Load() != oldTools || c.resources.Load() != oldResources || c.prompts.Load() != oldPrompts {
							t.Error("failed refresh replaced a previously complete cache")
						}
						if !wp03R2SameRaw(wp03R2Cached(c, kind.key), oldRaw) || notified != 0 {
							t.Errorf("failed refresh changed cached bytes or emitted callback: callbacks=%d", notified)
						}
					}
					pages := wp03R2Pages(kind)
					pages[badIndex] = tc.value(kind, badIndex)
					tr := &wp03R2Transport{t: t, method: kind.method, pages: pages, before: assertRetained}
					got, err := c.listAll(context.Background(), tr, kind.method, kind.key)
					if !errors.Is(err, tc.want) || len(got) != 0 {
						t.Errorf("malformed page accepted or partial listing returned: items=%d err=%v want=%v", len(got), err, tc.want)
					}
					if tr.calls != badIndex+1 {
						t.Errorf("listAll fetched %d pages, want %d", tr.calls, badIndex+1)
					}
					assertRetained()
					tr = &wp03R2Transport{t: t, method: kind.method, pages: pages, before: assertRetained}
					if err := kind.refresh(c, context.Background(), tr); !errors.Is(err, tc.want) {
						t.Errorf("discovery accepted malformed page: err=%v want=%v", err, tc.want)
					}
					if tr.calls != badIndex+1 {
						t.Errorf("discovery fetched %d pages, want %d", tr.calls, badIndex+1)
					}
					assertRetained()
				})
			}
		}
	}
}

func TestWP03R2ValidatedListSequencePublishes(t *testing.T) {
	for _, kind := range wp03R2Kinds() {
		for _, mode := range []string{"three-pages", "empty-first", "empty-middle", "empty-final", "empty-only", "explicit-empty-cursor", "whitespace"} {
			t.Run(kind.key+"/"+mode, func(t *testing.T) {
				c := NewClient(ClientConfig{Call: CallSettings{MaxResultBytes: 1024}})
				defer c.Close()
				wp03R2Seed(t, c)
				oldTools, oldResources, oldPrompts := c.tools.Load(), c.resources.Load(), c.prompts.Load()
				pages := wp03R2Pages(kind)
				switch mode {
				case "empty-first":
					pages[0] = wp03R2Page(kind.key, "[]", 0, 3)
				case "empty-middle":
					pages[1] = wp03R2Page(kind.key, "[]", 1, 3)
				case "empty-final":
					pages[2] = wp03R2Page(kind.key, "[]", 2, 3)
				case "empty-only":
					pages = []string{wp03R2Page(kind.key, "[]", 0, 1)}
				case "explicit-empty-cursor":
					pages[2] = wp03R3CursorPage(kind, 2, `""`)
				case "whitespace":
					for i, page := range pages {
						page = strings.Replace(page, `":`, "\": \t", 1)
						page = strings.Replace(page, `,"nextCursor":`, ", \"nextCursor\": \n", 1)
						pages[i] = " \r\n" + page + "\t "
					}
				}
				var want []json.RawMessage
				for _, page := range pages {
					var object map[string]json.RawMessage
					var items []json.RawMessage
					if err := json.Unmarshal([]byte(page), &object); err != nil {
						t.Fatal(err)
					}
					if err := json.Unmarshal(object[kind.key], &items); err != nil {
						t.Fatal(err)
					}
					want = append(want, items...)
				}
				notified := 0
				tr := &wp03R2Transport{t: t, method: kind.method, pages: pages}
				tr.before = func() {
					if c.tools.Load() != oldTools || c.resources.Load() != oldResources || c.prompts.Load() != oldPrompts || notified != 0 {
						t.Error("published before validating the final page")
					}
				}
				c.SetOnToolsChanged(func(_ string, tools []Tool) {
					notified++
					var raw []json.RawMessage
					for _, tool := range tools {
						raw = append(raw, tool.Raw)
					}
					if tr.calls != len(pages) || !wp03R2SameRaw(raw, want) || !wp03R2SameRaw(wp03R2Cached(c, kind.key), want) {
						t.Error("callback did not observe the full validated cache")
					}
				})
				if err := kind.refresh(c, context.Background(), tr); err != nil {
					t.Fatalf("healthy sequence refused: %v", err)
				}
				if tr.calls != len(pages) || !wp03R2SameRaw(wp03R2Cached(c, kind.key), want) {
					t.Error("healthy sequence lost a page, item order or raw extension bytes")
				}
				wantCallbacks := 0
				if kind.key == "tools" {
					wantCallbacks = 1
				}
				if notified != wantCallbacks {
					t.Errorf("callbacks=%d want=%d", notified, wantCallbacks)
				}
				if (kind.key != "tools" && c.tools.Load() != oldTools) ||
					(kind.key != "resources" && c.resources.Load() != oldResources) ||
					(kind.key != "prompts" && c.prompts.Load() != oldPrompts) {
					t.Error("healthy refresh changed another list's cache")
				}
			})
		}
	}
}
