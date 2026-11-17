package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/translation"
)

type admissionStreamProvider struct {
	providers.Provider
	busy bool
}

func (p admissionStreamProvider) StreamChatCompletion(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks, errs := make(chan models.StreamChunk, 1), make(chan error, 1)
	if p.busy {
		errs <- models.ErrProviderBusy("fixture")
	} else {
		text, finish := "first-and-only", "stop"
		chunks <- models.StreamChunk{ID: "first", Model: "resolved", Choices: []models.StreamChoice{{Index: 0,
			Delta: models.Delta{Content: &text}, FinishReason: &finish}},
			Usage: models.MeasuredUsage(3, 5)}
	}
	close(errs)
	close(chunks)
	return chunks, errs
}

type admissionPostObserver struct {
	calls int
	usage *models.Usage
}

func (*admissionPostObserver) Name() string  { return "admission-observer" }
func (*admissionPostObserver) Priority() int { return 999 }
func (*admissionPostObserver) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *admissionPostObserver) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.calls++
	if rc.Response != nil && rc.Response.Usage != nil {
		usage := *rc.Response.Usage
		p.usage = &usage
	}
	return pipeline.ResultContinue()
}

// Startup consumes the first chunk only to decide admission. It must still
// reach the client and the single post-plugin pass, including its usage.
func TestCanonicalStreamAdmission(t *testing.T) {
	for _, surface := range []string{"completions", "genai"} {
		for _, busy := range []bool{false, true} {
			name := surface + "/success"
			if busy {
				name = surface + "/busy"
			}
			t.Run(name, func(t *testing.T) {
				ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
				defer cancel()
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.IsStream, rc.Model, rc.Provider = true, "m", "fixture"
				rc.Request = &models.ChatCompletionRequest{Model: "m", Stream: true}
				post := &admissionPostObserver{}
				h := &Handlers{engine: pipeline.NewEngine(post)}
				w := httptest.NewRecorder()
				p := admissionStreamProvider{busy: busy}
				if surface == "completions" {
					h.handleCompletionStream(ctx, w, rc, p)
				} else {
					translator, ok := translation.InboundFor("google")
					if !ok {
						t.Fatal("missing GenAI translator")
					}
					h.handleGenAIStreamViaCanonical(ctx, w, rc, p, translator, rc.Request)
				}
				if post.calls != 1 {
					t.Errorf("post-plugin calls = %d, want exactly one", post.calls)
				}
				if busy {
					if w.Code != http.StatusServiceUnavailable || w.Header().Get("Retry-After") != "1" || !strings.Contains(w.Body.String(), "provider_busy") || w.Flushed {
						t.Errorf("startup refusal: status=%d retry=%q flushed=%t body=%s", w.Code, w.Header().Get("Retry-After"), w.Flushed, w.Body.String())
					}
					if post.usage != nil {
						t.Errorf("busy refusal invented usage: %+v", post.usage)
					}
					return
				}
				if w.Code != http.StatusOK || strings.Count(w.Body.String(), "first-and-only") != 1 {
					t.Errorf("first chunk lost or duplicated: status=%d body=%s", w.Code, w.Body.String())
				}
				if post.usage == nil || post.usage.PromptTokens != 3 || post.usage.CompletionTokens != 5 || post.usage.TotalTokens != 8 {
					t.Errorf("first chunk usage lost: %+v", post.usage)
				}
			})
		}
	}
}

func TestWaitForFirstStreamChunkKeepsClosedChannelError(t *testing.T) {
	// The adapter can enqueue its refusal and close both channels before the
	// handler runs. The closed chunk channel must not mask provider_busy.
	for i := 0; i < 100; i++ {
		chunks, errs := admissionStreamProvider{busy: true}.StreamChatCompletion(context.Background(), nil)
		first, err := waitForFirstStreamChunk(context.Background(), chunks, errs)
		if first != nil || err == nil || !strings.Contains(err.Error(), "provider_busy") {
			t.Fatalf("first=%+v err=%v; want the queued provider_busy", first, err)
		}
	}
}
