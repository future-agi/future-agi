package contracttest

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

// CanonicalFinishReasons are the OpenAI finish_reason values the gateway's
// canonical response may carry. internal/models leaves finish_reason a free
// string; this set is the one the inbound translators read back
// (translation/anthropic reverseMapFinishReason, translation/gemini
// mapFinishReasonToGemini) and the provider adapters emit. OpenAI's legacy
// "function_call" is deliberately absent: no adapter produces it.
var CanonicalFinishReasons = []string{"stop", "length", "tool_calls", "content_filter"}

// AssertCanonicalFinishReason fails unless reason is one of
// CanonicalFinishReasons.
func AssertCanonicalFinishReason(t testing.TB, reason string) {
	t.Helper()
	for _, r := range CanonicalFinishReasons {
		if reason == r {
			return
		}
	}
	t.Errorf("finish_reason %q is not canonical (want one of %q)", reason, CanonicalFinishReasons)
}

// Completion is the provider-agnostic view of one completion, assembled the
// same way from a whole response or from a stream, so both can be checked by
// the same rules.
type Completion struct {
	Role             string            `json:"role,omitempty"`
	Content          string            `json:"content"`
	ReasoningContent string            `json:"reasoning_content,omitempty"`
	ToolCalls        []models.ToolCall `json:"tool_calls,omitempty"`
	FinishReason     string            `json:"finish_reason"`
	Usage            *models.Usage     `json:"usage"`
}

// FromResponse assembles choice 0 of a non-streaming response.
func FromResponse(resp *models.ChatCompletionResponse) Completion {
	var c Completion
	if resp == nil || len(resp.Choices) == 0 {
		return c
	}
	ch := resp.Choices[0]
	c.Role = ch.Message.Role
	c.Content = contentText(ch.Message.Content)
	c.ReasoningContent = ch.Message.ReasoningContent
	c.ToolCalls = ch.Message.ToolCalls
	c.FinishReason = ch.FinishReason
	c.Usage = resp.Usage
	return c
}

// contentText flattens message content: a string, or the text parts of an
// array of content parts.
func contentText(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var s string
	if json.Unmarshal(raw, &s) == nil {
		return s
	}
	var parts []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if json.Unmarshal(raw, &parts) == nil {
		var b strings.Builder
		for _, p := range parts {
			if p.Type == "text" {
				b.WriteString(p.Text)
			}
		}
		return b.String()
	}
	return ""
}

// FromStream assembles choice 0 of a stream the way an OpenAI client would:
// content and reasoning concatenated, tool calls merged by index with their
// argument fragments appended, and the last finish_reason and usage seen.
func FromStream(chunks []models.StreamChunk) Completion {
	var c Completion
	var content, reasoning strings.Builder
	calls := map[int]*models.ToolCall{}
	for _, ch := range chunks {
		if ch.Usage != nil {
			c.Usage = ch.Usage
		}
		for _, sc := range ch.Choices {
			if sc.Index != 0 {
				continue
			}
			d := sc.Delta
			if c.Role == "" {
				c.Role = d.Role
			}
			if d.Content != nil {
				content.WriteString(*d.Content)
			}
			if d.ReasoningContent != nil {
				reasoning.WriteString(*d.ReasoningContent)
			}
			for _, tc := range d.ToolCalls {
				call := calls[tc.Index]
				if call == nil {
					call = &models.ToolCall{}
					calls[tc.Index] = call
				}
				if tc.ID != "" {
					call.ID = tc.ID
				}
				if tc.Type != "" {
					call.Type = tc.Type
				}
				if tc.Function != nil {
					if tc.Function.Name != "" {
						call.Function.Name = tc.Function.Name
					}
					call.Function.Arguments += tc.Function.Arguments
				}
			}
			if sc.FinishReason != nil {
				c.FinishReason = *sc.FinishReason
			}
		}
	}
	c.Content = content.String()
	c.ReasoningContent = reasoning.String()
	idx := make([]int, 0, len(calls))
	for i := range calls {
		idx = append(idx, i)
	}
	sort.Ints(idx)
	for _, i := range idx {
		c.ToolCalls = append(c.ToolCalls, *calls[i])
	}
	return c
}

// StreamResult is everything a StreamChatCompletion call produced. It
// marshals as {"chunks", "error", "assembled"}, the golden form for a stream:
// the chunks are the contract, the assembled completion is what a client
// would end up with.
type StreamResult struct {
	Chunks []models.StreamChunk
	Err    error
}

// MarshalJSON renders the golden form.
func (r StreamResult) MarshalJSON() ([]byte, error) {
	var errText *string
	if r.Err != nil {
		s := r.Err.Error()
		errText = &s
	}
	chunks := r.Chunks
	if chunks == nil {
		chunks = []models.StreamChunk{}
	}
	return json.Marshal(struct {
		Chunks    []models.StreamChunk `json:"chunks"`
		Error     *string              `json:"error"`
		Assembled Completion           `json:"assembled"`
	}{chunks, errText, FromStream(r.Chunks)})
}

// streamTimeout bounds DrainStream so a parser that never closes its
// channels fails the test instead of hanging it.
const streamTimeout = 10 * time.Second

// DrainStream reads chunks until the adapter closes it, then the first error,
// if any.
func DrainStream(t testing.TB, chunks <-chan models.StreamChunk, errs <-chan error) StreamResult {
	t.Helper()
	var res StreamResult
	timeout := time.After(streamTimeout)
	for {
		select {
		case ch, ok := <-chunks:
			if !ok {
				select {
				case err := <-errs:
					res.Err = err
				case <-timeout:
					t.Fatalf("contracttest: error channel not closed within %s", streamTimeout)
				}
				return res
			}
			res.Chunks = append(res.Chunks, ch)
		case <-timeout:
			t.Fatalf("contracttest: stream not closed within %s", streamTimeout)
		}
	}
}

// AssertSane checks the invariants any translated completion must hold:
//
//   - finish_reason is canonical, and "tool_calls" only with tool calls;
//   - every tool call has an id, a name, and arguments that are valid JSON;
//   - usage is present, prompt_tokens is positive, and the total adds up;
//   - when expectOutput, there is text or at least one tool call. The caller
//     decides expectOutput from the raw upstream body: if the provider sent
//     text or a tool call and the canonical form has neither, content was
//     dropped.
func AssertSane(t testing.TB, c Completion, expectOutput bool) {
	t.Helper()
	AssertCanonicalFinishReason(t, c.FinishReason)
	if c.FinishReason == "tool_calls" && len(c.ToolCalls) == 0 {
		t.Errorf("finish_reason is tool_calls but there are no tool calls")
	}
	for i, tc := range c.ToolCalls {
		if tc.ID == "" || tc.Function.Name == "" {
			t.Errorf("tool call %d lacks an id or name: %+v", i, tc)
		}
		if !json.Valid([]byte(tc.Function.Arguments)) {
			t.Errorf("tool call %d arguments are not valid JSON: %q", i, tc.Function.Arguments)
		}
	}
	if expectOutput && c.Content == "" && len(c.ToolCalls) == 0 {
		t.Errorf("upstream produced output but the canonical completion has no content and no tool calls")
	}
	switch u := c.Usage; {
	case u == nil:
		t.Errorf("usage is missing")
	case u.PromptTokens <= 0:
		t.Errorf("usage.prompt_tokens = %d, want > 0", u.PromptTokens)
	case u.TotalTokens != u.PromptTokens+u.CompletionTokens:
		t.Errorf("usage.total_tokens = %d, want prompt %d + completion %d",
			u.TotalTokens, u.PromptTokens, u.CompletionTokens)
	}
}

// AssertNoUnknownShapes fails for every report the adapter made (via
// common.ShapeReporter, observed through common.WithShapeRecorder) that c's
// meta.json does not list under allowed_unknown_shapes. Call it once per case
// so Discover can check that allowlist for stale entries.
func AssertNoUnknownShapes(t testing.TB, c Case, shapes []common.UnknownShape) {
	t.Helper()
	allowed := make(map[string]bool, len(c.Meta.AllowedUnknownShapes))
	for _, s := range c.Meta.AllowedUnknownShapes {
		allowed[s.key()] = true
	}
	var used, unexpected []string
	for _, s := range shapes {
		key := AllowedShape{Kind: string(s.Kind), Value: s.Value}.key()
		if allowed[key] {
			used = append(used, key)
		} else {
			unexpected = append(unexpected, fmt.Sprintf("%s (provider %s)", key, s.Provider))
		}
	}
	c.Meta.noteShapes(c.Name, used)
	if len(unexpected) > 0 {
		sort.Strings(unexpected)
		t.Errorf("adapter reported response shapes it does not translate (content was dropped or defaulted):\n  %s\n"+
			"Translate them, or allowlist each in %s/%s under allowed_unknown_shapes with a reason.",
			strings.Join(unexpected, "\n  "), c.Meta.dir, MetaFile)
	}
}
