package a2a

import (
	"encoding/json"
	"testing"
	"time"
)

func TestAgentCardMarshal(t *testing.T) {
	card := AgentCard{
		Name:    "test-agent",
		URL:     "https://example.com/a2a",
		Version: "1.0.0",
		Capabilities: AgentCapabilities{
			Streaming: true,
		},
		Skills: []Skill{
			{ID: "s1", Name: "Search", Description: "Search the web"},
		},
	}

	data, err := json.Marshal(card)
	if err != nil {
		t.Fatal(err)
	}

	var decoded AgentCard
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.Name != "test-agent" {
		t.Fatalf("expected test-agent, got %s", decoded.Name)
	}
	if !decoded.Capabilities.Streaming {
		t.Fatal("expected streaming capability")
	}
	if len(decoded.Skills) != 1 {
		t.Fatalf("expected 1 skill, got %d", len(decoded.Skills))
	}
}

func TestTaskMarshal(t *testing.T) {
	task := Task{
		ID: "task-123",
		Status: TaskStatus{
			State:   TaskStatusCompleted,
			Message: []MessagePart{{Type: "text", Text: "done"}},
		},
		Artifacts: []Artifact{
			{
				Name:  "output",
				Index: 0,
				Parts: []MessagePart{{Type: "text", Text: "hello"}},
			},
		},
	}

	data, err := json.Marshal(task)
	if err != nil {
		t.Fatal(err)
	}

	var decoded Task
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.ID != "task-123" {
		t.Fatalf("expected task-123, got %s", decoded.ID)
	}
	if decoded.Status.State != TaskStatusCompleted {
		t.Fatalf("expected completed, got %s", decoded.Status.State)
	}
}

func TestMessageSendParamsMarshal(t *testing.T) {
	params := MessageSendParams{
		Message: Message{
			Role: "user",
			Parts: []MessagePart{
				{Type: "text", Text: "Hello world"},
			},
		},
	}

	data, err := json.Marshal(params)
	if err != nil {
		t.Fatal(err)
	}

	var decoded MessageSendParams
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.Message.Role != "user" {
		t.Fatalf("expected user role, got %s", decoded.Message.Role)
	}
}

func TestTextMessageHelper(t *testing.T) {
	msg := TextMessage("user", "hello")
	if msg.Role != "user" {
		t.Fatalf("expected user, got %s", msg.Role)
	}
	if len(msg.Parts) != 1 {
		t.Fatalf("expected 1 part, got %d", len(msg.Parts))
	}
	if msg.Parts[0].Text != "hello" {
		t.Fatalf("expected hello, got %s", msg.Parts[0].Text)
	}
}

func TestExtractText(t *testing.T) {
	task := &Task{
		Artifacts: []Artifact{
			{Parts: []MessagePart{
				{Type: "text", Text: "line 1"},
				{Type: "file", URI: "ignored"},
				{Type: "text", Text: "line 2"},
			}},
		},
	}
	text := ExtractText(task)
	if text != "line 1\nline 2" {
		t.Fatalf("expected 'line 1\\nline 2', got %q", text)
	}
}

func TestExtractTextEmpty(t *testing.T) {
	task := &Task{}
	if ExtractText(task) != "" {
		t.Fatal("expected empty string for no artifacts")
	}
}

func TestTaskStore(t *testing.T) {
	ts := NewTaskStore()

	task := &Task{ID: "task-1", Status: TaskStatus{State: TaskStatusCompleted}}
	ts.Store(task)

	got, ok := ts.Get("task-1")
	if !ok {
		t.Fatal("expected to find task")
	}
	if got.ID != "task-1" {
		t.Fatalf("expected task-1, got %s", got.ID)
	}

	_, ok = ts.Get("nonexistent")
	if ok {
		t.Fatal("expected not found")
	}
}

func TestTaskStoreReturnsSnapshots(t *testing.T) {
	ts := NewTaskStore()
	original := &Task{
		ID:       "task-1",
		Metadata: json.RawMessage(`{"task":"original"}`),
		Status: TaskStatus{
			State:   TaskStatusWorking,
			Message: []MessagePart{{Type: "text", Text: "status", Data: json.RawMessage(`{"status":"original"}`)}},
		},
		Artifacts: []Artifact{{
			Name:     "artifact",
			Parts:    []MessagePart{{Type: "text", Text: "artifact", Data: json.RawMessage(`{"artifact":"original"}`)}},
			Metadata: json.RawMessage(`{"artifact_meta":"original"}`),
		}},
		History: []Message{{
			Role:     "user",
			Parts:    []MessagePart{{Type: "text", Text: "history", Data: json.RawMessage(`{"history":"original"}`)}},
			Metadata: json.RawMessage(`{"history_meta":"original"}`),
		}},
	}
	ts.Store(original)

	original.Status.State = TaskStatusFailed
	original.Status.Message[0].Text = "mutated"
	original.Artifacts[0].Parts[0].Text = "mutated"
	original.History[0].Parts[0].Text = "mutated"
	original.Metadata[0] = 'X'

	got, ok := ts.Get(original.ID)
	if !ok {
		t.Fatal("expected stored task")
	}
	if got.Status.State != TaskStatusWorking || got.Status.Message[0].Text != "status" {
		t.Fatalf("stored status was mutated through caller: %#v", got.Status)
	}
	if got.Artifacts[0].Parts[0].Text != "artifact" || got.History[0].Parts[0].Text != "history" {
		t.Fatalf("stored nested fields were mutated through caller: %#v", got)
	}
	if string(got.Metadata) != `{"task":"original"}` {
		t.Fatalf("stored metadata was mutated through caller: %s", got.Metadata)
	}

	got.Status.State = TaskStatusCompleted
	got.Status.Message[0].Text = "mutated snapshot"
	got.Artifacts[0].Parts[0].Text = "mutated snapshot"
	got.History[0].Parts[0].Text = "mutated snapshot"
	got.Metadata[0] = 'Y'

	again, ok := ts.Get(original.ID)
	if !ok {
		t.Fatal("expected stored task on second get")
	}
	if again.Status.State != TaskStatusWorking || again.Status.Message[0].Text != "status" {
		t.Fatalf("stored status was mutated through snapshot: %#v", again.Status)
	}
	if again.Artifacts[0].Parts[0].Text != "artifact" || again.History[0].Parts[0].Text != "history" {
		t.Fatalf("stored nested fields were mutated through snapshot: %#v", again)
	}
	if string(again.Metadata) != `{"task":"original"}` {
		t.Fatalf("stored metadata was mutated through snapshot: %s", again.Metadata)
	}
}

func TestTaskStoreCleanup(t *testing.T) {
	ts := NewTaskStore()

	task := &Task{ID: "old-task"}
	ts.Store(task)

	// Cleanup with zero duration removes everything.
	ts.Cleanup(0)

	_, ok := ts.Get("old-task")
	if ok {
		t.Fatal("expected task to be cleaned up")
	}
}

func TestTaskStoreCleanupKeepsRecent(t *testing.T) {
	ts := NewTaskStore()
	ts.Store(&Task{ID: "recent"})

	ts.Cleanup(1 * time.Hour)

	_, ok := ts.Get("recent")
	if !ok {
		t.Fatal("expected recent task to survive cleanup")
	}
}
