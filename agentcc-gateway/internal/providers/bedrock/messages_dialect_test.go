package bedrock

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	anthropicfmt "github.com/futureagi/agentcc-gateway/internal/translation/anthropic"
)

// A Bedrock Claude model is not an AnthropicNativeProvider, so /v1/messages
// serves it through the canonical path: Bedrock usage -> canonical usage
// (prompt_tokens include cache reads and writes) -> the Anthropic
// dialect. The dialect must hand an Anthropic-SDK client Anthropic's own
// shape back: input_tokens without the cache part, the cache reads and
// writes apart.

const (
	dialectUncached   = 100
	dialectCacheRead  = 100000
	dialectCacheWrite = 300
	dialectOutput     = 5
)

const dialectUsageJSON = `{"input_tokens":100,"cache_read_input_tokens":100000,"cache_creation_input_tokens":300,"output_tokens":5}`

// checkDialectUsage checks an Anthropic-dialect usage object.
func checkDialectUsage(t *testing.T, where string, usage map[string]any) {
	t.Helper()
	want := map[string]float64{
		"input_tokens":                dialectUncached,
		"cache_read_input_tokens":     dialectCacheRead,
		"cache_creation_input_tokens": dialectCacheWrite,
		"output_tokens":               dialectOutput,
	}
	for k, v := range want {
		if got, _ := usage[k].(float64); got != v {
			t.Errorf("%s: usage.%s = %v, want %v (usage %v)", where, k, usage[k], v, usage)
		}
	}
}

func TestBedrockClaudeMessagesDialectUsageNonStream(t *testing.T) {
	var resp bedrockResponse
	body := `{"id":"msg_d","type":"message","model":"anthropic.claude","role":"assistant",
		"content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":` + dialectUsageJSON + `}`
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		t.Fatal(err)
	}
	canonical := translateResponse(&resp)
	tr := anthropicfmt.New()
	for name, render := range map[string]func() ([]byte, error){
		"ResponseFromCanonical":            func() ([]byte, error) { return tr.ResponseFromCanonical(canonical) },
		"ResponseFromCanonicalWithMapping": func() ([]byte, error) { return tr.ResponseFromCanonicalWithMapping(canonical, nil) },
	} {
		out, err := render()
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		var msg struct {
			Usage map[string]any `json:"usage"`
		}
		if err := json.Unmarshal(out, &msg); err != nil {
			t.Fatalf("%s: %v in %s", name, err, out)
		}
		checkDialectUsage(t, name, msg.Usage)
	}
}

func TestBedrockClaudeMessagesDialectUsageStream(t *testing.T) {
	event := func(payload string) *eventStreamMessage {
		return &eventStreamMessage{Headers: map[string]string{":message-type": "event"}, Payload: []byte(payload)}
	}
	state := newBedrockStreamState("anthropic.claude")
	payloads := []string{
		`{"type":"message_start","message":{"id":"msg_ds","model":"anthropic.claude","usage":` +
			`{"input_tokens":100,"cache_read_input_tokens":100000,"cache_creation_input_tokens":300,"output_tokens":1}}}`,
		`{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}`,
		`{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}`,
	}
	chunks := make(chan models.StreamChunk, len(payloads))
	for _, p := range payloads {
		chunk, _, err := state.parseStreamPayload(event(p))
		if err != nil {
			t.Fatal(err)
		}
		if chunk != nil {
			chunks <- *chunk
		}
	}
	close(chunks)

	events, errs := anthropicfmt.New().StreamEventsFromCanonical(context.Background(), chunks)
	var delta map[string]any
	timeout := time.After(3 * time.Second)
	for done := false; !done; {
		select {
		case frame, ok := <-events:
			if !ok {
				done = true
				break
			}
			s := string(frame)
			if !strings.HasPrefix(s, "event: message_delta\n") {
				continue
			}
			data := strings.TrimSpace(strings.TrimPrefix(s[strings.Index(s, "\ndata: ")+1:], "data: "))
			if err := json.Unmarshal([]byte(data), &delta); err != nil {
				t.Fatalf("message_delta data %q: %v", data, err)
			}
		case err := <-errs:
			if err != nil {
				t.Fatalf("stream error: %v", err)
			}
		case <-timeout:
			t.Fatal("timed out collecting stream events")
		}
	}
	if delta == nil {
		t.Fatal("no message_delta event")
	}
	usage, _ := delta["usage"].(map[string]any)
	checkDialectUsage(t, "message_delta", usage)
}
