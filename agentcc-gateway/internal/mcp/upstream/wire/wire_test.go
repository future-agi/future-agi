package wire

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
)

func TestReaderEvents(t *testing.T) {
	stream := ": keepalive\n\nid: 7\nevent: message\ndata: {\"a\":\ndata: 1}\n\nretry: 10\n\nevent: endpoint\r\ndata: /m?s=1\r\n\ndata: last"
	r := NewReader(strings.NewReader(stream), 0)
	want := []Event{{ID: "7", Name: "message", Data: []byte("{\"a\":\n1}")}, {Name: "endpoint", Data: []byte("/m?s=1")}, {Data: []byte("last")}}
	for i, w := range want {
		ev, err := r.Next()
		if err != nil || ev.ID != w.ID || ev.Name != w.Name || string(ev.Data) != string(w.Data) {
			t.Fatalf("event %d = %+v, %v; want %+v", i, ev, err, w)
		}
	}
	if _, err := r.Next(); !errors.Is(err, io.EOF) {
		t.Fatalf("after the last event: %v, want EOF", err)
	}
	big := NewReader(strings.NewReader("data: "+strings.Repeat("x", 200)+"\n\n"), 100)
	if _, err := big.Next(); !errors.Is(err, ErrTooLarge) {
		t.Fatalf("a 200-byte event at a 100-byte limit: %v", err)
	}
}

func TestLimit(t *testing.T) {
	if b, err := io.ReadAll(Limit(strings.NewReader("12345"), 5)); err != nil || string(b) != "12345" {
		t.Fatalf("exactly at the limit: %q, %v", b, err)
	}
	if _, err := io.ReadAll(Limit(strings.NewReader("123456"), 5)); !errors.Is(err, ErrTooLarge) {
		t.Fatalf("one byte over: %v", err)
	}
}

func TestEncodeHeaderValue(t *testing.T) {
	for in, want := range map[string]string{
		"eu-west":    "eu-west",
		"":           "",
		"Zürich":     "=?base64?WsO8cmljaA==?=",
		" padded":    "=?base64?IHBhZGRlZA==?=",
		"a\nb":       "=?base64?YQpi?=",
		"=?base64?x": "=?base64?PT9iYXNlNjQ/eA==?=",
	} {
		if got := EncodeHeaderValue(in); got != want {
			t.Errorf("EncodeHeaderValue(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestMirroredNamesAndSchemaParams(t *testing.T) {
	schema := json.RawMessage(`{"properties":{"region":{"x-mcp-header":"Region"},"tier":{"x-mcp-header":true},"n":{"x-mcp-header":"N"}}}`)
	params := json.RawMessage(`{"name":"lookup","arguments":{"region":"eu","tier":"gold","n":3,"Other":"o"}}`)
	inbound := http.Header{"Mcp-Param-Other": {"stale"}, "Mcp-Param-Missing": {"x"}}
	h := Mirrored("tools/call", params, schema, inbound)
	for k, v := range map[string]string{"Mcp-Method": "tools/call", "Mcp-Name": "lookup", "Mcp-Param-Region": "eu", "Mcp-Param-Tier": "gold", "Mcp-Param-N": "3", "Mcp-Param-Other": "o"} {
		if h.Get(k) != v {
			t.Errorf("%s = %q, want %q", k, h.Get(k), v)
		}
	}
	if h.Get("Mcp-Param-Missing") != "" {
		t.Error("a header naming no argument was carried")
	}
	if got := Mirrored("resources/read", json.RawMessage(`{"uri":"file:///a b"}`), nil, nil).Get("Mcp-Name"); got != "file:///a b" {
		t.Errorf("resources/read Mcp-Name = %q", got)
	}
}

func TestSetMeta(t *testing.T) {
	out, err := SetMeta(json.RawMessage(`{"name":"x","_meta":{"keep":1,"progressToken":"old"}}`), map[string]json.RawMessage{"progressToken": json.RawMessage(`"new"`)})
	if err != nil || MetaOf(out)["keep"] == nil || string(MetaOf(out)["progressToken"]) != `"new"` || !strings.Contains(string(out), `"name":"x"`) {
		t.Fatalf("SetMeta = %s, %v", out, err)
	}
	if _, err := SetMeta(json.RawMessage(`[1]`), map[string]json.RawMessage{"a": json.RawMessage(`1`)}); err == nil {
		t.Fatal("params that are not an object were accepted")
	}
}

func TestAllCapsAndRepeatedCursor(t *testing.T) {
	page := func(n int) []json.RawMessage {
		out := make([]json.RawMessage, n)
		for i := range out {
			out[i] = json.RawMessage(`{}`)
		}
		return out
	}
	l, err := All(context.Background(), func(_ context.Context, cursor string) ([]json.RawMessage, string, error) {
		return page(1), "same", nil
	})
	if err != nil || !l.Capped || l.Pages != 2 {
		t.Fatalf("a repeated cursor: %+v, %v; want capped after the second page", l, err)
	}
	calls := 0
	l, _ = All(context.Background(), func(_ context.Context, cursor string) ([]json.RawMessage, string, error) {
		calls++
		return page(1), fmt.Sprint(calls), nil
	})
	if !l.Capped || l.Pages != MaxPages || len(l.Items) != MaxPages {
		t.Fatalf("an endless list: %d pages, %d items, capped %v", l.Pages, len(l.Items), l.Capped)
	}
}

func TestForwardedRefusesCredentialsAndMcpHeaders(t *testing.T) {
	in := http.Header{"X-Tenant": {"t"}, "Authorization": {"Bearer k"}, "Mcp-Param-A": {"a"}, "X-Agentcc-Run-Id": {"r"}}
	out := Forwarded(in, []string{"x-tenant", "authorization", "mcp-param-a", "x-agentcc-run-id", ""})
	if len(out) != 1 || out.Get("X-Tenant") != "t" {
		t.Fatalf("Forwarded = %v, want X-Tenant only", out)
	}
}

func TestTokensRouteOnlyIssued(t *testing.T) {
	tok := NewTokens()
	var got json.RawMessage
	gw, release := tok.Issue(json.RawMessage(`5`), func(p json.RawMessage) { got = p })
	if sink, p, ok := tok.Route(json.RawMessage(`{"progressToken":` + string(gw) + `,"progress":2}`)); !ok {
		t.Fatal("an issued token was not routed")
	} else {
		sink(p)
	}
	if !strings.Contains(string(got), `"progressToken":5`) {
		t.Fatalf("routed params %s, want the caller's token 5", got)
	}
	release()
	if _, _, ok := tok.Route(json.RawMessage(`{"progressToken":` + string(gw) + `}`)); ok {
		t.Fatal("a released token was routed")
	}
	if _, _, ok := NewTokens().Route(json.RawMessage(`{"progressToken":` + string(gw) + `}`)); ok {
		t.Fatal("another client's token was routed")
	}
}
