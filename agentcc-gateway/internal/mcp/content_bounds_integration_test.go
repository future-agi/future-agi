package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type wp03Transport struct {
	replies []string
	calls   int
}

func (s *wp03Transport) Send(_ context.Context, req *Message) (*Message, error) {
	if s.calls >= len(s.replies) {
		return nil, fmt.Errorf("synthetic reply exhausted")
	}
	raw := s.replies[s.calls]
	s.calls++
	return &Message{JSONRPC: "2.0", ID: req.ID, Result: json.RawMessage(raw)}, nil
}
func (*wp03Transport) Close() error  { return nil }
func (*wp03Transport) Healthy() bool { return true }

func TestWP03ListFailureKeepsCompleteCache(t *testing.T) {
	cases := map[string][]string{
		"cycle":      {`{"tools":[{"name":"new","inputSchema":{}}],"nextCursor":"A"}`, `{"tools":[],"nextCursor":"B"}`, `{"tools":[],"nextCursor":"A"}`},
		"bad-cursor": {`{"tools":[{"name":"new","inputSchema":{}}],"nextCursor":7}`},
		"duplicate":  {`{"tools":[{"name":"new","name":"replaced","inputSchema":{}}]}`},
		"aggregate":  {`{"tools":[{"name":"new","inputSchema":{},"description":"` + strings.Repeat("x", 280) + `"}],"nextCursor":"A"}`, `{"tools":[{"name":"next","inputSchema":{},"description":"` + strings.Repeat("y", 280) + `"}]}`},
	}
	for name, pages := range cases {
		t.Run(name, func(t *testing.T) {
			c := NewClient(ClientConfig{Call: CallSettings{MaxResultBytes: 512}})
			defer c.Close()
			old := []Tool{{Name: "old", InputSchema: json.RawMessage(`{}`), Raw: json.RawMessage(`{"name":"old","inputSchema":{}}`)}}
			c.tools.Store(&old)
			notified := 0
			c.SetOnToolsChanged(func(string, []Tool) { notified++ })
			tr := &wp03Transport{replies: pages}
			if err := c.discoverTools(context.Background(), tr); err == nil {
				t.Fatal("invalid listing accepted")
			}
			if got := c.Tools(); len(got) != 1 || got[0].Name != "old" || notified != 0 {
				t.Fatalf("partial cache published: count=%d callbacks=%d", len(got), notified)
			}
			if tr.calls > len(pages) {
				t.Fatal("followed rejected cursor")
			}
		})
	}
}

func TestWP03HealthyAllListKinds(t *testing.T) {
	c := NewClient(ClientConfig{})
	defer c.Close()
	for _, tc := range []struct{ method, key, item string }{
		{MethodToolsList, "tools", `{"name":"tool","inputSchema":{},"extension":{"kept":true}}`},
		{MethodResourcesList, "resources", `{"name":"resource","uri":"synthetic://resource"}`},
		{MethodPromptsList, "prompts", `{"name":"prompt","arguments":[]}`},
	} {
		tr := &wp03Transport{replies: []string{`{"` + tc.key + `":[],"nextCursor":"next"}`, `{"` + tc.key + `":[` + tc.item + `]}`}}
		got, err := c.listAll(context.Background(), tr, tc.method, tc.key)
		if err != nil || len(got) != 1 || string(got[0]) != tc.item {
			t.Fatalf("healthy %s: count=%d err=%v", tc.method, len(got), err)
		}
	}
}

func TestWP03CacheDeliveryDoesNotAliasValidatedBytes(t *testing.T) {
	c := NewClient(ClientConfig{})
	defer c.Close()
	tr := &wp03Transport{replies: []string{`{"tools":[{"name":"safe","inputSchema":{"type":"object"}}]}`}}
	c.SetOnToolsChanged(func(_ string, tools []Tool) { tools[0].Raw[0] = '['; tools[0].InputSchema[0] = '[' })
	if err := c.discoverTools(context.Background(), tr); err != nil {
		t.Fatal(err)
	}
	got := c.Tools()
	if got[0].Raw[0] != '{' || got[0].InputSchema[0] != '{' {
		t.Fatal("callback changed cached bytes")
	}
	got[0].Name = "changed"
	got[0].Raw[0] = '['
	got[0].InputSchema[0] = '['
	again := c.Tools()
	if again[0].Name != "safe" || again[0].Raw[0] != '{' || again[0].InputSchema[0] != '{' {
		t.Fatal("caller changed cached content")
	}
	resources := []Resource{{Name: "safe", Raw: json.RawMessage(`{}`), Icons: json.RawMessage(`[]`)}}
	prompts := []Prompt{{Name: "safe", Raw: json.RawMessage(`{}`), Arguments: []PromptArgument{{Name: "safe"}}}}
	c.resources.Store(&resources)
	c.prompts.Store(&prompts)
	c.Resources()[0].Raw[0] = '['
	c.Resources()[0].Icons[0] = '{'
	c.Prompts()[0].Raw[0] = '['
	c.Prompts()[0].Arguments[0].Name = "changed"
	if c.Resources()[0].Raw[0] != '{' || c.Resources()[0].Icons[0] != '[' || c.Prompts()[0].Raw[0] != '{' || c.Prompts()[0].Arguments[0].Name != "safe" {
		t.Fatal("resource/prompt cache aliases delivery")
	}
}

func TestWP03InheritedTransportCapCannotWiden(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"jsonrpc":"2.0","id":1,"result":{"text":"`+strings.Repeat("x", 100)+`"}}`)
	}))
	defer srv.Close()
	tr := NewHTTPTransport(srv.URL, AuthConfig{})
	defer tr.Close()
	tr.maxBytes = 64
	res, err := tr.sendWith(context.Background(), &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPing}, sendOptions{maxBytes: 4096})
	if err == nil || res != nil {
		t.Fatal("request option widened transport ceiling")
	}
}

func TestWP03FinalEnvelopeRefusesAmbiguousCachedContent(t *testing.T) {
	for _, raw := range []string{`{"content":[],"content":[{"type":"text","text":"synthetic-rejected"}]}`, `{"content":[],"x":` + strings.Repeat(`[`, 130) + `0` + strings.Repeat(`]`, 130) + `}`} {
		w := httptest.NewRecorder()
		writeJSONRPCResult(w, json.RawMessage(`1`), json.RawMessage(raw))
		var response Message
		if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
			t.Fatal(err)
		}
		if response.Error == nil || len(response.Result) != 0 || strings.Contains(w.Body.String(), "synthetic-rejected") {
			t.Fatal("unsafe cached content released")
		}
	}
	w := httptest.NewRecorder()
	writeJSONRPCResult(w, json.RawMessage(`1`), json.RawMessage(`{"content":[],"extension":{"kept":true}}`))
	var response Message
	if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil || response.Error != nil || !strings.Contains(string(response.Result), `"kept":true`) {
		t.Fatalf("healthy envelope lost: %v", err)
	}
}

func TestWP03NotificationValidationPrecedesCallback(t *testing.T) {
	called := 0
	deliverRaw([]byte(`{"jsonrpc":"2.0","method":"notifications/progress","params":{"x":1,"x":2}}`), func(*Message) { called++ })
	if called != 0 {
		t.Fatal("ambiguous notification released")
	}
	deliverRaw([]byte(`{"jsonrpc":"2.0","method":"notifications/progress","params":{"x":1}}`), func(*Message) { called++ })
	if called != 1 {
		t.Fatal("healthy notification dropped")
	}
}

type wp03Process struct {
	StdioProcess
	out io.Reader
}

func (p wp03Process) Stdout() io.Reader { return p.out }

func TestWP03StdioBeforePendingDelivery(t *testing.T) {
	for _, invalid := range []bool{false, true} {
		raw := `{"jsonrpc":"2.0","id":1,"result":{"content":[]}}`
		if invalid {
			raw = `{"jsonrpc":"2.0","id":1,"result":{"x":1,"x":2}}`
		}
		tr := newStdioTransport(StdioSpec{ServerID: "synthetic-bounds"}, defaultStdioOptions())
		tr.proc = wp03Process{out: strings.NewReader(raw + "\n")}
		ch := make(chan stdioResult, 1)
		tr.pending["1"] = ch
		tr.readLoop()
		got := <-ch
		if invalid && (got.err == nil || got.msg != nil) {
			t.Fatal("stdio released ambiguous message")
		}
		if !invalid && (got.err != nil || got.msg == nil) {
			t.Fatalf("healthy stdio refused: %v", got.err)
		}
	}
}

func TestWP03NoPartialEmptyPageBudget(t *testing.T) {
	c := NewClient(ClientConfig{})
	defer c.Close()
	var pages []string
	for i := 0; i < 51; i++ {
		pages = append(pages, fmt.Sprintf(`{"tools":[],"nextCursor":"page-%d"}`, i))
	}
	tr := &wp03Transport{replies: pages}
	got, err := c.listAll(context.Background(), tr, MethodToolsList, "tools")
	if err == nil || len(got) != 0 || tr.calls != 50 {
		t.Fatalf("empty-page budget not refused atomically: calls=%d items=%d err=%v", tr.calls, len(got), err)
	}
}

func TestWP03FallbackTransportPreservesByteError(t *testing.T) {
	c := NewClient(ClientConfig{Call: CallSettings{MaxResultBytes: 128}})
	defer c.Close()
	tr := &wp03Transport{replies: []string{`{"content":[{"type":"text","text":"` + strings.Repeat("x", 256) + `"}]}`}}
	got, err := c.send(context.Background(), tr, &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall}, sendOptions{})
	if got != nil || !errors.Is(err, ErrResultTooLarge) {
		t.Fatalf("byte refusal changed public error identity: %v", err)
	}
}

func TestWP03FinalEnvelopeCountsSerializerOverhead(t *testing.T) {
	prefix, suffix := `{"content":[{"type":"text","text":"`, `"}]}`
	raw := prefix + strings.Repeat("x", DefaultMaxResultBytes-len(prefix)-len(suffix)) + suffix
	if len(raw) != DefaultMaxResultBytes {
		t.Fatal("fixture must exactly fit the payload cap")
	}
	w := httptest.NewRecorder()
	writeJSONRPCResult(w, json.RawMessage(`1`), json.RawMessage(raw))
	var response Message
	if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil || response.Error == nil || len(response.Result) != 0 {
		t.Fatalf("JSON-RPC serializer overhead escaped the byte limit: %v", err)
	}
}
