package a2a

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"slices"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// Server handles incoming A2A requests (other agents sending tasks to Agentcc).
type Server struct {
	cardGen          *CardGenerator
	registry         *Registry
	tasks            *syncTaskStore
	taskCancels      sync.Map
	chatExecutor     ChatCompletionExecutor
	engine           *pipeline.Engine
	providerRegistry *providers.Registry
	caller           CallerFunc
	done             chan struct{}
}

// Caller is the API key a request was authenticated with. A task records the
// Caller that created it: the task belongs to that key's org.
type Caller struct {
	KeyID string
	OrgID string // "" for a key with no org, a license-authorized request, or auth off
}

// CallerFunc returns the Caller of an A2A request. ok is false when the
// request carries no authenticated caller although auth is on: such a request
// is refused, never served as a caller with no org.
type CallerFunc func(r *http.Request) (caller Caller, ok bool)

// singleTenant is the CallerFunc of a server built without WithCaller (auth
// off): every request is the same caller with no org.
func singleTenant(*http.Request) (Caller, bool) { return Caller{}, true }

type ChatCompletionExecutor func(ctx context.Context, authHeader string, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error)

// syncTaskStore is a thread-safe, copy-on-write task store. It never shares a
// *Task with a caller: Store saves a copy and every read returns one, so a
// background execution, a cancel and a response encoder each work on their
// own copy and meet only under mu.
type syncTaskStore struct {
	mu    sync.RWMutex
	store *TaskStore
}

func newSyncTaskStore() *syncTaskStore {
	return &syncTaskStore{store: NewTaskStore()}
}

func (s *syncTaskStore) Store(task *Task) {
	snapshot := cloneTask(task)
	s.mu.Lock()
	s.store.Store(snapshot)
	s.mu.Unlock()
}

// StoreResult saves the outcome of a task's execution unless the stored task
// is already canceled: a cancel is final, so a result that loses the race
// with it is dropped.
func (s *syncTaskStore) StoreResult(task *Task) {
	snapshot := cloneTask(task)
	s.mu.Lock()
	defer s.mu.Unlock()
	if cur, ok := s.store.Get(task.ID); ok && cur.Status.State == TaskStatusCanceled {
		return
	}
	s.store.Store(snapshot)
}

// CancelForOrg marks a stored task canceled, whatever its state, and returns a
// copy, but only for the org that created it. Another org's task is reported
// missing, exactly like an unknown id, and is left unchanged; a caller with no
// org reaches only tasks created with no org.
func (s *syncTaskStore) CancelForOrg(id, orgID string) (*Task, bool) {
	if id == "" {
		return nil, false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	cur, ok := s.store.Get(id)
	if !ok || cur.owner.OrgID != orgID {
		return nil, false
	}
	canceled := cloneTask(cur)
	canceled.Status = TaskStatus{
		State:   TaskStatusCanceled,
		Message: []MessagePart{{Type: "text", Text: "Task canceled"}},
	}
	s.store.Store(canceled)
	return cloneTask(canceled), true
}

// GetForOrg returns a copy of the task only to the org that created it.
// Another org's task is reported missing, exactly like an unknown id; a caller
// with no org reaches only tasks created with no org.
func (s *syncTaskStore) GetForOrg(id, orgID string) (*Task, bool) {
	if id == "" {
		return nil, false
	}
	s.mu.RLock()
	defer s.mu.RUnlock()
	task, ok := s.store.Get(id)
	if !ok || task.owner.OrgID != orgID {
		return nil, false
	}
	return cloneTask(task), true
}

// cloneTask copies every slice a writer could replace or modify. Raw JSON
// fields are never modified in place, so the copy shares them.
func cloneTask(task *Task) *Task {
	c := *task
	c.Status.Message = slices.Clone(task.Status.Message)
	c.Artifacts = slices.Clone(task.Artifacts)
	for i := range c.Artifacts {
		c.Artifacts[i].Parts = slices.Clone(c.Artifacts[i].Parts)
	}
	c.History = slices.Clone(task.History)
	for i := range c.History {
		c.History[i].Parts = slices.Clone(c.History[i].Parts)
	}
	return &c
}

func (s *syncTaskStore) Cleanup(maxAge time.Duration) {
	s.mu.Lock()
	s.store.Cleanup(maxAge)
	s.mu.Unlock()
}

// NewServer creates an A2A server.
// If engine and providerRegistry are provided, message/send routes through the
// full pipeline (pre-plugins → provider call → post-plugins). Otherwise it
// falls back to a simple echo for testing.
func NewServer(cardCfg CardConfig, registry *Registry, opts ...ServerOption) *Server {
	s := &Server{
		cardGen:  NewCardGenerator(cardCfg),
		registry: registry,
		tasks:    newSyncTaskStore(),
		caller:   singleTenant,
		done:     make(chan struct{}),
	}
	for _, opt := range opts {
		opt(s)
	}

	// Background cleanup every 10 minutes.
	go func() {
		ticker := time.NewTicker(10 * time.Minute)
		defer ticker.Stop()
		for {
			select {
			case <-s.done:
				return
			case <-ticker.C:
				s.tasks.Cleanup(1 * time.Hour)
			}
		}
	}()

	return s
}

// HandleAgentCard serves GET /.well-known/agent.json.
func (s *Server) HandleAgentCard(w http.ResponseWriter, r *http.Request) {
	s.cardGen.HandleAgentCard(w, r)
}

// HandleMessage handles POST /a2a — the main A2A JSON-RPC endpoint.
func (s *Server) HandleMessage(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.caller(r)
	if !ok {
		writeA2AUnauthorized(w)
		return
	}

	var msg mcp.Message
	if err := json.NewDecoder(r.Body).Decode(&msg); err != nil {
		writeA2AError(w, nil, mcp.ErrCodeParse, "invalid JSON: "+err.Error())
		return
	}

	if msg.JSONRPC != "2.0" {
		writeA2AError(w, msg.ID, mcp.ErrCodeInvalidRequest, "expected jsonrpc 2.0")
		return
	}

	switch msg.Method {
	case MethodMessageSend, MethodTasksSend:
		s.handleMessageSend(w, r, &msg, caller)
	case MethodTasksGet:
		s.handleTasksGet(w, &msg, caller)
	case MethodTasksCancel:
		s.handleTasksCancel(w, &msg, caller)
	default:
		writeA2AError(w, msg.ID, mcp.ErrCodeMethodNotFound, "unknown method: "+msg.Method)
	}
}

// ListAgents serves GET /v1/agents — returns configured external agents.
func (s *Server) ListAgents(w http.ResponseWriter, r *http.Request) {
	agents := s.registry.List()

	type agentInfo struct {
		Name        string     `json:"name"`
		URL         string     `json:"url"`
		Description string     `json:"description,omitempty"`
		Skills      []Skill    `json:"skills,omitempty"`
		Healthy     bool       `json:"healthy"`
		Card        *AgentCard `json:"card,omitempty"`
	}

	result := make([]agentInfo, 0, len(agents))
	for _, a := range agents {
		result = append(result, agentInfo{
			Name:        a.Name,
			URL:         a.URL,
			Description: a.Description,
			Skills:      a.Skills,
			Healthy:     a.Healthy(),
			Card:        a.Card,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(result)
}

// ServerOption configures an A2A server.
type ServerOption func(*Server)

// WithPipeline enables pipeline integration for message/send.
func WithPipeline(engine *pipeline.Engine, providerRegistry *providers.Registry) ServerOption {
	return func(s *Server) {
		s.engine = engine
		s.providerRegistry = providerRegistry
	}
}

// WithCaller sets how the server learns who sent a request: the gateway passes
// the identity its KeyAuth recorded. Tasks are scoped to the org it returns.
// Without it every request is the same caller with no org (auth off).
func WithCaller(fn CallerFunc) ServerOption {
	return func(s *Server) {
		if fn != nil {
			s.caller = fn
		}
	}
}

// WithChatCompletionExecutor routes A2A execution through an existing chat-completion path.
func WithChatCompletionExecutor(executor ChatCompletionExecutor) ServerOption {
	return func(s *Server) {
		s.chatExecutor = executor
	}
}

func (s *Server) handleMessageSend(w http.ResponseWriter, r *http.Request, msg *mcp.Message, caller Caller) {
	var params MessageSendParams
	if err := json.Unmarshal(msg.Params, &params); err != nil {
		writeA2AError(w, msg.ID, mcp.ErrCodeInvalidParams, "invalid message/send params")
		return
	}

	// Create task. It belongs to the caller's org from here on.
	taskID := generateTaskID()
	task := &Task{
		ID: taskID,
		Status: TaskStatus{
			State: TaskStatusWorking,
		},
		History: []Message{params.Message},
		owner:   caller,
	}

	// Extract text from the A2A message.
	var inputText string
	for _, part := range params.Message.Parts {
		if part.Type == "text" {
			if inputText != "" {
				inputText += "\n"
			}
			inputText += part.Text
		}
	}

	if inputText == "" {
		task.Status.State = TaskStatusFailed
		task.Status.Message = []MessagePart{
			{Type: "text", Text: "No text content in message"},
		}
		s.tasks.Store(task)
		writeA2AResult(w, msg.ID, task)
		return
	}

	// If pipeline is wired, route through the full plugin pipeline.
	if s.engine != nil && s.providerRegistry != nil {
		traceID, clientIP := requestTraceID(w, r), middleware.ClientIP(r)
		if params.Configuration != nil && params.Configuration.ReturnImmediately {
			s.tasks.Store(task)
			ctx, cancel := taskContext(traceID, clientIP)
			s.taskCancels.Store(task.ID, cancel)
			// The execution gets its own copy: it writes the result while this
			// handler is still encoding the accepted task. It releases its
			// context when it returns, as the blocking path does.
			authHeader, execTask := r.Header.Get("Authorization"), cloneTask(task)
			go func() {
				defer cancel()
				s.runMessageSendWithPipeline(ctx, authHeader, execTask, inputText, params)
			}()
			writeA2AResult(w, msg.ID, task)
			return
		}

		s.handleMessageSendWithPipeline(w, msg, r.Header.Get("Authorization"), traceID, clientIP, task, inputText, params)
		return
	}

	// Fallback: echo back (used in tests without full pipeline).
	task.Status.State = TaskStatusCompleted
	task.Status.Message = []MessagePart{
		{Type: "text", Text: "Task received and processed by Agentcc gateway"},
	}
	task.Artifacts = []Artifact{
		{
			Name:  "response",
			Index: 0,
			Parts: []MessagePart{
				{Type: "text", Text: fmt.Sprintf("Received: %s", inputText)},
			},
		},
	}
	s.tasks.Store(task)
	slog.Info("a2a task completed (echo)", "task_id", task.ID)
	writeA2AResult(w, msg.ID, task)
}

// handleMessageSendWithPipeline converts the A2A message to a ChatCompletionRequest,
// processes it through the full pipeline (pre-plugins → provider → post-plugins),
// and converts the response back to an A2A task.
func (s *Server) handleMessageSendWithPipeline(w http.ResponseWriter, msg *mcp.Message, authHeader, traceID, clientIP string, task *Task, inputText string, params MessageSendParams) {
	ctx, cancel := taskContext(traceID, clientIP)
	defer cancel()
	s.runMessageSendWithPipeline(ctx, authHeader, task, inputText, params)
	writeA2AResult(w, msg.ID, task)
}

// requestTraceID is the trace id of the A2A request r: the one the gateway's
// RequestID middleware set on its response (the caller's x-agentcc-trace-id,
// or one it made), else the caller's header. The task's run carries it.
func requestTraceID(w http.ResponseWriter, r *http.Request) string {
	if id := w.Header().Get("x-agentcc-trace-id"); id != "" {
		return id
	}
	return r.Header.Get("x-agentcc-trace-id")
}

// taskContext is the context a task's run gets: 120 s, apart from the A2A
// request's own context (a return-immediately task outlives it), carrying
// that request's trace id and its client IP (middleware.ClientIP), so every
// IP rule (ip_acl, a key's allowed_ips) judges the task's model call by the
// caller's address.
func taskContext(traceID, clientIP string) (context.Context, context.CancelFunc) {
	ctx := middleware.WithClientIP(models.WithTraceID(context.Background(), traceID), clientIP)
	return context.WithTimeout(ctx, 120*time.Second)
}

func (s *Server) runMessageSendWithPipeline(ctx context.Context, authHeader string, task *Task, inputText string, params MessageSendParams) {
	defer s.taskCancels.Delete(task.ID)

	model := s.resolveA2AModel(params)
	if model == "" {
		task.Status.State = TaskStatusFailed
		task.Status.Message = []MessagePart{{Type: "text", Text: "A2A request must include metadata.model"}}
		s.tasks.StoreResult(task)
		slog.Warn("a2a: missing metadata.model", "task_id", task.ID)
		return
	}

	// Build OpenAI ChatCompletionRequest.
	contentJSON, _ := json.Marshal(inputText)
	req := &models.ChatCompletionRequest{
		Model: model,
		Messages: []models.Message{
			{Role: "user", Content: contentJSON},
		},
	}

	if s.chatExecutor != nil {
		resp, err := s.chatExecutor(ctx, authHeader, req)
		if err != nil {
			if errors.Is(err, context.Canceled) || errors.Is(ctx.Err(), context.Canceled) {
				task.Status.State = TaskStatusCanceled
				task.Status.Message = []MessagePart{{Type: "text", Text: "Task canceled"}}
				s.tasks.StoreResult(task)
				return
			}
			task.Status.State = TaskStatusFailed
			task.Status.Message = []MessagePart{{Type: "text", Text: fmt.Sprintf("Pipeline error: %s", err.Error())}}
			s.tasks.StoreResult(task)
			return
		}
		s.finishTaskFromResponse(task, resp)
		s.tasks.StoreResult(task)
		return
	}

	// Acquire RequestContext from pool.
	rc := models.AcquireRequestContext()
	defer rc.Release()

	// The run's own ids, as the RequestID middleware gives a request: a new
	// request id, and the trace id of the A2A request that started the task
	// (one of its own when that request had none).
	rc.RequestID = middleware.NewID()
	rc.TraceID = models.GetTraceID(ctx)
	if rc.TraceID == "" {
		rc.TraceID = middleware.NewID()
	}

	rc.Model = model
	rc.Request = req
	rc.IsStream = false
	rc.SetMetadata("a2a_task_id", task.ID)
	rc.SetMetadata("a2a_context_id", params.ContextID)
	rc.SetMetadata("endpoint_type", "a2a")

	// Resolve provider.
	provider, err := s.providerRegistry.Resolve(model)
	if err != nil {
		task.Status.State = TaskStatusFailed
		task.Status.Message = []MessagePart{
			{Type: "text", Text: fmt.Sprintf("No provider for model %q: %s", model, err.Error())},
		}
		s.tasks.StoreResult(task)
		slog.Warn("a2a: provider resolution failed", "model", model, "error", err)
		return
	}
	rc.Provider = provider.ID()

	// Define provider call function.
	providerCall := func(callCtx context.Context, callRC *models.RequestContext) error {
		resp, err := provider.ChatCompletion(callCtx, callRC.Request)
		if err != nil {
			return err
		}
		callRC.Response = resp
		callRC.ResolvedModel = resp.Model
		return nil
	}

	// Execute pipeline.
	if err := s.engine.Process(ctx, rc, providerCall); err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(ctx.Err(), context.Canceled) {
			task.Status.State = TaskStatusCanceled
			task.Status.Message = []MessagePart{{Type: "text", Text: "Task canceled"}}
			s.tasks.StoreResult(task)
			slog.Info("a2a task canceled", "task_id", task.ID)
			return
		}

		task.Status.State = TaskStatusFailed
		task.Status.Message = []MessagePart{{Type: "text", Text: fmt.Sprintf("Pipeline error: %s", err.Error())}}
		s.tasks.StoreResult(task)
		slog.Warn("a2a: pipeline error", "task_id", task.ID, "error", err)
		return
	}
	// An output check that withheld the answer (a guardrail's block) dropped
	// it and recorded its error: the task fails with that error, as a task
	// through the chat executor does, not as a provider that sent nothing.
	if apiErr := models.WithheldAnswerError(rc); apiErr != nil {
		task.Status.State = TaskStatusFailed
		task.Status.Message = []MessagePart{{Type: "text", Text: fmt.Sprintf("Pipeline error: %s", apiErr.Error())}}
		s.tasks.StoreResult(task)
		slog.Warn("a2a: an output check withheld the answer", "task_id", task.ID, "error", apiErr)
		return
	}

	s.finishTaskFromResponse(task, rc.Response)

	s.tasks.StoreResult(task)
	slog.Info("a2a task completed (pipeline)", "task_id", task.ID, "model", model, "provider", rc.Provider)
}

func (s *Server) finishTaskFromResponse(task *Task, resp *models.ChatCompletionResponse) {
	if resp != nil && len(resp.Choices) > 0 {
		var responseText string
		if err := json.Unmarshal(resp.Choices[0].Message.Content, &responseText); err != nil {
			responseText = string(resp.Choices[0].Message.Content)
		}

		task.Status.State = TaskStatusCompleted
		task.Status.Message = []MessagePart{{Type: "text", Text: "Completed via pipeline"}}
		task.Artifacts = []Artifact{{
			Name:  "response",
			Index: 0,
			Parts: []MessagePart{{Type: "text", Text: responseText}},
		}}
		return
	}

	task.Status.State = TaskStatusFailed
	task.Status.Message = []MessagePart{{Type: "text", Text: "No response from provider"}}
}

func (s *Server) handleTasksGet(w http.ResponseWriter, msg *mcp.Message, caller Caller) {
	var params TaskGetParams
	if err := json.Unmarshal(msg.Params, &params); err != nil {
		writeA2AError(w, msg.ID, mcp.ErrCodeInvalidParams, "invalid tasks/get params")
		return
	}

	// Another org's task answers "task not found", the same as an unknown id.
	task, ok := s.tasks.GetForOrg(params.TaskID, caller.OrgID)
	if !ok && params.TaskIDSnake != "" {
		task, ok = s.tasks.GetForOrg(params.TaskIDSnake, caller.OrgID)
	}
	if !ok {
		taskID := params.TaskID
		if taskID == "" {
			taskID = params.TaskIDSnake
		}
		writeA2AError(w, msg.ID, mcp.ErrCodeInvalidParams, "task not found: "+taskID)
		return
	}

	writeA2AResult(w, msg.ID, task)
}

func (s *Server) handleTasksCancel(w http.ResponseWriter, msg *mcp.Message, caller Caller) {
	var params TaskCancelParams
	if err := json.Unmarshal(msg.Params, &params); err != nil {
		writeA2AError(w, msg.ID, mcp.ErrCodeInvalidParams, "invalid tasks/cancel params")
		return
	}

	// Another org's task answers "task not found", the same as an unknown id,
	// and is neither cancelled nor changed.
	task, ok := s.tasks.CancelForOrg(params.TaskID, caller.OrgID)
	if !ok && params.TaskIDSnake != "" {
		task, ok = s.tasks.CancelForOrg(params.TaskIDSnake, caller.OrgID)
	}
	if !ok {
		taskID := params.TaskID
		if taskID == "" {
			taskID = params.TaskIDSnake
		}
		writeA2AError(w, msg.ID, mcp.ErrCodeInvalidParams, "task not found: "+taskID)
		return
	}

	// The task is already marked canceled, so whatever the stopped execution
	// stores from here on is dropped by StoreResult.
	if cancelFn, ok := s.taskCancels.Load(task.ID); ok {
		cancelFn.(context.CancelFunc)()
	}
	writeA2AResult(w, msg.ID, task)
}

func (s *Server) resolveA2AModel(params MessageSendParams) string {
	return extractModelFromMetadata(params.Metadata)
}

func extractModelFromMetadata(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}

	var meta map[string]any
	if err := json.Unmarshal(raw, &meta); err != nil {
		return ""
	}

	model, _ := meta["model"].(string)
	return model
}

// Close stops the background cleanup goroutine.
func (s *Server) Close() {
	close(s.done)
}

// Registry returns the agent registry.
func (s *Server) Registry() *Registry {
	return s.registry
}

func writeA2AResult(w http.ResponseWriter, id json.RawMessage, result interface{}) {
	resp, err := mcp.NewResponse(id, result)
	if err != nil {
		writeA2AError(w, id, mcp.ErrCodeInternal, "failed to marshal result")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// writeA2AUnauthorized answers a request that carries no authenticated caller
// although auth is on. The gateway's KeyAuth refuses such a request before it
// gets here; this is the last line. The request has not been parsed, so the
// JSON-RPC error carries a null id.
func writeA2AUnauthorized(w http.ResponseWriter) {
	w.Header().Set("WWW-Authenticate", models.BearerChallenge)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusUnauthorized)
	json.NewEncoder(w).Encode(mcp.NewErrorResponse(json.RawMessage("null"), mcp.ErrCodeInvalidRequest, "Invalid or missing API key"))
}

func writeA2AError(w http.ResponseWriter, id json.RawMessage, code int, message string) {
	resp := mcp.NewErrorResponse(id, code, message)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func generateTaskID() string {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		panic("crypto/rand.Read failed: " + err.Error())
	}
	return "task-" + hex.EncodeToString(b)
}
