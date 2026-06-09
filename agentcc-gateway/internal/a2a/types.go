// Package a2a implements Google's Agent-to-Agent (A2A) protocol.
// Agentcc acts as both an A2A server (accepts tasks) and A2A client
// (delegates to external agents via the a2a/ model prefix).
package a2a

import (
	"encoding/json"
	"time"
)

// A2A JSON-RPC method constants.
const (
	MethodMessageSend   = "message/send"
	MethodTasksSend     = "tasks/send"
	MethodMessageStream = "message/stream"
	MethodTasksGet      = "tasks/get"
	MethodTasksCancel   = "tasks/cancel"
)

// Task status constants.
const (
	TaskStatusWorking       = "working"
	TaskStatusCompleted     = "completed"
	TaskStatusFailed        = "failed"
	TaskStatusCanceled      = "canceled"
	TaskStatusInputRequired = "input_required"
)

// AgentCard describes an A2A agent's capabilities.
type AgentCard struct {
	Name               string            `json:"name"`
	Description        string            `json:"description,omitempty"`
	URL                string            `json:"url"`
	Version            string            `json:"version"`
	Capabilities       AgentCapabilities `json:"capabilities"`
	Skills             []Skill           `json:"skills,omitempty"`
	SecuritySchemes    []SecurityScheme  `json:"securitySchemes,omitempty"`
	DefaultInputModes  []string          `json:"defaultInputModes,omitempty"`
	DefaultOutputModes []string          `json:"defaultOutputModes,omitempty"`
}

// AgentCapabilities declares what an agent supports.
type AgentCapabilities struct {
	Streaming         bool `json:"streaming,omitempty"`
	PushNotifications bool `json:"pushNotifications,omitempty"`
}

// Skill describes something an agent can do.
type Skill struct {
	ID          string   `json:"id"`
	Name        string   `json:"name"`
	Description string   `json:"description,omitempty"`
	Tags        []string `json:"tags,omitempty"`
	Examples    []string `json:"examples,omitempty"`
}

// SecurityScheme describes an auth method.
type SecurityScheme struct {
	Type   string `json:"type"` // "bearer", "apiKey"
	Name   string `json:"name,omitempty"`
	In     string `json:"in,omitempty"` // "header", "query"
	Scheme string `json:"scheme,omitempty"`
}

// Task represents an A2A task.
type Task struct {
	ID        string          `json:"id"`
	ContextID string          `json:"contextId,omitempty"`
	Status    TaskStatus      `json:"status"`
	Artifacts []Artifact      `json:"artifacts,omitempty"`
	History   []Message       `json:"history,omitempty"`
	Metadata  json.RawMessage `json:"metadata,omitempty"`
}

// TaskStatus describes the current state of a task.
type TaskStatus struct {
	State   string        `json:"state"` // working, completed, failed, canceled, input_required
	Message []MessagePart `json:"message,omitempty"`
}

// Message is a conversation message in A2A.
type Message struct {
	Role     string          `json:"role"` // "user" or "agent"
	Parts    []MessagePart   `json:"parts"`
	Metadata json.RawMessage `json:"metadata,omitempty"`
}

// MessagePart is a union type for content parts.
type MessagePart struct {
	Type     string          `json:"type"` // "text", "file", "data"
	Text     string          `json:"text,omitempty"`
	MIMEType string          `json:"mimeType,omitempty"`
	Data     json.RawMessage `json:"data,omitempty"`
	URI      string          `json:"uri,omitempty"`
}

// Artifact is an output from a task.
type Artifact struct {
	Name        string          `json:"name,omitempty"`
	Description string          `json:"description,omitempty"`
	Parts       []MessagePart   `json:"parts"`
	Metadata    json.RawMessage `json:"metadata,omitempty"`
	Index       int             `json:"index"`
}

// TaskEvent is an SSE event for streaming task updates.
type TaskEvent struct {
	Type string `json:"type"` // "task/status-update", "task/artifact-update"
	Task *Task  `json:"task,omitempty"`
}

// --- Request/Response Types ---

// MessageSendParams is the params for message/send.
type MessageSendParams struct {
	Message       Message                   `json:"message"`
	ContextID     string                    `json:"contextId,omitempty"`
	Metadata      json.RawMessage           `json:"metadata,omitempty"`
	Configuration *MessageSendConfiguration `json:"configuration,omitempty"`
}

// MessageSendConfiguration controls task execution behavior.
type MessageSendConfiguration struct {
	ReturnImmediately bool `json:"returnImmediately,omitempty"`
}

// TaskGetParams is the params for tasks/get.
type TaskGetParams struct {
	TaskID      string `json:"taskId,omitempty"`
	TaskIDSnake string `json:"task_id,omitempty"`
}

// TaskCancelParams is the params for tasks/cancel.
type TaskCancelParams struct {
	TaskID      string `json:"taskId,omitempty"`
	TaskIDSnake string `json:"task_id,omitempty"`
}

// --- Helper Types ---

// A2AAuth holds auth settings for connecting to an external agent.
type A2AAuth struct {
	Type   string `yaml:"type" json:"type"`     // "bearer", "api_key", "none"
	Token  string `yaml:"token" json:"token"`   // for bearer
	Header string `yaml:"header" json:"header"` // for api_key
	Key    string `yaml:"key" json:"key"`       // for api_key
}

// TaskStore is an in-memory store for active tasks.
type TaskStore struct {
	tasks map[string]*taskEntry
}

type taskEntry struct {
	task      *Task
	createdAt time.Time
}

// NewTaskStore creates a task store.
func NewTaskStore() *TaskStore {
	return &TaskStore{
		tasks: make(map[string]*taskEntry),
	}
}

// Store saves a task.
func (ts *TaskStore) Store(task *Task) {
	ts.tasks[task.ID] = &taskEntry{task: task, createdAt: time.Now()}
}

// Get retrieves a task by ID.
func (ts *TaskStore) Get(id string) (*Task, bool) {
	e, ok := ts.tasks[id]
	if !ok {
		return nil, false
	}
	return e.task, true
}

// Cleanup removes tasks older than the given duration.
func (ts *TaskStore) Cleanup(maxAge time.Duration) {
	cutoff := time.Now().Add(-maxAge)
	for id, e := range ts.tasks {
		if e.createdAt.Before(cutoff) {
			delete(ts.tasks, id)
		}
	}
}
