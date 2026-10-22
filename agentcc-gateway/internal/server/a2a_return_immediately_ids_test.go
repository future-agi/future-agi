package server

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	agentcca2a "github.com/futureagi/agentcc-gateway/internal/a2a"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// A task sent with configuration.returnImmediately is answered at once and
// runs after its A2A request has ended, on a context of its own
// (a2a/server.go, taskContext). Its run must still carry the trace id of the
// A2A request that started it, as a blocking task's does
// (TestA2ATasksRunWithTheirOwnRequestIDs): the caller's x-agentcc-trace-id,
// or the one the gateway gave that request. And on both paths the run has a
// request id of its own, never its A2A request's x-agentcc-request-id.
// The two tasks of each path run at the same time.
func TestA2ATasksCarryTheirA2ARequestsTraceOnBothPaths(t *testing.T) {
	for _, returnImmediately := range []bool{true, false} {
		name := "blocking"
		if returnImmediately {
			name = "return immediately"
		}
		t.Run(name, func(t *testing.T) {
			up := startRendezvousUpstream(t, 2)
			cfg := config.DefaultConfig()
			cfg.Auth.Enabled = true
			cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "svc", Key: "sk-test-key-001", Owner: "test-org", KeyType: "internal"}}
			cfg.A2A.Enabled = true
			cfg.A2A.Card = config.A2ACardConfig{Name: "gw", Description: "d", Version: "1.0.0"}
			cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			probe := &runIDProbe{}
			srv := New(cfg, "", registry, pipeline.NewEngine(probe), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
			srv.ready.Store(true)

			var replies []a2aReply
			if returnImmediately {
				// Each answer comes back before its task runs, so the two are
				// sent one after the other; the upstream still holds the
				// first call until the second is in flight.
				for _, trace := range []string{"trace-from-the-a2a-caller", ""} {
					replies = append(replies, sendReturnImmediatelyTask(t, srv, trace))
				}
			} else {
				replies = sendA2ATasksTogether(t, srv, "sk-test-key-001", "gpt-4o", "trace-from-the-a2a-caller", "")
			}
			if replies[0].traceID != "trace-from-the-a2a-caller" || replies[1].traceID == "" {
				t.Fatalf("the A2A responses carry trace ids %q and %q; want the caller's, and one the gateway made", replies[0].traceID, replies[1].traceID)
			}

			runs := probe.waitRuns(2)
			t.Logf("runs (request id, trace id): %q; A2A replies (request id, trace id): %q %q, %q %q",
				runs, replies[0].requestID, replies[0].traceID, replies[1].requestID, replies[1].traceID)
			if len(runs) != 2 {
				t.Fatalf("the pipeline ran %d times for 2 A2A tasks", len(runs))
			}
			if n := up.mostInFlight(); n != 2 {
				t.Fatalf("the upstream held at most %d call(s) at once: the two tasks did not run at the same time", n)
			}
			assertOwnRequestIDs(t, "A2A task", runs)
			assertRunsCarryTheirA2ATraces(t, runs, replies)
			for _, run := range runs {
				for _, reply := range replies {
					if reply.requestID == "" || run.requestID == reply.requestID {
						t.Errorf("a task's run has request id %q, its A2A request's x-agentcc-request-id is %q: the run must have one of its own", run.requestID, reply.requestID)
					}
				}
			}
		})
	}
}

// sendReturnImmediatelyTask sends one message/send with returnImmediately,
// with the caller's x-agentcc-trace-id when traceID is set, and returns the
// A2A response's headers once the gateway accepted the task.
func sendReturnImmediatelyTask(t *testing.T, srv *Server, traceID string) a2aReply {
	t.Helper()
	params, err := json.Marshal(agentcca2a.MessageSendParams{
		Message:       agentcca2a.Message{Role: "user", Parts: []agentcca2a.MessagePart{{Type: "text", Text: "hi"}}},
		Metadata:      json.RawMessage(`{"model":"gpt-4o"}`),
		Configuration: &agentcca2a.MessageSendConfiguration{ReturnImmediately: true},
	})
	if err != nil {
		t.Fatal(err)
	}
	body, err := json.Marshal(mcp.Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: agentcca2a.MethodMessageSend, Params: params})
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/a2a", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer sk-test-key-001")
	req.Header.Set("Content-Type", "application/json")
	if traceID != "" {
		req.Header.Set("x-agentcc-trace-id", traceID)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	var resp mcp.Message
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil || resp.Error != nil {
		t.Fatalf("message/send with returnImmediately: status %d, body %s", w.Code, w.Body.String())
	}
	reply := a2aReply{code: w.Code, traceID: w.Header().Get("x-agentcc-trace-id"), requestID: w.Header().Get("x-agentcc-request-id")}
	if err := json.Unmarshal(resp.Result, &reply.task); err != nil {
		t.Fatalf("decoding the accepted task: %v (body %s)", err, w.Body.String())
	}
	return reply
}
