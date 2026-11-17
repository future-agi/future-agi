package server

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Opt-in cloud gate. It builds the actual gateway handler at this commit and
// exercises its auth, routing, adapter and translation over real loopback HTTP.
// The upstream is synthetic; no credentials or external model APIs are used.
func TestAnthropicSDKStreams(t *testing.T) {
	python := os.Getenv("ANTHROPIC_SDK_PYTHON")
	if python == "" {
		t.Skip("cloud SDK gate: set ANTHROPIC_SDK_PYTHON and SDK_NODE_PACKAGE")
	}
	if os.Getenv("SDK_NODE_PACKAGE") == "" {
		t.Fatal("SDK_NODE_PACKAGE required")
	}
	for _, native := range []bool{false, true} {
		for _, scenario := range []string{"text", "tool", "thinking", "estimated", "error"} {
			t.Run(fmt.Sprintf("native=%v/%s", native, scenario), func(t *testing.T) {
				var srv *Server
				model, credential := "anthropic/claude-sonnet-5.5", managedOrgKey
				if native {
					srv = sdkNativeGateway(t, scenario)
					model, credential = "org-model", r7CustA
				} else {
					g := newManagedTestGateway(t, managedOptIn())
					g.ledger.SetMicros("org-m", auth.USDToMicros(10))
					upstream := sdkCanonicalStream(scenario)
					g.managed.streamWith.Store(&upstream)
					srv = g.srv
				}
				ts := httptest.NewServer(srv.httpServer.Handler)
				defer ts.Close()
				for _, sdk := range []struct{ executable, script string }{{python, "stream.py"}, {"node", "stream.mjs"}} {
					script, err := filepath.Abs(filepath.Join("..", "..", "tests", "anthropic-sdk", sdk.script))
					if err != nil {
						t.Fatal(err)
					}
					ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
					args := []string{script, ts.URL, model, scenario}
					if sdk.executable == python {
						args = append([]string{"-I"}, args...)
					}
					cmd := exec.CommandContext(ctx, sdk.executable, args...)
					cmd.Env = append(os.Environ(), "SDK_FIXTURE_KEY="+credential, fmt.Sprintf("SDK_FIXTURE_NATIVE=%v", native))
					out, err := cmd.CombinedOutput()
					cancel()
					if err != nil {
						t.Errorf("%s failed: %v\n%s", sdk.script, err, out)
					} else {
						t.Logf("%s", out)
					}
				}
			})
		}
	}
}

func sdkCanonicalStream(scenario string) upstreamStream {
	return func(w http.ResponseWriter, _ *http.Request, model string) {
		w.Header().Set("Content-Type", "text/event-stream")
		if scenario != "estimated" {
			writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":0,"total_tokens":7}`)
		}
		if scenario == "thinking" {
			writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"reasoning_content":"Consider"}}]`)
		}
		reason := "stop"
		if scenario == "tool" {
			reason = "tool_calls"
			writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"toolu_test","type":"function","function":{"name":"weather","arguments":"{\"city\":"}}]}}]`)
			writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"Paris\"}"}}]}}]`)
		} else {
			writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"content":"Hello"}}]`)
			writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"content":" world"}}]`)
		}
		if scenario == "error" {
			fmt.Fprint(w, "data: {\"error\":{\"type\":\"server_error\",\"message\":\"synthetic stream failure\",\"code\":\"synthetic\"}}\n\n")
			w.(http.Flusher).Flush()
			return
		}
		writeManagedChunk(w, model, fmt.Sprintf(`"choices":[{"index":0,"delta":{},"finish_reason":%q}]`, reason))
		if scenario != "estimated" {
			writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":4,"total_tokens":13}`)
		}
		fmt.Fprint(w, "data: [DONE]\n\n")
	}
}

// Native fixture deliberately includes ping and signature_delta. Its bytes
// must pass through unchanged, not through the canonical translator.
func sdkNativeFrames(scenario string) string {
	frames := []string{`{"type":"message_start","message":{"id":"msg_native","type":"message","role":"assistant","model":"org-model","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":9,"output_tokens":0}}}`, `{"type":"ping"}`}
	index := 0
	if scenario == "thinking" {
		frames = append(frames,
			`{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"","signature":""}}`,
			`{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Consider"}}`,
			`{"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"synthetic-signature"}}`,
			`{"type":"content_block_stop","index":0}`)
		index++
	}
	reason := "end_turn"
	if scenario == "tool" {
		reason = "tool_use"
		frames = append(frames,
			`{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_test","name":"weather","input":{}}}`,
			`{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"city\":"}}`,
			`{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\"Paris\"}"}}`)
	} else {
		frames = append(frames,
			fmt.Sprintf(`{"type":"content_block_start","index":%d,"content_block":{"type":"text","text":""}}`, index),
			fmt.Sprintf(`{"type":"content_block_delta","index":%d,"delta":{"type":"text_delta","text":"Hello world"}}`, index))
	}
	if scenario == "error" {
		frames = append(frames, `{"type":"error","error":{"type":"api_error","message":"synthetic stream failure"}}`)
	} else {
		frames = append(frames,
			fmt.Sprintf(`{"type":"content_block_stop","index":%d}`, index),
			fmt.Sprintf(`{"type":"message_delta","delta":{"stop_reason":%q,"stop_sequence":null},"usage":{"output_tokens":4}}`, reason),
			`{"type":"message_stop"}`)
	}
	var out strings.Builder
	for _, frame := range frames {
		// type is the first field in these literal, independent golden fixtures.
		name := strings.SplitN(strings.TrimPrefix(frame, `{"type":"`), `"`, 2)[0]
		fmt.Fprintf(&out, "event: %s\ndata: %s\n\n", name, frame)
	}
	return out.String()
}

func sdkNativeGateway(t *testing.T, scenario string) *Server {
	t.Helper()
	up := &r7Upstream{Server: httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/messages" {
			http.Error(w, "unexpected fixture path", 400)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, sdkNativeFrames(scenario))
	}))}
	t.Cleanup(up.Close)
	srv := r7Gateway(t, map[string]r7Op{"alpha": {up, []string{"org-model"}}}, func(cfg *config.Config) {
		p := cfg.Providers["alpha"]
		p.APIFormat = "anthropic"
		cfg.Providers["alpha"] = p
	})
	r7Push(t, srv, r7OrgA, `{"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"anthropic","models":["org-model"],"enabled":true}}}`)
	return srv
}

func TestAnthropicNativeStreamSchemaPassthrough(t *testing.T) {
	for _, scenario := range []string{"text", "tool", "thinking", "error"} {
		t.Run(scenario, func(t *testing.T) {
			srv := sdkNativeGateway(t, scenario)
			w := messagesRoutingCall(srv, r7CustA, "org-model", true, "")
			if w.Code != 200 || w.Body.String() != sdkNativeFrames(scenario) {
				t.Fatalf("native bytes changed: status=%d body=%s", w.Code, w.Body)
			}
		})
	}
}
