package server

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func assertSplitUsageWire(t *testing.T, surface, body string) {
	t.Helper()
	if surface == "/v1/chat/completions" {
		chunks := parseWireStream(t, body)
		assertEndsWithDone(t, chunks, body)
		finish, _ := usageOnTheFinishChunk(t, chunks, body)
		if !usageIs(finish.Usage, 8, 3, 11) {
			t.Errorf("chat terminal usage = %+v, want 8/3/11", finish.Usage)
		}
		return
	}
	starts, deltas, stops := 0, 0, 0
	for _, line := range strings.Split(body, "\n") {
		data, ok := strings.CutPrefix(line, "data: ")
		if !ok {
			continue
		}
		var event struct {
			Type  string `json:"type"`
			Usage struct {
				Input  int `json:"input_tokens"`
				Output int `json:"output_tokens"`
			} `json:"usage"`
		}
		if err := json.Unmarshal([]byte(data), &event); err != nil {
			t.Fatal(err)
		}
		switch event.Type {
		case "message_start":
			starts++
		case "message_stop":
			stops++
		case "message_delta":
			deltas++
			if event.Usage.Input != 8 || event.Usage.Output != 3 {
				t.Errorf("Messages terminal usage = %+v, want 8/3", event.Usage)
			}
		}
	}
	if starts != 1 || deltas != 1 || stops != 1 {
		t.Errorf("Messages starts=%d deltas=%d stops=%d, want one of each", starts, deltas, stops)
	}
}

type openAuxiliaryErrorStream struct{ preparedStream }

func (p *openAuxiliaryErrorStream) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks, _ := p.preparedStream.StreamChatCompletion(ctx, req)
	return chunks, make(chan error) // Deliberately never closes: not canonical EOF.
}

// Accounting follows canonical chunk EOF, not an auxiliary error channel.
// This is also a full-then-revised report: merely having both counters does
// not make an earlier usage final.
func TestManagedMessagesClosedChunksDoNotWaitForAuxiliaryError(t *testing.T) {
	g := recheck3Gateway(t, "")
	p := &openAuxiliaryErrorStream{preparedStream: preparedStream{script: func(model string) []models.StreamChunk {
		stop := "stop"
		chunks := []models.StreamChunk{
			{ID: "split_aux", Model: model, Choices: []models.StreamChoice{{FinishReason: &stop}}, Usage: models.MeasuredUsage(8, 1)},
		}
		for i := 0; i < 300; i++ {
			chunks = append(chunks, models.StreamChunk{ID: "split_aux", Model: model})
		}
		return append(chunks, models.StreamChunk{ID: "split_aux", Model: model, Usage: models.MeasuredUsage(8, 3)})
	}}}
	prepareManagedStream(t, g, &p.preparedStream)
	g.srv.handlers.registry.RegisterProvider("openrouter", p, nil)
	status, body, complete := sendManagedWithin(t, g, "/v1/messages", "acme/token", 5*time.Second)
	spent, held := settledAccount(t, g)
	if !complete || status != 200 || p.calls.Load() != 1 || spent != 48 || held != 0 {
		t.Fatalf("status=%d complete=%t calls=%d spent=%d held=%d", status, complete, p.calls.Load(), spent, held)
	}
	assertSplitUsageWire(t, "/v1/messages", body)
}
