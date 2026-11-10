package openai

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	anthropicwire "github.com/futureagi/agentcc-gateway/internal/translation/anthropic"
)

func TestResponsesBridgePreservesNativeSingleCallPolicy(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(map[bool]string{false: "complete", true: "stream"}[stream], func(t *testing.T) {
			var calls atomic.Int32
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				var body map[string]json.RawMessage
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Error(err)
				}
				if string(body["parallel_tool_calls"]) != "false" {
					t.Errorf("parallel_tool_calls=%s, want false", body["parallel_tool_calls"])
				}
				if !strings.Contains(string(body["tools"]), "permitted_tool") {
					t.Error("permitted tool missing")
				}
				if stream {
					w.Header().Set("Content-Type", "text/event-stream")
					sse(w, `{"type":"response.completed","response":{"id":"synthetic","model":"test","status":"completed","output":[],"usage":{"input_tokens":1,"output_tokens":1}}}`)
				} else {
					io.WriteString(w, `{"id":"synthetic","model":"test","output":[],"usage":{"input_tokens":1,"output_tokens":1}}`)
				}
			}))
			defer upstream.Close()
			p := newTestProvider(t, upstream.URL)
			defer p.Close()
			req, _, err := anthropicwire.New().RequestToCanonical([]byte(`{"model":"test","max_tokens":32,"messages":[{"role":"user","content":"synthetic"}],"tools":[{"name":"permitted_tool","input_schema":{"type":"object"}}],"tool_choice":{"type":"auto","disable_parallel_tool_use":true}}`))
			if err != nil {
				t.Fatal(err)
			}
			if stream {
				chunks, errs := p.StreamChatCompletionViaResponses(context.Background(), req)
				for range chunks {
				}
				err = <-errs
			} else {
				_, err = p.ChatCompletionViaResponses(context.Background(), req)
			}
			if err != nil {
				t.Fatal(err)
			}
			if calls.Load() != 1 {
				t.Errorf("calls=%d", calls.Load())
			}
		})
	}
}

func TestResponsesBridgeRefusesCallerContextsBeforeDispatch(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		io.WriteString(w, `{"id":"synthetic","output":[]}`)
	}))
	defer upstream.Close()
	p := newTestProvider(t, upstream.URL)
	defer p.Close()
	req, _, err := anthropicwire.New().RequestToCanonical([]byte(`{"model":"test","max_tokens":32,"messages":[{"role":"user","content":"synthetic"}],"tools":[{"name":"restricted_tool","allowed_callers":["code_execution_20260120"],"input_schema":{"type":"object"}}]}`))
	if err != nil {
		t.Fatal(err)
	}
	_, err = p.ChatCompletionViaResponses(context.Background(), req)
	if apiErr, ok := err.(*models.APIError); !ok || apiErr.Status != 400 {
		t.Errorf("completion error=%v, want 400", err)
	}
	chunks, errs := p.StreamChatCompletionViaResponses(context.Background(), req)
	for range chunks {
	}
	err = <-errs
	if apiErr, ok := err.(*models.APIError); !ok || apiErr.Status != 400 {
		t.Errorf("stream error=%v, want 400", err)
	}
	if calls.Load() != 0 {
		t.Errorf("restricted request reached provider: calls=%d", calls.Load())
	}
}
