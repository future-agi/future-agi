package requestview

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// CompletionPrompt is a /v1/completions request that the handler forwards
// to an OpenAI-compatible provider's own completions API with its prompt as
// the client sent it. The checks read the chat request the handler built
// from that prompt (rc.Request, one user message); ApplyChecks writes what a
// check rewrote there into the prompt sent, and ChatRequest says when that
// prompt cannot be read (token ids). It implements
// models.PassThroughRequest.
type CompletionPrompt struct {
	req  *models.CompletionRequest
	chat *models.ChatCompletionRequest
	read json.RawMessage // the chat request's content, as built
}

// Completion is req's prompt, which the checks read as chat, the chat
// request built from it (req.ToChatRequest).
func Completion(req *models.CompletionRequest, chat *models.ChatCompletionRequest) *CompletionPrompt {
	p := &CompletionPrompt{req: req, chat: chat}
	if len(chat.Messages) == 1 {
		p.read = chat.Messages[0].Content
	}
	return p
}

// ChatRequest is the chat request the checks read. It is nil, and the
// reason says why, when the prompt is not text the gateway can read: token
// ids (or a list of them, or a list that is not all strings), which the
// provider decodes into text the model reads while the chat request holds
// only their JSON text.
func (p *CompletionPrompt) ChatRequest(context.Context) (*models.ChatCompletionRequest, string) {
	switch first(p.req.Prompt) {
	case 0, '"', 'n': // no prompt, a string, null
		return p.chat, ""
	case '[':
		var prompts []string
		if json.Unmarshal(p.req.Prompt, &prompts) == nil {
			return p.chat, ""
		}
		return nil, "the gateway cannot read this request: prompt is a list of token ids, or not all text"
	}
	return nil, "the gateway cannot read this request: prompt is neither a string nor a list of strings"
}

// ApplyChecks writes a check's rewrite of the chat request's message into
// the prompt: a prompt string becomes the rewritten text, and a list of
// prompts, which the chat request holds joined with newlines, gets each its
// share of it (split). A rewrite that runs across two prompts, or of a
// prompt of token ids, cannot be written, and is an error; the prompt is
// then left as it was.
func (p *CompletionPrompt) ApplyChecks() error {
	if len(p.chat.Messages) != 1 {
		return fmt.Errorf("a check changed the number of messages (%d, built 1)", len(p.chat.Messages))
	}
	content := p.chat.Messages[0].Content
	if bytes.Equal(content, p.read) {
		return nil
	}
	rewritten, ok := contentText(content)
	if !ok {
		return errors.New("a check rewrote the prompt into content that is not text")
	}
	var prompt json.RawMessage
	switch first(p.req.Prompt) {
	case '"':
		prompt = marshal(rewritten)
	case '[':
		var prompts []string
		if json.Unmarshal(p.req.Prompt, &prompts) != nil {
			return errors.New("the rewrite cannot be written into a prompt of token ids")
		}
		shares, keep, err := split(prompts, "\n", rewritten)
		if err != nil {
			return err
		}
		for _, k := range keep {
			if !k {
				return errors.New("the rewrite runs across two prompts of the list")
			}
		}
		prompt = marshal(shares)
	default:
		return errors.New("the rewrite cannot be written into this prompt")
	}
	p.req.Prompt = prompt
	p.read = content
	return nil
}
