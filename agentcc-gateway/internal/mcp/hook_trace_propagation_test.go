package mcp

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream/wire"
)

func traceEcho(msg *Message) *Message {
	var result any = map[string]any{}
	switch msg.Method {
	case MethodInitialize:
		result = map[string]any{"protocolVersion": "2025-11-25", "capabilities": map[string]any{"tools": map[string]any{}}, "serverInfo": map[string]string{"name": "trace-echo", "version": "1"}}
	case MethodToolsList:
		result = map[string]any{"tools": []any{map[string]any{"name": "echo", "inputSchema": map[string]any{"type": "object"}}}}
	case MethodToolsCall:
		result = ToolCallResult{Content: []ContentPart{{Type: "text", Text: string(msg.Params)}}}
	}
	resp, _ := NewResponse(msg.ID, result)
	return resp
}

func TestTraceEchoHelperProcess(t *testing.T) {
	if len(os.Args) == 0 || os.Args[len(os.Args)-1] != "g16-trace-helper" {
		return
	}
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		var msg Message
		if json.Unmarshal(scanner.Bytes(), &msg) == nil && !msg.IsNotification() {
			raw, _ := json.Marshal(traceEcho(&msg))
			fmt.Println(string(raw))
		}
	}
	helperExit(0)
}

func TestTraceparentIntoUpstreamMetaHTTPAndStdio(t *testing.T) {
	for _, transport := range []string{"http", "stdio"} {
		t.Run(transport, func(t *testing.T) {
			e := &traceCapture{}
			h := tracedHarness(t, e)
			cfg := ClientConfig{ServerID: "trace", TransportType: transport}
			if transport == "http" {
				srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					var msg Message
					_ = json.NewDecoder(r.Body).Decode(&msg)
					if msg.IsNotification() {
						w.WriteHeader(202)
						return
					}
					w.Header().Set("Content-Type", "application/json")
					_ = json.NewEncoder(w).Encode(traceEcho(&msg))
				}))
				defer srv.Close()
				cfg.URL = srv.URL
			} else {
				cfg.Command = os.Args[0]
				cfg.Args = []string{"-test.run=^TestTraceEchoHelperProcess$", "--", "g16-trace-helper"}
			}
			client := NewClient(cfg)
			if err := client.Connect(context.Background()); err != nil {
				t.Fatal(err)
			}
			defer client.Close()
			h.s.RegisterClient(client)
			parent := "00-11111111111111111111111111111111-2222222222222222-01"
			h.onHook(func(hook string, cc *CallContext) {
				attribute("none")(hook, cc)
				if hook == hookStartSpan {
					cc.msg.Params = json.RawMessage(`{"name":"trace_echo","arguments":{},"_meta":{"traceparent":"` + parent + `","baggage":"synthetic-private-meta"}}`)
				}
			})
			resp := h.call(t, "trace_echo", nil, "traceparent: 00-33333333333333333333333333333333-4444444444444444-01")
			s, c := spanPair(t, e)
			if s.TraceID != "11111111111111111111111111111111" || s.ParentID != "2222222222222222" {
				t.Fatal("meta did not win over header")
			}
			var result ToolCallResult
			_ = json.Unmarshal(resp.Result, &result)
			if len(result.Content) != 1 {
				t.Fatal("no echo result")
			}
			meta := wire.MetaOf(json.RawMessage(result.Content[0].Text))
			var got string
			_ = json.Unmarshal(meta["traceparent"], &got)
			if got != "00-"+c.TraceID+"-"+c.SpanID+"-01" {
				t.Fatal("child context not propagated")
			}
			raw, _ := json.Marshal(e.spans)
			if len(meta) != 1 || strings.Contains(string(raw), "synthetic-private-meta") {
				t.Fatal("arbitrary meta propagated or exported")
			}
		})
	}
}
