package gemini

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/translation"
)

// StreamEventsFromCanonical converts a channel of OpenAI StreamChunks into
// Gemini SSE event bytes.
//
// Gemini's SSE format is simpler than Anthropic's: each event is a single
// GenerateContentResponse JSON object on a "data: {...}\n\n" line — no
// event-type framing, no per-content-block start/stop events.
//
// The main complexity is tool-call argument accumulation: OpenAI streams
// arguments in fragments; we must buffer them and only emit the functionCall
// part on the terminal chunk (when finish_reason arrives).
func (t *Translator) StreamEventsFromCanonical(
	ctx context.Context,
	chunks <-chan models.StreamChunk,
) (<-chan []byte, <-chan error) {
	outCh := make(chan []byte, 32)
	errCh := make(chan error, 1)

	go func() {
		// Close order matters: outCh (events) must close BEFORE errCh so the
		// handler's select drains all buffered events before seeing the
		// err-channel terminator. LIFO: registered first runs last → errCh last.
		defer close(errCh)
		defer close(outCh)

		state := &streamAccumulator{
			toolArgs: make(map[int]*toolCallAccum),
		}

		for {
			select {
			case <-ctx.Done():
				return
			case chunk, ok := <-chunks:
				if !ok {
					// Stream ended — flush any buffered state.
					if events := state.flush(); len(events) > 0 {
						for _, ev := range events {
							select {
							case outCh <- ev:
							case <-ctx.Done():
								return
							}
						}
					}
					return
				}

				events, err := state.handleChunk(chunk)
				if err != nil {
					// Emit a Gemini-format error event then stop.
					errEvent := buildErrorEvent(err)
					select {
					case outCh <- errEvent:
					case <-ctx.Done():
					}
					return
				}

				for _, ev := range events {
					select {
					case outCh <- ev:
					case <-ctx.Done():
						return
					}
				}
			}
		}
	}()

	return outCh, errCh
}

// ── accumulator ──────────────────────────────────────────────────────────────

type toolCallAccum struct {
	name string
	args strings.Builder
}

type streamAccumulator struct {
	toolArgs map[int]*toolCallAccum
}

// handleChunk processes one OpenAI StreamChunk and returns 0..N SSE events.
func (a *streamAccumulator) handleChunk(chunk models.StreamChunk) ([][]byte, error) {
	var events [][]byte

	if len(chunk.Choices) == 0 && chunk.Usage == nil {
		return nil, nil
	}

	// Build one Gemini GenerateContentResponse per chunk.
	gr := geminiResponse{}

	if len(chunk.Choices) > 0 {
		sc := chunk.Choices[0]
		delta := sc.Delta

		var parts []geminiPart
		isTerminal := sc.FinishReason != nil

		// ── Text content ──────────────────────────────────────────────────────
		if delta.Content != nil && *delta.Content != "" {
			parts = append(parts, geminiPart{Text: *delta.Content})
		}

		// ── Tool-call argument accumulation ───────────────────────────────────
		// Accumulate fragments; only emit functionCall parts on the terminal chunk.
		for _, tcd := range delta.ToolCalls {
			acc, exists := a.toolArgs[tcd.Index]
			if !exists {
				acc = &toolCallAccum{}
				a.toolArgs[tcd.Index] = acc
			}
			if tcd.Function != nil {
				if tcd.Function.Name != "" {
					acc.name = tcd.Function.Name
				}
				if tcd.Function.Arguments != "" {
					acc.args.WriteString(tcd.Function.Arguments)
				}
			}
		}

		// On the terminal chunk: emit all accumulated functionCall parts then clear.
		if isTerminal && len(a.toolArgs) > 0 {
			// Emit in insertion order (index 0, 1, 2, …).
			for i := 0; i < len(a.toolArgs); i++ {
				acc, ok := a.toolArgs[i]
				if !ok {
					continue
				}
				argsStr := acc.args.String()
				if argsStr == "" {
					argsStr = "{}"
				}
				var argsJSON json.RawMessage
				var parsed interface{}
				if err := json.Unmarshal([]byte(argsStr), &parsed); err == nil {
					argsJSON, _ = json.Marshal(parsed)
				} else {
					argsJSON = json.RawMessage(`{}`)
				}
				parts = append(parts, geminiPart{
					FunctionCall: &geminiFunctionCall{
						Name: acc.name,
						Args: argsJSON,
					},
				})
			}
			// Clear accumulated state so flush() doesn't re-emit.
			a.toolArgs = make(map[int]*toolCallAccum)
		}

		candidate := geminiCandidate{
			Content: geminiContent{
				Role:  "model",
				Parts: parts,
			},
		}
		if isTerminal {
			candidate.FinishReason = mapFinishReasonToGemini(*sc.FinishReason)
		}
		gr.Candidates = []geminiCandidate{candidate}
	}

	if chunk.Usage != nil {
		gr.UsageMetadata = &geminiUsageMetadata{
			PromptTokenCount:     chunk.Usage.PromptTokens,
			CandidatesTokenCount: chunk.Usage.CompletionTokens,
			TotalTokenCount:      chunk.Usage.TotalTokens,
		}
	}

	ev, err := sseEvent(gr)
	if err != nil {
		return nil, err
	}
	events = append(events, ev)
	return events, nil
}

// flush emits any trailing state when the input channel closes without a
// finish_reason chunk (defensive; Gemini has no DONE sentinel).
func (a *streamAccumulator) flush() [][]byte {
	if len(a.toolArgs) == 0 {
		return nil
	}
	// If there are pending tool calls that never got a finish_reason chunk,
	// emit them now as a final chunk with STOP.
	gr := geminiResponse{}
	var parts []geminiPart
	for i := 0; i < len(a.toolArgs); i++ {
		acc, ok := a.toolArgs[i]
		if !ok {
			continue
		}
		argsStr := acc.args.String()
		if argsStr == "" {
			argsStr = "{}"
		}
		var argsJSON json.RawMessage
		var parsed interface{}
		if err := json.Unmarshal([]byte(argsStr), &parsed); err == nil {
			argsJSON, _ = json.Marshal(parsed)
		} else {
			argsJSON = json.RawMessage(`{}`)
		}
		parts = append(parts, geminiPart{
			FunctionCall: &geminiFunctionCall{
				Name: acc.name,
				Args: argsJSON,
			},
		})
	}
	if len(parts) == 0 {
		return nil
	}
	candidate := geminiCandidate{
		Content:      geminiContent{Role: "model", Parts: parts},
		FinishReason: "STOP",
	}
	gr.Candidates = []geminiCandidate{candidate}
	ev, err := sseEvent(gr)
	if err != nil {
		return nil
	}
	return [][]byte{ev}
}

// ── helpers ──────────────────────────────────────────────────────────────────

// sseEvent marshals a GenerateContentResponse and wraps it in a Gemini SSE line.
// Format: "data: <json>\n\n"
func sseEvent(gr geminiResponse) ([]byte, error) {
	b, err := json.Marshal(gr)
	if err != nil {
		return nil, err
	}
	return []byte(fmt.Sprintf("data: %s\n\n", string(b))), nil
}

// buildErrorEvent converts an error into a Gemini SSE error payload.
// Format: data: {"error":{"code":...,"message":"...","status":"INTERNAL"}}\n\n
func buildErrorEvent(err error) []byte {
	code := http.StatusInternalServerError
	msg := err.Error()
	status := "INTERNAL"

	// If it's a translation.ErrNotImplemented, use 501.
	if err == translation.ErrNotImplemented {
		code = http.StatusNotImplemented
		status = "UNIMPLEMENTED"
	}

	resp := geminiErrorResponse{
		Error: geminiErrorDetail{
			Code:    code,
			Message: msg,
			Status:  status,
		},
	}
	b, _ := json.Marshal(resp)
	return []byte(fmt.Sprintf("data: %s\n\n", string(b)))
}

