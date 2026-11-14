package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/streaming"
	"github.com/futureagi/agentcc-gateway/internal/tokenizer"
)

// streamEndError is the error to report in a stream that stopped before its
// last event, for the reason err gives.
//
// By then the stream has answered 200 and the status cannot change, so an
// error event is the only signal the client gets. Without one, a stream cut at
// its deadline ends like a finished one, only shorter.
//
// It is nil when the client left: nobody is reading.
func streamEndError(ctx context.Context, err error) *models.APIError {
	switch {
	case errors.Is(ctx.Err(), context.Canceled):
		return nil
	case ctx.Err() != nil:
		return models.ErrGatewayTimeout("request timed out before the stream finished")
	}
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		return apiErr
	}
	return common.ReadError(err, 0, "stream read")
}

// closedStreamError says whether a stream whose chunk channel has closed
// really finished. When it did not, apiErr is what to report — nil when the
// client is the one that left.
//
// A closed channel alone cannot tell. A provider that failed sends its error
// and closes errs before it closes chunks, so the error is already waiting;
// but once ctx is done a provider sends nothing, so the context is the only
// evidence of a deadline. A select that reads all three would otherwise end a
// failed or timed-out stream as a finished one whenever the closed chunk
// channel won.
func closedStreamError(ctx context.Context, errCh <-chan error) (apiErr *models.APIError, finished bool) {
	if apiErr := pendingStreamError(errCh); apiErr != nil {
		return apiErr, false
	}
	if ctx.Err() != nil {
		return streamEndError(ctx, ctx.Err()), false
	}
	return nil, true
}

// pendingStreamError is the error waiting on a provider's error channel, or
// nil when there is none. It never blocks.
func pendingStreamError(errCh <-chan error) *models.APIError {
	select {
	case err, ok := <-errCh:
		if !ok || err == nil {
			return nil
		}
		var apiErr *models.APIError
		if errors.As(err, &apiErr) {
			return apiErr
		}
		return models.ErrUpstreamProvider(0, err.Error())
	default:
		return nil
	}
}

// upstreamStreamError is a failure the upstream reported inside its own
// stream: an Anthropic or Responses error event, or response.failed. The
// client gets the event as the upstream sent it; this is what records the
// request as failed rather than as a success.
func upstreamStreamError(data []byte) *models.APIError {
	var payload struct {
		Message string `json:"message"`
		Error   struct {
			Message string `json:"message"`
		} `json:"error"`
		Response struct {
			Error struct {
				Message string `json:"message"`
			} `json:"error"`
		} `json:"response"`
	}
	_ = json.Unmarshal(data, &payload)
	for _, message := range []string{payload.Error.Message, payload.Message, payload.Response.Error.Message} {
		if message != "" {
			return models.ErrUpstreamProvider(0, message)
		}
	}
	return models.ErrUpstreamProvider(0, "upstream reported an error in the stream")
}

// wantsStreamMetadata reports whether the caller sent x-agentcc-include-metadata.
func wantsStreamMetadata(rc *models.RequestContext) bool {
	include := rc.RequestHeaders.Get("x-agentcc-include-metadata")
	return include == "1" || strings.EqualFold(include, "true")
}

// streamMetadata is what x-agentcc-include-metadata adds to the last event of
// a stream: the cost and latency a response header cannot carry, because the
// headers went out before either was known. Nil when the caller did not ask.
func streamMetadata(rc *models.RequestContext) *models.AgentccStreamMetadata {
	if !wantsStreamMetadata(rc) {
		return nil
	}
	var cost float64
	if rawCost, ok := rc.Metadata["cost"]; ok {
		if parsed, err := strconv.ParseFloat(rawCost, 64); err == nil {
			cost = parsed
		}
	}
	return &models.AgentccStreamMetadata{
		Cost:      cost,
		LatencyMs: rc.Elapsed().Milliseconds(),
	}
}

// withStreamMetadata returns a stream's terminal event with agentcc_metadata
// added to its JSON payload, and the event as it arrived when meta is nil or
// the payload is not a JSON object.
//
// The metadata rides on an event the SDKs already parse: message_stop on
// /v1/messages, response.completed on /v1/responses. Both SDKs keep fields
// they do not know, whereas the Anthropic SDK drops an event name it does not
// know, so a separate event would never reach SDK code.
func withStreamMetadata(ev streaming.Event, meta *models.AgentccStreamMetadata) []byte {
	if meta == nil {
		return ev.Raw
	}
	var data bytes.Buffer
	if json.Compact(&data, ev.Data()) != nil || data.Len() < 2 || data.Bytes()[0] != '{' {
		return ev.Raw
	}
	metaJSON, err := json.Marshal(meta)
	if err != nil {
		return ev.Raw
	}

	// Splice the field in before the closing brace rather than re-encode, so
	// the payload — a whole response, on response.completed — keeps its bytes.
	payload := data.Bytes()[:data.Len()-1]
	sep := ","
	if len(payload) == 1 {
		sep = ""
	}
	out := fmt.Appendf(nil, "data: %s%s\"agentcc_metadata\":%s}\n\n", payload, sep, metaJSON)
	if ev.Name != "" {
		out = append(fmt.Appendf(nil, "event: %s\n", ev.Name), out...)
	}
	return out
}

// endRelay sends a relayed stream's terminal event and ends the relay.
//
// The event waits for the post-plugins only when the caller asked for
// metadata, because only then does it carry the cost. Otherwise it goes out
// first, as the relay always sent it: post-stage plugins can make network
// calls, and SDK helpers wait on this event to return the final response.
//
// The upstream is read to its end either way. Closed before EOF, the
// transport drops the connection instead of reusing it, and the next request
// pays for a new one. An upstream that never closes holds the relay until the
// deadline, as the byte relay before it did.
func endRelay(ctx context.Context, w io.Writer, flusher http.Flusher, rc *models.RequestContext, ev streaming.Event, upstream io.Reader, finalize func(detach bool)) {
	if wantsStreamMetadata(rc) {
		finalize(false)
		if _, err := w.Write(withStreamMetadata(ev, streamMetadata(rc))); err == nil {
			flusher.Flush()
		}
		drain(upstream)
		return
	}

	_, writeErr := w.Write(ev.Raw)
	if writeErr == nil {
		flusher.Flush()
	}
	drain(upstream)
	finalize(writeErr != nil || ctx.Err() != nil)
}

// drain reads upstream to its end; nil, for a stream that is not a byte relay, is a no-op.
func drain(upstream io.Reader) {
	if upstream != nil {
		_, _ = io.Copy(io.Discard, upstream)
	}
}

// lastEventHolder holds back a stream's latest data event, so the cost can be
// added to it when the stream ends. A GenAI stream has no terminal event of
// its own: its last chunk is known only once the stream has ended. Events
// without data, such as comments, queue behind the held one in order.
type lastEventHolder struct {
	held  *streaming.Event
	after []byte
}

// push takes the next event and returns the bytes now safe to send.
func (h *lastEventHolder) push(ev streaming.Event) []byte {
	if len(bytes.TrimSpace(ev.Data())) == 0 {
		if h.held == nil {
			return ev.Raw
		}
		h.after = append(h.after, ev.Raw...)
		return nil
	}
	var out []byte
	if h.held != nil {
		out = append(append(out, h.held.Raw...), h.after...)
	}
	h.held, h.after = &ev, nil
	return out
}

// flush returns the held event, with meta added when it is not nil, and what
// queued behind it.
func (h *lastEventHolder) flush(meta *models.AgentccStreamMetadata) []byte {
	if h.held == nil {
		return h.after
	}
	return append(withStreamMetadata(*h.held, meta), h.after...)
}

// streamedChars counts the output a stream relayed, in characters: text,
// reasoning and tool-call arguments. An upstream reports usage only in its
// last event, so a stream cut before it, at its deadline, by the upstream or
// by a client that left, has none. The provider bills that output anyway, so
// the count is what such a stream is priced from.
type streamedChars int

// addChunk counts the deltas of a chat completion chunk.
func (n *streamedChars) addChunk(chunk models.StreamChunk) {
	for _, choice := range chunk.Choices {
		if choice.Delta.Content != nil {
			*n += streamedChars(len(*choice.Delta.Content))
		}
		if choice.Delta.ReasoningContent != nil {
			*n += streamedChars(len(*choice.Delta.ReasoningContent))
		}
		for _, tc := range choice.Delta.ToolCalls {
			if tc.Function != nil {
				*n += streamedChars(len(tc.Function.Arguments))
			}
		}
	}
}

// addEvent counts the delta of a relayed native event: a string on Responses
// delta events, an object on Anthropic's content_block_delta.
func (n *streamedChars) addEvent(data []byte) {
	var ev struct {
		Delta json.RawMessage `json:"delta"`
	}
	if json.Unmarshal(data, &ev) != nil || len(ev.Delta) == 0 {
		return
	}
	var text string
	if json.Unmarshal(ev.Delta, &text) == nil {
		*n += streamedChars(len(text))
		return
	}
	var block struct {
		Text        string `json:"text"`
		PartialJSON string `json:"partial_json"`
		Thinking    string `json:"thinking"`
	}
	if json.Unmarshal(ev.Delta, &block) == nil {
		*n += streamedChars(len(block.Text) + len(block.PartialJSON) + len(block.Thinking))
	}
}

// estimatedTokens converts characters to tokens at about 4 characters a
// token, the rule the gateway's token counter uses.
func estimatedTokens(chars int) int {
	return (chars + 3) / 4
}

// estimatedStreamUsage is the usage of a stream that relayed output but got
// none from its upstream: the prompt, counted or estimated, and the output
// relayed. The request log row is marked usage_estimated.
func estimatedStreamUsage(rc *models.RequestContext, promptTokens int, out streamedChars) *models.Usage {
	completion := estimatedTokens(int(out))
	rc.Metadata[usageEstimatedKey] = "true"
	return &models.Usage{
		PromptTokens:     promptTokens,
		CompletionTokens: completion,
		TotalTokens:      promptTokens + completion,
	}
}

// usageEstimatedKey marks a request whose token counts the gateway estimated.
const usageEstimatedKey = "usage_estimated"

// chatPromptTokens estimates a chat request's prompt.
func chatPromptTokens(req *models.ChatCompletionRequest) int {
	if req == nil {
		return 0
	}
	return tokenizer.EstimateTokens(req.Messages)
}

// responsesPromptTokens estimates a Responses request's prompt: the text of its
// input and instructions, and a fixed amount per image, audio or file part.
func responsesPromptTokens(body []byte) int {
	var req struct {
		Input        json.RawMessage `json:"input"`
		Instructions string          `json:"instructions"`
	}
	if json.Unmarshal(body, &req) != nil {
		return 0
	}
	var input any
	_ = json.Unmarshal(req.Input, &input)
	chars, media := responsesInputText(input)
	return estimatedTokens(chars+len(req.Instructions)) + media*tokenizer.MediaPartTokens
}

// responsesInputText sums the text of a decoded Responses input and counts its
// image, audio and file parts. Only the fields that hold text are read: an
// image URL, file data or encrypted reasoning is not text.
func responsesInputText(v any) (chars, media int) {
	switch t := v.(type) {
	case string:
		return len(t), 0
	case []any:
		for _, item := range t {
			c, m := responsesInputText(item)
			chars, media = chars+c, media+m
		}
	case map[string]any:
		switch t["type"] {
		case "input_image", "input_audio", "input_file":
			return 0, 1
		}
		for _, key := range []string{"text", "content", "output", "arguments"} {
			if value, ok := t[key]; ok {
				c, m := responsesInputText(value)
				chars, media = chars+c, media+m
			}
		}
	}
	return chars, media
}
