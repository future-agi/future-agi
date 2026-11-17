package server

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// preparedStream serves every streaming chat call from channels that are
// already in their final state: script's chunks queued, then err, both
// closed, as an adapter leaves them once it has read the whole upstream
// response and returned. What the gateway reads next is then decided by the
// gateway alone, never by when an adapter goroutine happens to run.
type preparedStream struct {
	providers.Provider
	calls  atomic.Int32
	script func(model string) []models.StreamChunk
	err    error
}

func (p *preparedStream) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	p.calls.Add(1)
	models.MarkStreamAccepted(ctx)
	script := p.script(req.Model)
	chunks := make(chan models.StreamChunk, len(script))
	for _, c := range script {
		chunks <- c
	}
	errs := make(chan error, 1)
	if p.err != nil {
		errs <- p.err
	}
	close(errs)
	close(chunks)
	return chunks, errs
}

// prepareManagedStream puts p in front of the managed provider's adapter.
func prepareManagedStream(t *testing.T, g *managedTestGateway, p *preparedStream) {
	t.Helper()
	real, ok := g.srv.handlers.registry.GetProvider("openrouter")
	if !ok {
		t.Fatal("no managed openrouter provider")
	}
	p.Provider = real
	g.srv.handlers.registry.RegisterProvider("openrouter", p, nil)
}

// terminalEvidenceError makes the error channel the only ready channel after
// the initial output. Converting that error for the response synchronously
// queues evidence before terminal handling, so this exercises the error arm's
// drain deterministically: no sleeps, scheduling assumptions or retry odds.
type terminalEvidenceError struct {
	once  sync.Once
	queue func()
}

func (e *terminalEvidenceError) Error() string {
	e.once.Do(e.queue)
	return "fixture failure"
}

type terminalEvidenceStream struct{ providers.Provider }

func (p *terminalEvidenceStream) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	models.MarkStreamAccepted(ctx)
	chunks := make(chan models.StreamChunk, 2)
	text := "hello there"
	chunks <- models.StreamChunk{Model: req.Model, Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
	errs := make(chan error, 1)
	errs <- &terminalEvidenceError{queue: func() {
		chunks <- models.StreamChunk{UpstreamProvider: "Cheap Compute"}
		chunks <- models.StreamChunk{Usage: models.MeasuredUsage(8, 3)}
		close(chunks)
	}}
	close(errs)
	return chunks, errs
}

func TestManagedChatTerminalEvidenceDeterministic(t *testing.T) {
	g := recheck3Gateway(t, recheck3CheapExpensive)
	real, ok := g.srv.handlers.registry.GetProvider("openrouter")
	if !ok {
		t.Fatal("no managed provider")
	}
	g.srv.handlers.registry.RegisterProvider("openrouter", &terminalEvidenceStream{Provider: real}, nil)
	status, body, _ := sendManaged(t, g, "/v1/chat/completions", "acme/token", true, false, nil)
	spent, held := settledAccount(t, g)
	if status != 200 || spent != 48 || held != 0 || !strings.Contains(body, "fixture failure") || strings.Contains(body, "[DONE]") {
		t.Fatalf("status=%d spent=%d held=%d body=%.400s", status, spent, held, body)
	}
}

// An adapter that reads an error envelope after the stream started queues
// the envelope's evidence (served endpoint and usage) as a chunk, then the
// terminal error, then closes its chunks. Once the error is read, every
// chunk already queued is handled before it, and a closed stream with a
// queued error ends with that error, not [DONE]: the request is priced at
// the named endpoint from the reported usage (48 micros), never at the
// reserve card from an estimate.
//
// Both channels are ready before the gateway reads either, so an
// implementation that lets the error and the chunks race leaves the choice
// to select's uniform pick: each chat request then loses the evidence with
// probability 1/2, and all 64 sequential requests keep it with probability
// 2^-64. Messages reads its chunks to the end before it settles, and is the
// control.
func TestManagedChatDrainsQueuedEvidence(t *testing.T) {
	for _, tc := range []struct {
		surface string
		runs    int
	}{{"/v1/chat/completions", 64}, {"/v1/messages", 8}} {
		t.Run(tc.surface, func(t *testing.T) {
			g := recheck3Gateway(t, recheck3CheapExpensive)
			p := &preparedStream{
				err: models.ErrUpstreamProvider(http.StatusBadGateway, "fixture failure"),
				script: func(model string) []models.StreamChunk {
					text := "hello there"
					var evidence models.StreamChunk
					if err := json.Unmarshal([]byte(`{"provider":"Cheap Compute","usage":{"prompt_tokens":8,"completion_tokens":3}}`), &evidence); err != nil {
						t.Error(err)
					}
					return []models.StreamChunk{
						{ID: "c1", Object: "chat.completion.chunk", Model: model, Choices: []models.StreamChoice{{Delta: models.Delta{Role: "assistant", Content: &text}}}},
						evidence,
					}
				},
			}
			prepareManagedStream(t, g, p)
			var before int64
			for run := 0; run < tc.runs; run++ {
				status, body, _ := sendManaged(t, g, tc.surface, "acme/token", true, false, nil)
				spent, held := settledAccount(t, g)
				charged := spent - before
				before = spent
				if status != 200 || charged != 48 || held != 0 {
					t.Fatalf("run %d: status=%d charged=%d want=48 held=%d body=%.400s", run, status, charged, held, body)
				}
				if tc.surface == "/v1/chat/completions" && (!strings.Contains(body, "hello there") || !strings.Contains(body, `"message":"fixture failure"`) || strings.Contains(body, "[DONE]")) {
					t.Fatalf("run %d: want the answer, then the upstream's error and no [DONE]: %.400s", run, body)
				}
			}
			if got := int(p.calls.Load()); got != tc.runs {
				t.Errorf("dispatches=%d want=%d", got, tc.runs)
			}
		})
	}
}
