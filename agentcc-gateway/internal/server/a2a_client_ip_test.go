package server

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	agentcca2a "github.com/futureagi/agentcc-gateway/internal/a2a"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A2A message/send runs the chat-completion path in-process. These tests pin
// that the address rules (global and per-org ip_acl, per-key allowed_ips) are
// judged on the address of the caller of /v1/a2a, as they are for
// /v1/chat/completions.

const a2aChatReply = `{"id":"x","object":"chat.completion","created":1,"model":"gpt-4o",` +
	`"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],` +
	`"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`

// a2aAccessServer is accessServer with A2A enabled and the given number of
// trusted proxy hops.
func a2aAccessServer(t *testing.T, upstreamURL string, rules accessRules, trustedProxies int) *Server {
	t.Helper()
	return accessServerWith(t, map[string]config.ProviderConfig{
		"openai": {BaseURL: upstreamURL, APIFormat: "openai", Models: []string{"gpt-4o"}},
	}, rules, func(cfg *config.Config) {
		cfg.A2A.Enabled = true
		cfg.IPACL.TrustedProxies = trustedProxies
	})
}

// a2aCall sends one JSON-RPC call to /v1/a2a from peer, with forwardedFor as
// X-Forwarded-For when set, and returns the task in the result.
func a2aCall(t *testing.T, s *Server, peer, forwardedFor, method string, params any) agentcca2a.Task {
	t.Helper()
	data, err := json.Marshal(params)
	if err != nil {
		t.Fatal(err)
	}
	body, err := json.Marshal(mcp.Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: method, Params: data})
	if err != nil {
		t.Fatal(err)
	}
	r := httptest.NewRequest(http.MethodPost, "/v1/a2a", bytes.NewReader(body))
	r.RemoteAddr = peer + ":40000"
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Authorization", "Bearer "+clientIPTestKey)
	if forwardedFor != "" {
		r.Header.Set("X-Forwarded-For", forwardedFor)
	}
	w := httptest.NewRecorder()
	s.httpServer.Handler.ServeHTTP(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("%s from %s: got %d %s, want 200", method, peer, w.Code, w.Body.String())
	}
	var resp mcp.Message
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing a2a response: %v", err)
	}
	if resp.Error != nil {
		t.Fatalf("%s from %s: JSON-RPC error %s", method, peer, resp.Error.Message)
	}
	var task agentcca2a.Task
	if err := json.Unmarshal(resp.Result, &task); err != nil {
		t.Fatalf("parsing task: %v", err)
	}
	return task
}

func a2aSendParams(returnImmediately bool) agentcca2a.MessageSendParams {
	p := agentcca2a.MessageSendParams{
		Message: agentcca2a.Message{
			Role:  "user",
			Parts: []agentcca2a.MessagePart{{Type: "text", Text: "Hello"}},
		},
		Metadata: json.RawMessage(`{"model":"gpt-4o"}`),
	}
	if returnImmediately {
		p.Configuration = &agentcca2a.MessageSendConfiguration{ReturnImmediately: true}
	}
	return p
}

// a2aSend sends message/send and returns the finished task. With
// returnImmediately it polls tasks/get until the task leaves "working".
func a2aSend(t *testing.T, s *Server, peer, forwardedFor string, returnImmediately bool) agentcca2a.Task {
	t.Helper()
	task := a2aCall(t, s, peer, forwardedFor, agentcca2a.MethodMessageSend, a2aSendParams(returnImmediately))
	for deadline := time.Now().Add(5 * time.Second); task.Status.State == agentcca2a.TaskStatusWorking; {
		if time.Now().After(deadline) {
			t.Fatalf("task %s from %s still working", task.ID, peer)
		}
		time.Sleep(5 * time.Millisecond)
		task = a2aCall(t, s, peer, forwardedFor, agentcca2a.MethodTasksGet, agentcca2a.TaskGetParams{TaskID: task.ID})
	}
	return task
}

func taskText(task agentcca2a.Task) string {
	var b bytes.Buffer
	for _, p := range task.Status.Message {
		b.WriteString(p.Text)
	}
	return b.String()
}

// checkA2AAccess sends from peer (with forwardedFor) and checks the task
// state and whether the provider was called.
func checkA2AAccess(t *testing.T, s *Server, upstreamHits func() int64, peer, forwardedFor string, returnImmediately, wantServed bool) {
	t.Helper()
	before := upstreamHits()
	task := a2aSend(t, s, peer, forwardedFor, returnImmediately)
	called := upstreamHits() - before
	label := peer
	if forwardedFor != "" {
		label += " (X-Forwarded-For " + forwardedFor + ")"
	}
	if wantServed {
		if task.Status.State != agentcca2a.TaskStatusCompleted {
			t.Errorf("message/send from %s: task %q %q, want %q", label, task.Status.State, taskText(task), agentcca2a.TaskStatusCompleted)
		}
		if called == 0 {
			t.Errorf("message/send from %s: provider never called", label)
		}
		return
	}
	if task.Status.State != agentcca2a.TaskStatusFailed {
		t.Errorf("message/send from %s: task %q %q, want %q", label, task.Status.State, taskText(task), agentcca2a.TaskStatusFailed)
	}
	if called != 0 {
		t.Errorf("message/send from %s: provider called %d times, want 0", label, called)
	}
}

func TestA2AMessageSendJudgesAccessRulesOnCaller(t *testing.T) {
	for _, returnImmediately := range []bool{false, true} {
		for name, rules := range accessRuleCases {
			label := name
			if returnImmediately {
				label += " returnImmediately"
			}
			t.Run(label, func(t *testing.T) {
				upstream, hits := accessUpstream(t, a2aChatReply)
				s := a2aAccessServer(t, upstream.URL, rules, 0)
				checkA2AAccess(t, s, hits.Load, outsideClient, "", returnImmediately, false)
				checkA2AAccess(t, s, hits.Load, allowedClient, "", returnImmediately, true)
			})
		}
	}
}

// With one trusted proxy hop, the A2A caller is the rightmost
// X-Forwarded-For entry, as for chat.
func TestA2AMessageSendUsesTrustedForwardedHop(t *testing.T) {
	upstream, hits := accessUpstream(t, a2aChatReply)
	s := a2aAccessServer(t, upstream.URL, accessRules{
		org: &tenant.IPACLConfig{Enabled: true, Allow: []string{"10.0.0.0/8"}},
	}, 1)
	checkA2AAccess(t, s, hits.Load, proxyOutsideAllow, outsideClient, false, false)
	checkA2AAccess(t, s, hits.Load, proxyOutsideAllow, allowedClient, false, true)
}
