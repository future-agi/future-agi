package a2a

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// raceIterations is how many times each race test repeats its interleaving.
// The race detector only reports accesses it actually observes, so one run
// is not enough to prove the absence of a race.
const raceIterations = 200

// newExecutorTestServer builds a server whose return-immediately sends run
// through exec. The engine and provider registry only switch the pipeline
// path on; the injected executor means neither is called.
func newExecutorTestServer(t *testing.T, exec ChatCompletionExecutor) *Server {
	t.Helper()
	registry, err := providers.NewRegistry(&config.Config{})
	if err != nil {
		t.Fatalf("providers.NewRegistry: %v", err)
	}
	s := NewServer(CardConfig{Name: "race-test"}, NewRegistry(nil),
		WithPipeline(pipeline.NewEngine(), registry),
		WithChatCompletionExecutor(exec))
	t.Cleanup(s.Close)
	return s
}

func completedResponse() *models.ChatCompletionResponse {
	return &models.ChatCompletionResponse{Choices: []models.Choice{{
		Message: models.Message{Role: "assistant", Content: json.RawMessage(`"done"`)},
	}}}
}

// callA2A posts one JSON-RPC call and decodes the task in its result. It
// returns an error instead of failing the test, so goroutines can use it.
func callA2A(s *Server, method string, params any) (Task, error) {
	paramsData, err := json.Marshal(params)
	if err != nil {
		return Task{}, err
	}
	body, err := json.Marshal(&mcp.Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  method,
		Params:  paramsData,
	})
	if err != nil {
		return Task{}, err
	}
	w := httptest.NewRecorder()
	s.HandleMessage(w, httptest.NewRequest("POST", "/a2a", bytes.NewReader(body)))

	var resp mcp.Message
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		return Task{}, fmt.Errorf("decode %s response: %w (body %s)", method, err, w.Body.String())
	}
	if resp.Error != nil {
		return Task{}, fmt.Errorf("%s: JSON-RPC error %d: %s", method, resp.Error.Code, resp.Error.Message)
	}
	var task Task
	if err := json.Unmarshal(resp.Result, &task); err != nil {
		return Task{}, fmt.Errorf("decode %s task: %w", method, err)
	}
	return task, nil
}

func sendReturnImmediately(t *testing.T, s *Server) Task {
	t.Helper()
	task, err := callA2A(s, MethodMessageSend, MessageSendParams{
		Message:       Message{Role: "user", Parts: []MessagePart{{Type: "text", Text: "race me"}}},
		Metadata:      json.RawMessage(`{"model":"gpt-4o"}`),
		Configuration: &MessageSendConfiguration{ReturnImmediately: true},
	})
	if err != nil {
		t.Fatalf("send: %v", err)
	}
	if task.Status.State != TaskStatusWorking {
		t.Fatalf("send returned state %q, want %q", task.Status.State, TaskStatusWorking)
	}
	return task
}

// waitExecutionDone waits until the background execution of taskID has
// returned. Its deferred cancel-map delete runs after its final task write.
func waitExecutionDone(t *testing.T, s *Server, taskID string) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, running := s.taskCancels.Load(taskID); !running {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("execution of %s did not finish", taskID)
		}
		time.Sleep(time.Millisecond)
	}
}

func assertCanceled(t *testing.T, label string, task Task) {
	t.Helper()
	if task.Status.State != TaskStatusCanceled {
		t.Fatalf("%s: state = %q, want %q", label, task.Status.State, TaskStatusCanceled)
	}
	if len(task.Status.Message) != 1 || task.Status.Message[0].Text != "Task canceled" {
		t.Fatalf("%s: status message = %+v, want one part %q", label, task.Status.Message, "Task canceled")
	}
}

// TestTasksCancelRacesExecutionFinish cancels a task at the same moment its
// execution finishes, with a tasks/get reading it in the same window. Both
// orders happen across the iterations: the execution returns its result, or
// it sees the canceled context first. The task must end canceled either way.
func TestTasksCancelRacesExecutionFinish(t *testing.T) {
	for i := 0; i < raceIterations; i++ {
		release := make(chan struct{})
		started := make(chan struct{})
		s := newExecutorTestServer(t, func(ctx context.Context, _ string, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
			close(started)
			select {
			case <-release:
				return completedResponse(), nil
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		})

		task := sendReturnImmediately(t, s)
		<-started

		var (
			wg       sync.WaitGroup
			canceled Task
			errs     = make(chan error, 2)
		)
		gate := make(chan struct{})
		wg.Add(3)
		go func() { defer wg.Done(); <-gate; close(release) }()
		go func() {
			defer wg.Done()
			<-gate
			var err error
			if canceled, err = callA2A(s, MethodTasksCancel, TaskCancelParams{TaskID: task.ID}); err != nil {
				errs <- err
			}
		}()
		go func() {
			defer wg.Done()
			<-gate
			if _, err := callA2A(s, MethodTasksGet, TaskGetParams{TaskID: task.ID}); err != nil {
				errs <- err
			}
		}()
		close(gate)
		wg.Wait()
		close(errs)
		for err := range errs {
			t.Fatalf("iteration %d: %v", i, err)
		}

		waitExecutionDone(t, s, task.ID)
		assertCanceled(t, fmt.Sprintf("iteration %d cancel response", i), canceled)
		final, err := callA2A(s, MethodTasksGet, TaskGetParams{TaskID: task.ID})
		if err != nil {
			t.Fatalf("iteration %d: %v", i, err)
		}
		assertCanceled(t, fmt.Sprintf("iteration %d final task", i), final)
	}
}

// TestTasksCancelIsFinalWhenExecutionCompletesLate covers an executor that
// does not watch its context: it returns a result after the cancel has been
// answered. That late result must not overwrite the canceled task.
func TestTasksCancelIsFinalWhenExecutionCompletesLate(t *testing.T) {
	release := make(chan struct{})
	s := newExecutorTestServer(t, func(context.Context, string, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
		<-release
		return completedResponse(), nil
	})

	task := sendReturnImmediately(t, s)
	canceled, err := callA2A(s, MethodTasksCancel, TaskCancelParams{TaskIDSnake: task.ID})
	if err != nil {
		t.Fatal(err)
	}
	assertCanceled(t, "cancel response", canceled)

	close(release)
	waitExecutionDone(t, s, task.ID)
	final, err := callA2A(s, MethodTasksGet, TaskGetParams{TaskID: task.ID})
	if err != nil {
		t.Fatal(err)
	}
	assertCanceled(t, "final task", final)
}

// TestReturnImmediatelyAnswersWhileExecutionRuns sends with an executor that
// returns at once, so the execution writes its result while the handler is
// still encoding the response. The response is the task as it was accepted.
func TestReturnImmediatelyAnswersWhileExecutionRuns(t *testing.T) {
	s := newExecutorTestServer(t, func(context.Context, string, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
		return completedResponse(), nil
	})

	for i := 0; i < raceIterations; i++ {
		task := sendReturnImmediately(t, s)
		waitExecutionDone(t, s, task.ID)
		final, err := callA2A(s, MethodTasksGet, TaskGetParams{TaskID: task.ID})
		if err != nil {
			t.Fatalf("iteration %d: %v", i, err)
		}
		if final.Status.State != TaskStatusCompleted || len(final.Artifacts) != 1 || final.Artifacts[0].Parts[0].Text != "done" {
			t.Fatalf("iteration %d: final task = %+v, want completed with artifact %q", i, final, "done")
		}
	}
}

// TestReturnImmediatelyReleasesExecutionContext checks that a background
// execution releases its context when it returns, as the blocking path does,
// instead of holding the context and its timer until the 120 s timeout.
func TestReturnImmediatelyReleasesExecutionContext(t *testing.T) {
	ctxs := make(chan context.Context, 1)
	s := newExecutorTestServer(t, func(ctx context.Context, _ string, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
		ctxs <- ctx
		return completedResponse(), nil
	})

	sendReturnImmediately(t, s)
	ctx := <-ctxs
	select {
	case <-ctx.Done():
	case <-time.After(5 * time.Second):
		t.Fatal("execution context is still live after the execution returned")
	}
	if !errors.Is(ctx.Err(), context.Canceled) {
		t.Fatalf("execution context error = %v, want %v", ctx.Err(), context.Canceled)
	}
}
