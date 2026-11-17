package openai

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Keep the limit independent of the implementation so removing or accidentally
// raising the bound fails the test. Scanner's capacity includes the newline.
func TestStreamChatCompletionEventSizeBound(t *testing.T) {
	const limit = 10 * 1024 * 1024
	const prefix = `data: {"choices":[{"delta":{"content":"`
	const suffix = `"}}]}`
	for _, tc := range []struct {
		name       string
		lineBytes  int
		terminated bool
		wantError  bool
	}{
		{"below-limit", limit - 1, true, false},
		{"at-limit", limit, true, true},
		{"over-limit", limit + 1, true, true},
		{"unterminated", 2 * limit, false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			padding := tc.lineBytes - len(prefix) - len(suffix)
			end := suffix
			if tc.terminated {
				end += "\n\ndata: [DONE]\n\n"
			}
			body := &countedEventBody{Reader: io.MultiReader(strings.NewReader(prefix), io.LimitReader(eventPadding{}, int64(padding)), strings.NewReader(end))}
			transport := &eventSizeTransport{body: body}
			p := newTestProvider(t, "http://127.0.0.1")
			// Replace the exact transport used by the production adapter. It has no
			// network fallback, so even a bad endpoint cannot leave this process.
			p.httpClient.Transport = transport
			chunks, errs := p.StreamChatCompletion(context.Background(), &models.ChatCompletionRequest{Model: "fixture"})
			count := 0
			for chunk := range chunks {
				count++
				if len(chunk.Choices) != 1 || chunk.Choices[0].Delta.Content == nil || len(*chunk.Choices[0].Delta.Content) != padding {
					t.Error("event content was truncated")
				}
			}
			var failures []error
			for err := range errs {
				failures = append(failures, err)
			}
			if tc.wantError {
				if count != 0 || len(failures) != 1 || !strings.Contains(failures[0].Error(), "token too long") {
					t.Fatalf("oversize event: chunks=%d errors=%v", count, failures)
				}
				if body.read.Load() > limit {
					t.Errorf("oversize response consumed %d bytes; want at most %d", body.read.Load(), limit)
				}
			} else if count != 1 || len(failures) != 0 {
				t.Fatalf("bounded event: chunks=%d errors=%v", count, failures)
			}
			if transport.calls.Load() != 1 || !body.closed.Load() {
				t.Errorf("transport calls=%d body closed=%t", transport.calls.Load(), body.closed.Load())
			}
		})
	}
}

type eventSizeTransport struct {
	body  io.ReadCloser
	calls atomic.Int64
}

func (r *eventSizeTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	r.calls.Add(1)
	if req.URL.Host != "127.0.0.1" || req.URL.Path != "/v1/chat/completions" {
		return nil, fmt.Errorf("unexpected fixture endpoint")
	}
	return &http.Response{StatusCode: http.StatusOK, Header: make(http.Header), Body: r.body, Request: req}, nil
}

type countedEventBody struct {
	io.Reader
	read   atomic.Int64
	closed atomic.Bool
}

func (b *countedEventBody) Read(p []byte) (int, error) {
	n, err := b.Reader.Read(p)
	b.read.Add(int64(n))
	return n, err
}

func (b *countedEventBody) Close() error {
	b.closed.Store(true)
	return nil
}

type eventPadding struct{}

func (eventPadding) Read(p []byte) (int, error) {
	for i := range p {
		p[i] = 'a'
	}
	return len(p), nil
}
