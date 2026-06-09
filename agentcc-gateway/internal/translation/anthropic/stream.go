// Package anthropic — OpenAI stream chunks → Anthropic SSE events.
//
// The Anthropic stream event sequence is:
//
//	message_start
//	  → content_block_start (index 0)
//	  → content_block_delta* (index 0)
//	  → content_block_stop  (index 0)
//	  → content_block_start (index 1)  ← new block (tool_use, thinking, …)
//	  → …
//	  → message_delta
//	  → message_stop
//
// OpenAI streams emit undifferentiated chat.completion.chunk events. We must
// synthesize boundary events. The core invariant is: never buffer — emit each
// event as soon as we have enough state to produce it so TTFT is preserved.
package anthropic

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ─── State machine ────────────────────────────────────────────────────────────

// streamState tracks the evolving state of one Anthropic stream.
type streamState struct {
	messageStarted    bool
	messageID         string
	currentBlockIdx   int
	currentBlockType  string // "text" | "tool_use" | "thinking" | ""
	currentToolCallID string
	pendingStopReason string
	pendingUsage      *models.Usage
	// toolNameMapping maps truncated tool name → original name, populated
	// when the caller used WithMapping. Applied on tool_use content_block_start
	// emission so the SDK sees the original name the client sent.
	toolNameMapping map[string]string
}

// StreamEventsFromCanonical converts a channel of OpenAI StreamChunks into a
// channel of Anthropic SSE event byte slices.
//
// Each emitted element on the returned channel is a complete SSE frame:
//
//	event: content_block_delta\ndata: {...}\n\n
//
// The error channel carries at most one non-nil error (upstream stream failure)
// followed by a close. The events channel is always closed before or at the
// same time as the error channel.
func (t *Translator) StreamEventsFromCanonical(
	ctx context.Context,
	chunks <-chan models.StreamChunk,
) (<-chan []byte, <-chan error) {
	return streamEvents(ctx, chunks, nil)
}

// StreamEventsFromCanonicalWithMapping is the streaming counterpart of
// ResponseFromCanonicalWithMapping — pass the tool_name_mapping captured at
// request time so the stream state machine can restore truncated tool names
// on content_block_start events. Handlers that have
// canonicalReq.Extra["tool_name_mapping"] should call this variant; others
// can use StreamEventsFromCanonical.
func (t *Translator) StreamEventsFromCanonicalWithMapping(
	ctx context.Context,
	chunks <-chan models.StreamChunk,
	toolNameMapping map[string]string,
) (<-chan []byte, <-chan error) {
	return streamEvents(ctx, chunks, toolNameMapping)
}

func streamEvents(
	ctx context.Context,
	chunks <-chan models.StreamChunk,
	toolNameMapping map[string]string,
) (<-chan []byte, <-chan error) {
	events := make(chan []byte, 64)
	errs := make(chan error, 1)

	go func() {
		// Close order matters: events must close BEFORE errs so the handler's
		// select drains all buffered events before seeing the err-channel
		// terminator. Without this, final message_delta/message_stop events
		// can sit in the buffered events channel while the handler returns
		// on !ok from errs. LIFO: registered first runs last → errs last.
		defer close(errs)
		defer close(events)

		state := &streamState{
			currentBlockIdx: -1, // no block started yet
			toolNameMapping: toolNameMapping,
		}

		for {
			select {
			case <-ctx.Done():
				return

			case chunk, ok := <-chunks:
				if !ok {
					// Stream closed — flush any pending stop reason.
					if state.pendingStopReason != "" {
						flushPendingStop(ctx, state, events)
					} else if state.currentBlockType != "" {
						// Graceful close without a finish_reason: emit a block stop.
						emit(ctx, events, sseFrame("content_block_stop", ContentBlockStopEvent{
							Type:  "content_block_stop",
							Index: state.currentBlockIdx,
						}))
					}
					// Emit message_stop if we opened a message.
					if state.messageStarted {
						emit(ctx, events, sseFrame("message_stop", MessageStopEvent{Type: "message_stop"}))
					}
					return
				}

				if err := handleChunk(ctx, state, chunk, events); err != nil {
					errs <- err
					// Emit a format-native error event before closing.
					emitStreamError(ctx, events, err)
					return
				}
			}
		}
	}()

	return events, errs
}

// handleChunk processes one OpenAI StreamChunk and emits the corresponding
// Anthropic SSE events onto the events channel.
func handleChunk(ctx context.Context, state *streamState, chunk models.StreamChunk, events chan<- []byte) error {
	// First chunk: emit message_start.
	if !state.messageStarted {
		state.messageStarted = true
		state.messageID = chunk.ID
		if state.messageID == "" {
			state.messageID = generateMsgID()
		}

		var startUsage ResponseUsage
		if chunk.Usage != nil {
			startUsage = ResponseUsage{
				InputTokens:  chunk.Usage.PromptTokens,
				OutputTokens: chunk.Usage.CompletionTokens,
			}
		}
		emit(ctx, events, sseFrame("message_start", MessageStartEvent{
			Type: "message_start",
			Message: MessageStartMsg{
				ID:    state.messageID,
				Type:  "message",
				Role:  "assistant",
				Model: chunk.Model,
				Usage: startUsage,
			},
		}))
	}

	// If we previously stashed a pending stop reason AND this chunk carries
	// usage, now is the time to flush.
	if state.pendingStopReason != "" && chunk.Usage != nil {
		state.pendingUsage = chunk.Usage
		flushPendingStop(ctx, state, events)
		return nil
	}

	if len(chunk.Choices) == 0 {
		return nil
	}
	choice := chunk.Choices[0]
	delta := choice.Delta

	// ── Text delta ─────────────────────────────────────────────────────────────
	if delta.Content != nil && *delta.Content != "" {
		if state.currentBlockType != "text" {
			// Close any open block.
			if state.currentBlockType != "" {
				emit(ctx, events, sseFrame("content_block_stop", ContentBlockStopEvent{
					Type:  "content_block_stop",
					Index: state.currentBlockIdx,
				}))
			}
			// Open a new text block.
			state.currentBlockIdx++
			state.currentBlockType = "text"
			emit(ctx, events, sseFrame("content_block_start", ContentBlockStartEvent{
				Type:  "content_block_start",
				Index: state.currentBlockIdx,
				ContentBlock: ContentBlock{
					Type: "text",
					Text: "",
				},
			}))
		}
		emit(ctx, events, sseFrame("content_block_delta", ContentBlockDeltaEvent{
			Type:  "content_block_delta",
			Index: state.currentBlockIdx,
			Delta: ContentDelta{
				Type: "text_delta",
				Text: *delta.Content,
			},
		}))
	}

	// ── Tool call deltas ───────────────────────────────────────────────────────
	for _, tc := range delta.ToolCalls {
		if tc.Function == nil {
			continue
		}

		if tc.Function.Name != "" {
			// New tool call starting: name (and id) are set on the same chunk
			// as the first arguments fragment (LiteLLM streaming_iterator.py
			// gotcha: name+id+first-args in the same chunk).
			// Emit: close prior block → open tool_use block → emit first delta.
			if state.currentBlockType != "" {
				emit(ctx, events, sseFrame("content_block_stop", ContentBlockStopEvent{
					Type:  "content_block_stop",
					Index: state.currentBlockIdx,
				}))
			}
			state.currentBlockIdx++
			state.currentBlockType = "tool_use"
			state.currentToolCallID = tc.ID

			// Restore the original tool name if RequestToCanonical truncated
			// it to fit OpenAI's 64-char limit (see tool_name_mapping in
			// to_openai.go). The non-streaming path uses
			// ResponseFromCanonicalWithMapping; streaming does the same via
			// state.toolNameMapping populated by
			// StreamEventsFromCanonicalWithMapping.
			emittedName := tc.Function.Name
			if state.toolNameMapping != nil {
				if original, ok := state.toolNameMapping[emittedName]; ok {
					emittedName = original
				}
			}

			emit(ctx, events, sseFrame("content_block_start", ContentBlockStartEvent{
				Type:  "content_block_start",
				Index: state.currentBlockIdx,
				ContentBlock: ContentBlock{
					Type:  "tool_use",
					ID:    tc.ID,
					Name:  emittedName,
					Input: json.RawMessage("{}"),
				},
			}))

			// If the same chunk also carries arguments, emit a delta immediately.
			if tc.Function.Arguments != "" {
				emit(ctx, events, sseFrame("content_block_delta", ContentBlockDeltaEvent{
					Type:  "content_block_delta",
					Index: state.currentBlockIdx,
					Delta: ContentDelta{
						Type:        "input_json_delta",
						PartialJSON: tc.Function.Arguments,
					},
				}))
			}
		} else if tc.Function.Arguments != "" {
			// Continuation of an existing tool_use block.
			emit(ctx, events, sseFrame("content_block_delta", ContentBlockDeltaEvent{
				Type:  "content_block_delta",
				Index: state.currentBlockIdx,
				Delta: ContentDelta{
					Type:        "input_json_delta",
					PartialJSON: tc.Function.Arguments,
				},
			}))
		}
	}

	// ── Finish reason ──────────────────────────────────────────────────────────
	// Don't emit stop events yet — OpenAI may send usage in a trailing chunk.
	if choice.FinishReason != nil && *choice.FinishReason != "" {
		state.pendingStopReason = reverseMapFinishReasonForStream(*choice.FinishReason)
		if chunk.Usage != nil {
			state.pendingUsage = chunk.Usage
			flushPendingStop(ctx, state, events)
		}
		// else: wait for a trailing usage chunk or stream close.
	}

	return nil
}

// flushPendingStop emits content_block_stop (if in a block), message_delta,
// and message_stop, then resets the pending state.
func flushPendingStop(ctx context.Context, state *streamState, events chan<- []byte) {
	if state.currentBlockType != "" {
		emit(ctx, events, sseFrame("content_block_stop", ContentBlockStopEvent{
			Type:  "content_block_stop",
			Index: state.currentBlockIdx,
		}))
		state.currentBlockType = ""
	}

	var outputTokens int
	if state.pendingUsage != nil {
		outputTokens = state.pendingUsage.CompletionTokens
	}

	emit(ctx, events, sseFrame("message_delta", MessageDeltaEvent{
		Type: "message_delta",
		Delta: MessageDelta{
			StopReason: state.pendingStopReason,
		},
		Usage: ResponseUsage{OutputTokens: outputTokens},
	}))

	emit(ctx, events, sseFrame("message_stop", MessageStopEvent{Type: "message_stop"}))

	state.pendingStopReason = ""
	state.pendingUsage = nil
	state.messageStarted = false // so we don't double-emit on stream close
}

// emitStreamError sends a format-native error event onto the events channel.
func emitStreamError(ctx context.Context, events chan<- []byte, err error) {
	evt := StreamErrorEvent{
		Type: "error",
		Error: StreamError{
			Type:    "api_error",
			Message: err.Error(),
		},
	}
	emit(ctx, events, sseFrame("error", evt))
}

// ─── SSE formatting ───────────────────────────────────────────────────────────

// sseFrame encodes an Anthropic SSE event into the wire bytes:
//
//	event: <name>\ndata: <json>\n\n
func sseFrame(eventName string, payload interface{}) []byte {
	data, err := json.Marshal(payload)
	if err != nil {
		data = []byte(fmt.Sprintf(`{"type":"error","error":{"type":"api_error","message":%q}}`, err.Error()))
	}
	frame := make([]byte, 0, len(eventName)+len(data)+16)
	frame = append(frame, "event: "...)
	frame = append(frame, eventName...)
	frame = append(frame, '\n')
	frame = append(frame, "data: "...)
	frame = append(frame, data...)
	frame = append(frame, '\n', '\n')
	return frame
}

// emit blocks until the event is sent or the context is cancelled.
// Non-blocking was wrong — dropping a content_block_start would desynchronize
// the SDK on the receiving end (subsequent content_block_delta events would
// reference an index that was never announced). Blocking + ctx-aware lets
// backpressure propagate up to the provider goroutine, which is what we want
// when the HTTP writer (network / slow client) falls behind.
func emit(ctx context.Context, ch chan<- []byte, frame []byte) {
	select {
	case ch <- frame:
	case <-ctx.Done():
	}
}
