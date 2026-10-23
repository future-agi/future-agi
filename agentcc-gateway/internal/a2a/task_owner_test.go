package a2a

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// callerHeader lets these tests choose the caller per request: the value is
// "keyID/orgID".
const callerHeader = "X-Test-Caller"

func headerCaller(r *http.Request) (Caller, bool) {
	v := r.Header.Get(callerHeader)
	if v == "" {
		return Caller{}, false
	}
	keyID, orgID, _ := strings.Cut(v, "/")
	return Caller{KeyID: keyID, OrgID: orgID}, true
}

func newOwnedA2AServer(t *testing.T) *Server {
	t.Helper()
	s := NewServer(CardConfig{Name: "test", Version: "1"}, NewRegistry(nil), WithCaller(headerCaller))
	t.Cleanup(s.Close)
	return s
}

func postAs(t *testing.T, s *Server, caller, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest("POST", "/a2a", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if caller != "" {
		req.Header.Set(callerHeader, caller)
	}
	w := httptest.NewRecorder()
	s.HandleMessage(w, req)
	return w
}

func sendAs(t *testing.T, s *Server, caller string) *Task {
	t.Helper()
	w := postAs(t, s, caller, `{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"role":"user","parts":[{"type":"text","text":"hi"}]}}}`)
	resp := decodeA2AResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("message/send as %s: %s", caller, resp.Error.Message)
	}
	var task Task
	if err := json.Unmarshal(resp.Result, &task); err != nil || task.ID == "" {
		t.Fatalf("message/send as %s: no task in %s", caller, w.Body.String())
	}
	return &task
}

func taskCallAs(t *testing.T, s *Server, caller, method, taskID string) *mcp.Message {
	t.Helper()
	return decodeA2AResponse(t, postAs(t, s, caller, fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":%q,"params":{"taskId":%q}}`, method, taskID)))
}

func TestTaskRecordsItsCreator(t *testing.T) {
	s := newOwnedA2AServer(t)
	sent := sendAs(t, s, "key-a/org-a")
	task, ok := s.tasks.GetForOrg(sent.ID, "org-a")
	if !ok {
		t.Fatal("task not found for its own org")
	}
	if task.owner != (Caller{KeyID: "key-a", OrgID: "org-a"}) {
		t.Fatalf("task owner = %+v, want key-a in org-a", task.owner)
	}
	// The owner never reaches the wire.
	raw, _ := json.Marshal(task)
	if strings.Contains(string(raw), "org-a") || strings.Contains(string(raw), "key-a") {
		t.Fatalf("serialized task carries its owner: %s", raw)
	}
}

// Cancelling a running task calls its cancel function, which stops the
// provider call. Another org's cancel must not reach it or change the task.
func TestCancelOfAnotherOrgsRunningTaskLeavesItRunning(t *testing.T) {
	s := newOwnedA2AServer(t)
	task := &Task{ID: "task-running", Status: TaskStatus{State: TaskStatusWorking}, owner: Caller{KeyID: "key-a", OrgID: "org-a"}}
	s.tasks.Store(task)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.taskCancels.Store(task.ID, cancel)

	for _, caller := range []string{"key-b/org-b", "key-ops/"} {
		resp := taskCallAs(t, s, caller, MethodTasksCancel, task.ID)
		if resp.Error == nil || resp.Error.Message != "task not found: "+task.ID {
			t.Fatalf("tasks/cancel as %s: want task not found, got %+v", caller, resp)
		}
		if ctx.Err() != nil {
			t.Fatalf("tasks/cancel as %s cancelled org-a's running task", caller)
		}
		if task.Status.State != TaskStatusWorking {
			t.Fatalf("tasks/cancel as %s changed org-a's task to %q", caller, task.Status.State)
		}
	}

	// A key of the same org may cancel it.
	resp := taskCallAs(t, s, "key-a2/org-a", MethodTasksCancel, task.ID)
	if resp.Error != nil {
		t.Fatalf("tasks/cancel by org-a's second key: %s", resp.Error.Message)
	}
	if ctx.Err() == nil {
		t.Fatal("tasks/cancel by org-a's second key did not cancel the running task")
	}
}

func TestTasksAreScopedToTheCreatorsOrg(t *testing.T) {
	s := newOwnedA2AServer(t)
	orgTask := sendAs(t, s, "key-a/org-a")
	noOrgTask := sendAs(t, s, "key-ops/")

	cases := []struct {
		caller string
		taskID string
		found  bool
	}{
		{"key-a/org-a", orgTask.ID, true},
		{"key-a2/org-a", orgTask.ID, true},
		{"key-b/org-b", orgTask.ID, false},
		{"key-ops/", orgTask.ID, false},
		{"key-ops2/", noOrgTask.ID, true},
		{"key-a/org-a", noOrgTask.ID, false},
	}
	for _, c := range cases {
		resp := taskCallAs(t, s, c.caller, MethodTasksGet, c.taskID)
		if c.found && resp.Error != nil {
			t.Errorf("tasks/get %s as %s: %s", c.taskID, c.caller, resp.Error.Message)
		}
		if !c.found && (resp.Error == nil || resp.Error.Message != "task not found: "+c.taskID) {
			t.Errorf("tasks/get %s as %s: want task not found, got %+v", c.taskID, c.caller, resp)
		}
	}
}

// With auth on, a request with no authenticated caller is refused before it
// is read, and creates no task.
func TestRequestWithoutCallerIsRefused(t *testing.T) {
	s := newOwnedA2AServer(t)
	w := postAs(t, s, "", `{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"role":"user","parts":[{"type":"text","text":"hi"}]}}}`)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("status %d, want 401. Body: %s", w.Code, w.Body.String())
	}
	if !strings.HasPrefix(w.Header().Get("WWW-Authenticate"), "Bearer ") {
		t.Fatalf("WWW-Authenticate %q, want a Bearer challenge", w.Header().Get("WWW-Authenticate"))
	}
	s.tasks.mu.RLock()
	n := len(s.tasks.store.tasks)
	s.tasks.mu.RUnlock()
	if n != 0 {
		t.Fatalf("a refused request stored %d task(s)", n)
	}
}
