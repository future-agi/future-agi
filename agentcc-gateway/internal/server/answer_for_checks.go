package server

import (
	"bytes"
	"encoding/json"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	anthropicprov "github.com/futureagi/agentcc-gateway/internal/providers/anthropic"
	geminiprov "github.com/futureagi/agentcc-gateway/internal/providers/gemini"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

// The output checks read the answer on rc.Response, which the post-plugins
// receive. A handler that relays the provider's own answer (a pass-through:
// /v1/messages to an Anthropic-format provider, /v1beta to a Google-format
// one, /v1/responses) puts the answer the client gets on rc.Response through
// these helpers whenever a check reads the answer (rc.Flags.AnswerReaders,
// set by the guardrail plugin before the provider call). The handler still
// serves the provider's own bytes, or the guardrail's error when a check
// withholds the answer.

// The reasons a handler gives when no output check can read the answer
// (models.RequestContext.MarkAnswerUnavailable). The guardrail plugin puts
// them in its refusal.
const (
	answerUnreadable           = "the gateway could not read the provider's answer"
	answerOfBackgroundResponse = "the answer of a background response does not pass through the gateway; send the request without background"
)

// markAnswerUnreadable records that the answer the provider sent could not be
// read for the output checks, and leaves the post stage a response to
// withhold: the guardrail plugin refuses it when a check on the answer would
// block, rather than let it out unchecked.
func markAnswerUnreadable(rc *models.RequestContext) {
	markAnswerUnreadableBecause(rc, answerUnreadable)
}

// markAnswerUnreadableBecause is markAnswerUnreadable with the reason the
// guardrail plugin's refusal gives.
func markAnswerUnreadableBecause(rc *models.RequestContext, reason string) {
	rc.MarkAnswerUnavailable(reason)
	if rc.Response == nil {
		model := rc.ResolvedModel
		if model == "" {
			model = rc.Model
		}
		rc.Response = &models.ChatCompletionResponse{Object: "chat.completion", Model: model}
	}
}

// giveChecksTheAnswer puts answer, the canonical form of an answer the
// handler relays as the provider sent it, on rc.Response, keeping the model
// and usage the handler already set there for pricing (none when the answer
// reported no usage, so the cost plugin still prices nothing rather than
// zero).
func giveChecksTheAnswer(rc *models.RequestContext, answer *models.ChatCompletionResponse) {
	if answer == nil {
		return
	}
	model, usage := rc.ResolvedModel, (*models.Usage)(nil)
	if model == "" {
		model = rc.Model
	}
	if rc.Response != nil {
		model, usage = rc.Response.Model, rc.Response.Usage
	}
	answer.Model, answer.Usage = model, usage
	rc.Response = answer
}

// giveChecksTheStreamedAnswer puts the answer a stream sent, as capture
// assembled it, on rc.Response, after the handler set the stream's usage
// there. A stream that sent no text leaves rc as it is.
func giveChecksTheStreamedAnswer(rc *models.RequestContext, capture *streamCapture) {
	if !capture.hasText() {
		return
	}
	if rc.Response == nil {
		model := rc.ResolvedModel
		if model == "" {
			model = rc.Model
		}
		rc.Response = &models.ChatCompletionResponse{Object: "chat.completion", Model: model}
	}
	capture.applyTo(rc.Response)
}

// maxSSELineBytes bounds one line of a relayed stream that sseAnswer holds
// while it waits for the line's end. A longer line is skipped.
const maxSSELineBytes = 4 << 20

// sseAnswer assembles the answer of a server-sent-event stream the gateway
// relays as the provider sent it. Each data line goes through decode, which
// turns the event into a canonical chunk (its text, or its finish reason),
// and the chunks go to a streamCapture, under its bound. It is nil when no
// check reads the answer, and its methods are safe on nil.
type sseAnswer struct {
	capture *streamCapture
	decode  func(data []byte) (models.StreamChunk, bool)
	line    []byte // the current line, until its newline arrives
	skip    bool   // the current line passed maxSSELineBytes
	skipped bool   // a line passed maxSSELineBytes, so it was not read
}

func newSSEAnswer(enabled bool, decode func(data []byte) (models.StreamChunk, bool)) *sseAnswer {
	if !enabled {
		return nil
	}
	return &sseAnswer{capture: newStreamCapture(true), decode: decode}
}

// Write takes the next bytes of the relayed stream, which split lines
// anywhere.
func (a *sseAnswer) Write(p []byte) {
	if a == nil {
		return
	}
	for len(p) > 0 {
		end := bytes.IndexByte(p, '\n')
		if end < 0 {
			a.hold(p)
			return
		}
		a.hold(p[:end])
		if !a.skip {
			a.readLine(a.line)
		}
		a.line, a.skip = a.line[:0], false
		p = p[end+1:]
	}
}

func (a *sseAnswer) hold(p []byte) {
	if a.skip {
		return
	}
	if len(a.line)+len(p) > maxSSELineBytes {
		a.line, a.skip, a.skipped = a.line[:0], true, true
		return
	}
	a.line = append(a.line, p...)
}

func (a *sseAnswer) readLine(line []byte) {
	data, ok := bytes.CutPrefix(bytes.TrimSuffix(line, []byte("\r")), []byte("data:"))
	if !ok {
		return
	}
	if chunk, ok := a.decode(bytes.TrimPrefix(data, []byte(" "))); ok {
		a.capture.observe(chunk)
	}
}

// holdAll makes the assembly hold what a held stream may hold
// (streamCapture.holdAll). Safe on nil.
func (a *sseAnswer) holdAll() {
	if a != nil {
		a.capture.holdAll()
	}
}

// complete reports whether the assembly holds the whole answer the stream
// sent: no line was skipped and the capture is complete. True on nil, where
// nothing is assembled because no check reads the answer.
func (a *sseAnswer) complete() bool {
	return a == nil || (!a.skipped && a.capture.complete())
}

// giveTo puts the assembled answer on rc.Response (giveChecksTheStreamedAnswer).
func (a *sseAnswer) giveTo(rc *models.RequestContext) {
	if a == nil {
		return
	}
	giveChecksTheStreamedAnswer(rc, a.capture)
}

// textChunk and finishChunk are a stream's text and its finish reason as
// canonical chunks, for choice index.
func textChunk(index int, text string) models.StreamChunk {
	return models.StreamChunk{Choices: []models.StreamChoice{{Index: index, Delta: models.Delta{Content: &text}}}}
}

func finishChunk(index int, finish string) models.StreamChunk {
	return models.StreamChunk{Choices: []models.StreamChoice{{Index: index, FinishReason: &finish}}}
}

// anthropicStreamDelta decodes one event of a Messages API stream: a text
// delta, or the message's stop reason. Tool input and thinking deltas are not
// assembled, as on a chat stream.
func anthropicStreamDelta(data []byte) (models.StreamChunk, bool) {
	var ev struct {
		Type  string `json:"type"`
		Delta struct {
			Type       string `json:"type"`
			Text       string `json:"text"`
			StopReason string `json:"stop_reason"`
		} `json:"delta"`
	}
	if json.Unmarshal(data, &ev) != nil {
		return models.StreamChunk{}, false
	}
	switch {
	case ev.Type == "content_block_delta" && ev.Delta.Type == "text_delta" && ev.Delta.Text != "":
		return textChunk(0, ev.Delta.Text), true
	case ev.Type == "message_delta" && ev.Delta.StopReason != "":
		return finishChunk(0, anthropicprov.FinishReason(ev.Delta.StopReason)), true
	}
	return models.StreamChunk{}, false
}

// genaiStreamDelta decodes one event of a streamGenerateContent stream: each
// candidate's text, at the candidate's own index, and its finish reason.
// Thought summaries are kept out of the text, as a chat answer keeps them out
// of its content.
func genaiStreamDelta(data []byte) (models.StreamChunk, bool) {
	var ev struct {
		Candidates []struct {
			Index   *int `json:"index"`
			Content struct {
				Parts []struct {
					Text    string `json:"text"`
					Thought bool   `json:"thought"`
				} `json:"parts"`
			} `json:"content"`
			FinishReason string `json:"finishReason"`
		} `json:"candidates"`
	}
	if json.Unmarshal(data, &ev) != nil || len(ev.Candidates) == 0 {
		return models.StreamChunk{}, false
	}
	var chunk models.StreamChunk
	for i, candidate := range ev.Candidates {
		choice := models.StreamChoice{Index: i}
		if candidate.Index != nil {
			choice.Index = *candidate.Index
		}
		var text strings.Builder
		for _, part := range candidate.Content.Parts {
			if !part.Thought {
				text.WriteString(part.Text)
			}
		}
		if text.Len() > 0 {
			content := text.String()
			choice.Delta.Content = &content
		}
		if candidate.FinishReason != "" {
			finish := geminiprov.FinishReason(candidate.FinishReason)
			choice.FinishReason = &finish
		}
		chunk.Choices = append(chunk.Choices, choice)
	}
	return chunk, true
}

// responsesStreamDecoder decodes the events of one Responses API stream: each
// output_text delta, and the finish at response.completed (or
// response.incomplete), with the completed response's text when the stream
// sent no delta. Refusal and reasoning deltas are not assembled.
func responsesStreamDecoder() func(data []byte) (models.StreamChunk, bool) {
	sawText := false
	return func(data []byte) (models.StreamChunk, bool) {
		var ev struct {
			Type     string          `json:"type"`
			Delta    string          `json:"delta"`
			Response json.RawMessage `json:"response"`
		}
		if json.Unmarshal(data, &ev) != nil {
			return models.StreamChunk{}, false
		}
		switch ev.Type {
		case "response.output_text.delta":
			if ev.Delta == "" {
				return models.StreamChunk{}, false
			}
			sawText = true
			return textChunk(0, ev.Delta), true
		case "response.completed", "response.incomplete":
			finish := "stop"
			if ev.Type == "response.incomplete" {
				finish = "length"
			}
			chunk := finishChunk(0, finish)
			if !sawText {
				if text := openai.ResponsesStreamAnswerText(ev.Response); text != "" {
					chunk.Choices[0].Delta.Content = &text
				}
			}
			return chunk, true
		}
		return models.StreamChunk{}, false
	}
}
