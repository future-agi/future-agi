package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func prefixKinds(prefixes []models.StreamChunk) []string {
	kinds := make([]string, 0, len(prefixes))
	for _, p := range prefixes {
		switch {
		case len(p.Choices) == 0 && p.Usage != nil:
			kinds = append(kinds, "usage")
		case len(p.Choices) == 0:
			kinds = append(kinds, "provider")
		case len(p.Choices[0].Delta.ToolCalls) > 0:
			kinds = append(kinds, "tool:"+p.Choices[0].Delta.ToolCalls[0].ID)
		case p.Choices[0].Delta.Role != "":
			kinds = append(kinds, "role")
		default:
			kinds = append(kinds, "empty")
		}
	}
	return kinds
}

// What a managed stream sends before its first output is replayed, in the
// order it came, once the stream starts: the role chunk and other stream
// metadata as well as tool prefixes. None of it is billable output, and a
// chunk with an error finish is not replayed.
func TestManagedStreamStartReplaysMetadata(t *testing.T) {
	empty, text := "", "hello"
	failed := "error"
	prompt := 8
	usage := models.NewUsage(&prompt, nil)
	toolChunk := func(id string) models.StreamChunk {
		return models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{ID: id, Type: "function"}}}}}}
	}
	metadata := func(n int) []models.StreamChunk {
		out := make([]models.StreamChunk, n)
		for i := range out {
			out[i] = models.StreamChunk{ID: fmt.Sprint(i), Choices: []models.StreamChoice{{Delta: models.Delta{Content: &empty}}}}
		}
		return out
	}
	huge := models.StreamChunk{SystemFingerprint: strings.Repeat("x", maxStreamPrefixBytes), Choices: []models.StreamChoice{{Delta: models.Delta{Content: &empty}}}}
	for _, tc := range []struct {
		name   string
		before []models.StreamChunk
		want   []string
	}{
		{"mixed-order", []models.StreamChunk{
			{Choices: []models.StreamChoice{{Delta: models.Delta{Role: "assistant", Content: &empty}}}},
			{Usage: usage},
			toolChunk("call_a"),
			{UpstreamProvider: "Cheap Compute"},
			{Choices: []models.StreamChoice{{Delta: models.Delta{}, FinishReason: &failed}}},
			{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &empty}}}},
			toolChunk("call_b"),
		}, []string{"role", "usage", "tool:call_a", "provider", "empty", "tool:call_b"}},
		// OpenRouter reasoning chunks decode to empty deltas: a long hidden
		// reasoning phase must neither fail the stream nor crowd out a tool
		// prefix that follows it. Metadata past its own bound is dropped.
		{"metadata-past-its-bound", append(metadata(3*maxStreamPrefixChunks), toolChunk("call_after_reasoning")),
			append(strings.Split(strings.TrimSuffix(strings.Repeat("empty,", maxStreamPrefixChunks), ","), ","), "tool:call_after_reasoning")},
		{"oversized-metadata", []models.StreamChunk{huge, toolChunk("call_c")}, []string{"tool:call_c"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			chunks := make(chan models.StreamChunk, len(tc.before)+1)
			errs := make(chan error, 1)
			for _, c := range tc.before {
				chunks <- c
			}
			chunks <- models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
			close(errs)
			close(chunks)
			chunk, prefixes, err := waitForManagedStreamStart(context.Background(), chunks, errs, true)
			if err != nil {
				t.Fatalf("stream did not start: %v", err)
			}
			if chunk.Choices[0].Delta.Content == nil || *chunk.Choices[0].Delta.Content != text {
				t.Fatalf("first output changed: %+v", chunk)
			}
			got := prefixKinds(prefixes)
			if strings.Join(got, ",") != strings.Join(tc.want, ",") {
				t.Fatalf("replayed %d prefixes %.200v, want %d %.200v", len(got), got, len(tc.want), tc.want)
			}
			for i := 1; tc.name == "metadata-past-its-bound" && i < maxStreamPrefixChunks; i++ {
				if prefixes[i].ID != fmt.Sprint(i) {
					t.Fatalf("metadata order changed at %d: %q", i, prefixes[i].ID)
				}
			}
		})
	}
}

// On chat the role chunk an upstream sends first reaches the client first,
// before the tool prefix that follows it, as an unmanaged stream sends it.
func TestManagedChatReplaysRoleBeforeToolPrefix(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	installBillabilityModels(billabilityModels(t))(t, g)
	g.ledger.SetMicros("org-m", 1_000_000)
	up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
		w.Header().Set("Content-Type", "text/event-stream")
		writeManagedChunk(w, model, `"system_fingerprint":"fp_role","choices":[{"index":0,"delta":{"role":"assistant","content":""}}]`)
		writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_role_first","type":"function"}]}}]`)
		writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"lookup","arguments":"{}"}}]}}]`)
		writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":8,"completion_tokens":3}`)
		fmt.Fprint(w, "data: [DONE]\n\n")
	})
	g.managed.streamWith.Store(&up)
	status, body, complete := sendManagedWithin(t, g, "/v1/chat/completions", "acme/token", 10*time.Second)
	spent, held := settledAccount(t, g)
	if !complete || status != 200 || spent != 48 || held != 0 {
		t.Fatalf("complete=%t status=%d spent=%d held=%d body=%.400s", complete, status, spent, held, body)
	}
	var order []string
	for _, line := range strings.Split(body, "\n") {
		raw := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
		if !strings.HasPrefix(line, "data:") || raw == "[DONE]" {
			continue
		}
		var c models.StreamChunk
		if json.Unmarshal([]byte(raw), &c) != nil || len(c.Choices) == 0 {
			continue
		}
		d := c.Choices[0].Delta
		switch {
		case d.Role == "assistant" && c.SystemFingerprint == "fp_role":
			order = append(order, "role")
		case len(d.ToolCalls) > 0 && d.ToolCalls[0].ID == "call_role_first":
			order = append(order, "tool-id")
		case len(d.ToolCalls) > 0 && d.ToolCalls[0].Function != nil && d.ToolCalls[0].Function.Name == "lookup":
			order = append(order, "tool-name")
		}
	}
	if strings.Join(order, ",") != "role,tool-id,tool-name" {
		t.Errorf("client saw %v, want the role chunk, then the tool id, then its name: %.600s", order, body)
	}
}

// Hundreds of metadata chunks before an id-only tool prefix (an upstream's
// hidden reasoning, then a tool call) still start a normal stream on both
// APIs, with the tool's identity intact and nothing but the reported usage
// billed.
func TestManagedStreamLongMetadataThenToolPrefix(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface, func(t *testing.T) {
			g := newManagedTestGateway(t, managedOptIn())
			installBillabilityModels(billabilityModels(t))(t, g)
			g.ledger.SetMicros("org-m", 1_000_000)
			up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
				w.Header().Set("Content-Type", "text/event-stream")
				for i := 0; i < 3*maxStreamPrefixChunks; i++ {
					writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","content":"","reasoning":"step"}}]`)
				}
				writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_after_reasoning","type":"function"}]}}]`)
				writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"lookup","arguments":"{}"}}]}}]`)
				writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":8,"completion_tokens":3}`)
				fmt.Fprint(w, "data: [DONE]\n\n")
			})
			g.managed.streamWith.Store(&up)
			status, body, complete := sendManagedWithin(t, g, surface, "acme/token", 10*time.Second)
			spent, held := settledAccount(t, g)
			if !complete || status != 200 || spent != 48 || held != 0 || !strings.Contains(body, "call_after_reasoning") || !strings.Contains(body, "lookup") {
				t.Errorf("complete=%t status=%d spent=%d held=%d body=%.400s", complete, status, spent, held, body)
			}
		})
	}
}
