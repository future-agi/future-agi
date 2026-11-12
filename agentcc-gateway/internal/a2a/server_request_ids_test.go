package a2a

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/oklog/ulid/v2"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// pipelineIDProbe is a pre-plugin that records the request and trace id of
// each run and answers it without the provider. It holds every run until n
// runs are inside the pipeline at once (or 5 s pass), so the runs it answers
// are proven to overlap.
type pipelineIDProbe struct {
	n      int
	mu     sync.Mutex
	inside int
	most   int
	all    chan struct{}
	opened bool
	runs   [][2]string // request id, trace id
}

func newPipelineIDProbe(n int) *pipelineIDProbe {
	return &pipelineIDProbe{n: n, all: make(chan struct{})}
}

func (p *pipelineIDProbe) Name() string  { return "pipeline-id-probe" }
func (p *pipelineIDProbe) Priority() int { return 1 }
func (p *pipelineIDProbe) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	p.runs = append(p.runs, [2]string{rc.RequestID, rc.TraceID})
	p.inside++
	p.most = max(p.most, p.inside)
	if p.inside >= p.n && !p.opened {
		p.opened = true
		close(p.all)
	}
	p.mu.Unlock()
	select {
	case <-p.all:
	case <-time.After(5 * time.Second):
	}
	p.mu.Lock()
	p.inside--
	p.mu.Unlock()
	return pipeline.ResultShortCircuit(completedResponse())
}
func (p *pipelineIDProbe) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// sendWithTrace posts one blocking message/send, with the caller's
// x-agentcc-trace-id when traceID is set, and returns the task.
func sendWithTrace(s *Server, traceID string) (Task, error) {
	params, err := json.Marshal(MessageSendParams{
		Message:  Message{Role: "user", Parts: []MessagePart{{Type: "text", Text: "hi"}}},
		Metadata: json.RawMessage(`{"model":"gpt-4o"}`),
	})
	if err != nil {
		return Task{}, err
	}
	body, err := json.Marshal(&mcp.Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodMessageSend, Params: params})
	if err != nil {
		return Task{}, err
	}
	r := httptest.NewRequest(http.MethodPost, "/a2a", bytes.NewReader(body))
	if traceID != "" {
		r.Header.Set("x-agentcc-trace-id", traceID)
	}
	w := httptest.NewRecorder()
	s.HandleMessage(w, r)
	var resp mcp.Message
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		return Task{}, fmt.Errorf("body %s: %w", w.Body.String(), err)
	}
	if resp.Error != nil {
		return Task{}, fmt.Errorf("JSON-RPC error %d: %s", resp.Error.Code, resp.Error.Message)
	}
	var task Task
	err = json.Unmarshal(resp.Result, &task)
	return task, err
}

// A server built with a pipeline and no chat executor runs a task through the
// pipeline itself, on a request context it builds. Each task's run must have
// a request id of its own, from the gateway's request-id source, and carry
// the trace id of the A2A request that started it: the caller's
// x-agentcc-trace-id, or else one of its own. The two runs overlap.
func TestPipelineTasksRunWithTheirOwnRequestIDs(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) }))
	defer upstream.Close()
	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	probe := newPipelineIDProbe(2)
	s := NewServer(CardConfig{Name: "ids"}, NewRegistry(nil), WithPipeline(pipeline.NewEngine(probe), registry))
	t.Cleanup(s.Close)

	const callerTrace = "trace-from-the-a2a-caller"
	tasks := make([]Task, 2)
	errs := make([]error, 2)
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i, trace := range []string{callerTrace, ""} {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			tasks[i], errs[i] = sendWithTrace(s, trace)
		}()
	}
	close(start)
	wg.Wait()
	for i, err := range errs {
		if err != nil {
			t.Fatalf("task %d: %v", i, err)
		}
		if tasks[i].Status.State != TaskStatusCompleted {
			t.Fatalf("task %d ended %q (%+v), want %q", i, tasks[i].Status.State, tasks[i].Status.Message, TaskStatusCompleted)
		}
	}

	probe.mu.Lock()
	runs, most := append([][2]string(nil), probe.runs...), probe.most
	probe.mu.Unlock()
	t.Logf("runs (request id, trace id): %q", runs)
	if most != 2 {
		t.Fatalf("at most %d run(s) were inside the pipeline at once: the two tasks did not run at the same time", most)
	}
	if len(runs) != 2 {
		t.Fatalf("the pipeline ran %d times for 2 tasks", len(runs))
	}
	if _, err := ulid.ParseStrict(runs[0][0]); err != nil || runs[0][0] == runs[1][0] {
		t.Errorf("the two runs have request ids %q and %q; want two ULIDs of their own", runs[0][0], runs[1][0])
	} else if _, err := ulid.ParseStrict(runs[1][0]); err != nil {
		t.Errorf("the two runs have request ids %q and %q; want two ULIDs of their own", runs[0][0], runs[1][0])
	}
	callerRuns, ownTrace := 0, ""
	for _, r := range runs {
		if r[1] == callerTrace {
			callerRuns++
		} else {
			ownTrace = r[1]
		}
	}
	if callerRuns != 1 {
		t.Errorf("%d run(s) carry the caller's trace id %q; want the one its request sent it on", callerRuns, callerTrace)
	}
	if _, err := ulid.ParseStrict(ownTrace); err != nil {
		t.Errorf("the run whose request sent no trace id carries trace id %q; want one of its own: %v", ownTrace, err)
	}
}
