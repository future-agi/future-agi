package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	agentcca2a "github.com/futureagi/agentcc-gateway/internal/a2a"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// An A2A task belongs to the org of the API key that created it. tasks/get and
// tasks/cancel, on /a2a and on /v1/a2a, answer another org's task id exactly
// as they answer an id that does not exist, and leave the task alone. A key
// with no org reaches only tasks created with no org. Any key of the creating
// org may read and cancel the task.

const (
	a2aOrgA     = "org-a2a-a"
	a2aOrgB     = "org-a2a-b"
	a2aKeyA     = "sk-agentcc-a2a-org-a"
	a2aKeyA2    = "sk-agentcc-a2a-org-a-second"
	a2aKeyB     = "sk-agentcc-a2a-org-b"
	a2aKeyNoOrg = "sk-agentcc-a2a-no-org"
)

var a2aPaths = []string{"/a2a", "/v1/a2a"}

// newA2AScopeServer builds the whole gateway (KeyAuth, router, A2A server)
// with two keys in org A, one in org B and one with no org. Internal keys
// reach the configured provider whatever their org, so every task completes.
func newA2AScopeServer(t *testing.T, providerURL string) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "a2a-org-a", Key: a2aKeyA, Owner: "a", KeyType: "internal", Metadata: map[string]string{"org_id": a2aOrgA}},
		{Name: "a2a-org-a-second", Key: a2aKeyA2, Owner: "a", KeyType: "internal", Metadata: map[string]string{"org_id": a2aOrgA}},
		{Name: "a2a-org-b", Key: a2aKeyB, Owner: "b", KeyType: "internal", Metadata: map[string]string{"org_id": a2aOrgB}},
		{Name: "a2a-no-org", Key: a2aKeyNoOrg, Owner: "ops", KeyType: "internal"},
	}
	cfg.A2A.Enabled = true
	cfg.A2A.Card = config.A2ACardConfig{Name: "Agentcc LLM Gateway", Version: "1.0.0"}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   providerURL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o", "gpt-4o-mini"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func a2aCall(t *testing.T, srv *Server, path, key, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest("POST", path, bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func a2aDecode(t *testing.T, w *httptest.ResponseRecorder) mcp.Message {
	t.Helper()
	var msg mcp.Message
	if err := json.Unmarshal(w.Body.Bytes(), &msg); err != nil {
		t.Fatalf("a2a response is not JSON-RPC (status %d): %s", w.Code, w.Body.String())
	}
	return msg
}

// a2aSend creates a task with key on path and returns it. returnImmediately
// answers while the task is still running.
func a2aSend(t *testing.T, srv *Server, path, key string, returnImmediately bool) agentcca2a.Task {
	t.Helper()
	conf := ""
	if returnImmediately {
		conf = `,"configuration":{"returnImmediately":true}`
	}
	body := `{"jsonrpc":"2.0","id":1,"method":"tasks/send","params":{"message":{"role":"user","parts":[{"type":"text","text":"hello"}]},"metadata":{"model":"gpt-4o"}` + conf + `}}`
	w := a2aCall(t, srv, path, key, body)
	if w.Code != http.StatusOK {
		t.Fatalf("tasks/send with %s on %s: status %d body %s", key, path, w.Code, w.Body.String())
	}
	resp := a2aDecode(t, w)
	if resp.Error != nil {
		t.Fatalf("tasks/send with %s on %s: JSON-RPC error %s", key, path, resp.Error.Message)
	}
	var task agentcca2a.Task
	if err := json.Unmarshal(resp.Result, &task); err != nil || task.ID == "" {
		t.Fatalf("tasks/send with %s on %s: no task in %s", key, path, w.Body.String())
	}
	return task
}

func a2aTaskCall(t *testing.T, srv *Server, path, key, method, taskID string) *httptest.ResponseRecorder {
	t.Helper()
	return a2aCall(t, srv, path, key, fmt.Sprintf(`{"jsonrpc":"2.0","id":7,"method":%q,"params":{"task_id":%q}}`, method, taskID))
}

// requireA2ATask asserts the call returned the task with this id and returns it.
func requireA2ATask(t *testing.T, w *httptest.ResponseRecorder, taskID, what string) agentcca2a.Task {
	t.Helper()
	resp := a2aDecode(t, w)
	if w.Code != http.StatusOK || resp.Error != nil {
		t.Fatalf("%s: want the task, got status %d body %s", what, w.Code, w.Body.String())
	}
	var task agentcca2a.Task
	if err := json.Unmarshal(resp.Result, &task); err != nil || task.ID != taskID {
		t.Fatalf("%s: want task %s, got %s", what, taskID, w.Body.String())
	}
	return task
}

// requireA2ANotFound asserts the call got the answer an unknown task id gets:
// the same status, and the same body once the id is swapped. So a task id from
// another org reveals nothing, not even that the task exists.
func requireA2ANotFound(t *testing.T, srv *Server, w *httptest.ResponseRecorder, path, key, method, taskID, what string) {
	t.Helper()
	const missingID = "task-000000000000000000000000"
	missing := a2aTaskCall(t, srv, path, key, method, missingID)
	if missing.Code != http.StatusOK || a2aDecode(t, missing).Error == nil {
		t.Fatalf("%s: an unknown task id answered status %d body %s; want a JSON-RPC error", what, missing.Code, missing.Body.String())
	}
	if w.Code != missing.Code {
		t.Fatalf("%s: status %d, an unknown task id gets %d. Body: %s", what, w.Code, missing.Code, w.Body.String())
	}
	got := bytes.ReplaceAll(w.Body.Bytes(), []byte(taskID), []byte(missingID))
	if !bytes.Equal(got, missing.Body.Bytes()) {
		t.Fatalf("%s: answered %s; an unknown task id answers %s", what, w.Body.String(), missing.Body.String())
	}
}

func TestA2ATasks_AnotherOrgCannotReadATask(t *testing.T) {
	mock := startValidatedMockOpenAI(t, 0)
	defer mock.Close()
	srv := newA2AScopeServer(t, mock.URL)

	for _, createPath := range a2aPaths {
		task := a2aSend(t, srv, createPath, a2aKeyA, false)
		for _, path := range a2aPaths {
			w := a2aTaskCall(t, srv, path, a2aKeyB, "tasks/get", task.ID)
			requireA2ANotFound(t, srv, w, path, a2aKeyB, "tasks/get", task.ID,
				fmt.Sprintf("org B tasks/get on %s for org A's task created on %s", path, createPath))

			w = a2aTaskCall(t, srv, path, a2aKeyNoOrg, "tasks/get", task.ID)
			requireA2ANotFound(t, srv, w, path, a2aKeyNoOrg, "tasks/get", task.ID,
				fmt.Sprintf("no-org key tasks/get on %s for org A's task created on %s", path, createPath))

			// Any key of the creating org reads it.
			for _, key := range []string{a2aKeyA, a2aKeyA2} {
				got := requireA2ATask(t, a2aTaskCall(t, srv, path, key, "tasks/get", task.ID), task.ID,
					fmt.Sprintf("org A key %s tasks/get on %s", key, path))
				if got.Status.State != agentcca2a.TaskStatusCompleted {
					t.Fatalf("org A's task state = %q, want completed", got.Status.State)
				}
			}
		}
	}
}

func TestA2ATasks_NoOrgTasksAreOnlyForNoOrgKeys(t *testing.T) {
	mock := startValidatedMockOpenAI(t, 0)
	defer mock.Close()
	srv := newA2AScopeServer(t, mock.URL)

	task := a2aSend(t, srv, "/a2a", a2aKeyNoOrg, false)
	for _, path := range a2aPaths {
		for _, key := range []string{a2aKeyA, a2aKeyB} {
			w := a2aTaskCall(t, srv, path, key, "tasks/get", task.ID)
			requireA2ANotFound(t, srv, w, path, key, "tasks/get", task.ID,
				fmt.Sprintf("org key %s tasks/get on %s for a no-org task", key, path))
			w = a2aTaskCall(t, srv, path, key, "tasks/cancel", task.ID)
			requireA2ANotFound(t, srv, w, path, key, "tasks/cancel", task.ID,
				fmt.Sprintf("org key %s tasks/cancel on %s for a no-org task", key, path))
		}
		requireA2ATask(t, a2aTaskCall(t, srv, path, a2aKeyNoOrg, "tasks/get", task.ID), task.ID,
			"no-org key tasks/get on "+path+" for its own task")
	}
}

func TestA2ATasks_AnotherOrgCannotCancelAFinishedTask(t *testing.T) {
	mock := startValidatedMockOpenAI(t, 0)
	defer mock.Close()
	srv := newA2AScopeServer(t, mock.URL)

	task := a2aSend(t, srv, "/v1/a2a", a2aKeyA, false)
	for _, path := range a2aPaths {
		w := a2aTaskCall(t, srv, path, a2aKeyB, "tasks/cancel", task.ID)
		requireA2ANotFound(t, srv, w, path, a2aKeyB, "tasks/cancel", task.ID,
			"org B tasks/cancel on "+path+" for org A's task")
		w = a2aTaskCall(t, srv, path, a2aKeyNoOrg, "tasks/cancel", task.ID)
		requireA2ANotFound(t, srv, w, path, a2aKeyNoOrg, "tasks/cancel", task.ID,
			"no-org key tasks/cancel on "+path+" for org A's task")
	}
	got := requireA2ATask(t, a2aTaskCall(t, srv, "/a2a", a2aKeyA, "tasks/get", task.ID), task.ID, "org A tasks/get after org B's cancel")
	if got.Status.State != agentcca2a.TaskStatusCompleted {
		t.Fatalf("org A's task state after org B's cancel = %q, want completed", got.Status.State)
	}

	// A second key of org A may cancel it.
	got = requireA2ATask(t, a2aTaskCall(t, srv, "/a2a", a2aKeyA2, "tasks/cancel", task.ID), task.ID, "org A second key tasks/cancel")
	if got.Status.State != agentcca2a.TaskStatusCanceled {
		t.Fatalf("org A second key cancel: state %q, want canceled", got.Status.State)
	}
}

// startHeldMockOpenAI answers chat completions only once release is closed.
// It reports on outcome whether the gateway hung up first ("canceled") or the
// answer was written ("served"), and on started when a call arrives.
func startHeldMockOpenAI(t *testing.T) (srv *httptest.Server, started chan struct{}, release chan struct{}, outcome chan string) {
	t.Helper()
	started = make(chan struct{}, 1)
	release = make(chan struct{})
	outcome = make(chan string, 1)
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var req models.ChatCompletionRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		select {
		case started <- struct{}{}:
		default:
		}
		select {
		case <-r.Context().Done():
			outcome <- "canceled"
			return
		case <-release:
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.ChatCompletionResponse{
			ID: "chatcmpl-held", Object: "chat.completion", Created: 1700000000, Model: req.Model,
			Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"held response"`)}, FinishReason: "stop"}},
		})
		outcome <- "served"
	}))
	return srv, started, release, outcome
}

// Cancelling a running task stops its provider call. Another org's cancel must
// not reach that call.
func TestA2ATasks_AnotherOrgCannotCancelARunningTask(t *testing.T) {
	mock, started, release, outcome := startHeldMockOpenAI(t)
	defer mock.Close()
	srv := newA2AScopeServer(t, mock.URL)

	task := a2aSend(t, srv, "/a2a", a2aKeyA, true)
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		close(release)
		t.Fatal("org A's task never reached the provider")
	}

	w := a2aTaskCall(t, srv, "/v1/a2a", a2aKeyB, "tasks/cancel", task.ID)
	if resp := a2aDecode(t, w); resp.Error == nil {
		t.Errorf("org B tasks/cancel for org A's running task answered %s, want a JSON-RPC error", w.Body.String())
	}

	select {
	case got := <-outcome:
		t.Fatalf("org A's running provider call ended %q during org B's cancel, want it still running", got)
	case <-time.After(300 * time.Millisecond):
	}
	close(release)
	select {
	case got := <-outcome:
		if got != "served" {
			t.Fatalf("org A's provider call outcome %q, want served", got)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("org A's provider call never finished")
	}
}
