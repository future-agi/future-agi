package logging

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

func TestOptBC4RealStreamTerminal(t *testing.T) {
	const key = "optb-real-stream-outbound-fixture"
	sent := make(chan bool, 1)
	rawErrors := make(chan error, 1)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sent <- r.Header.Get("Authorization") == "Bearer "+key
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: {\"id\":\"fixture\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"prefix\"}}]}\n\n")
		w.(http.Flusher).Flush()
		message := "opaque-upstream-value " + strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		rawErrors <- &models.APIError{Status: 429, ProviderStatus: 429, Code: "rate_limit_error", Type: models.ErrTypeRateLimit, Message: message}
		raw, _ := json.Marshal(map[string]any{"error": map[string]any{"code": 429, "message": message}})
		_, _ = fmt.Fprintf(w, "data: %s\n\n", raw)
	}))
	defer upstream.Close()
	p, err := openai.New("fixture", config.ProviderConfig{BaseURL: upstream.URL, APIKey: key})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	chunks, errs := p.StreamChatCompletion(ctx, &models.ChatCompletionRequest{Model: "fixture", Stream: true})
	count := 0
	for range chunks {
		count++
	}
	var terminal error
	for err := range errs {
		if err != nil {
			terminal = err
		}
	}
	if terminal == nil {
		t.Fatal("real SSE adapter did not return its terminal error")
	}
	// Bypass source scrubbing only for the sink witness, not the real stream.
	terminal = optbRawSinkError(t, ctx, rawErrors)
	if count == 0 || terminal == nil || !strings.Contains(terminal.Error(), key) {
		t.Fatal("real SSE canary failure witness absent")
	}
	if !<-sent {
		t.Fatal("canary not sent as platform key")
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req_optb_real_stream"
	rc.Model = "fixture"
	rc.IsStream = true
	rc.AddError(terminal)
	rec := buildRecord(rc, config.RequestLoggingConfig{Enabled: true})
	entry := optbExport(t, rec, rc)
	optbAssertErrorFields(t, entry, key)
	if !strings.Contains(entry["error_message"].(string), "rate_limit_error") {
		t.Error("terminal 429 lost its closed-table category")
	}
}

func TestOptBBYOKUncappedStream(t *testing.T) {
	// Above the 512-character attempt diagnostic limit: ordinary BYOK output
	// must remain uncapped, with identical UTF-8 bytes at the deployed adapter.
	want := strings.Repeat("customer-owned-output-λ", 2048)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer tenant-stream-fixture" {
			t.Error("wrong tenant auth")
		}
		w.Header().Set("Content-Type", "text/event-stream")
		raw, _ := json.Marshal(map[string]any{"id": "fixture", "choices": []any{map[string]any{"index": 0, "delta": map[string]any{"content": want}}}})
		_, _ = fmt.Fprintf(w, "data: %s\n\ndata: [DONE]\n\n", raw)
	}))
	defer upstream.Close()
	p, err := openai.New("fixture", config.ProviderConfig{BaseURL: upstream.URL, APIKey: "tenant-stream-fixture"})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	chunks, errs := p.StreamChatCompletion(ctx, &models.ChatCompletionRequest{Model: "fixture", Stream: true})
	got := ""
	for chunk := range chunks {
		for _, choice := range chunk.Choices {
			if choice.Delta.Content != nil {
				got += *choice.Delta.Content
			}
		}
	}
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	if got != want {
		t.Error("uncapped BYOK success output is not byte-equal")
	}
}
