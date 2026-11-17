package requestview

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Fields is a request of an API other than chat that the handler decodes
// into its own type and sends to the provider from that type (embeddings,
// images, speech, transcriptions and translations, rerank, search, OCR, a
// video job). The checks read the texts the provider reads, each as a
// message of its own in the chat shape, and a check's rewrite of one (a
// remediation) is written back into the field the provider reads it from.
// It implements models.PassThroughRequest. Fields reads the request from
// the decoded value, never from the body: the value is what the provider
// gets, whatever spelling or repeated key the body used.
type Fields struct {
	model string
	read  func() ([]Field, string)

	done   bool
	fields []Field
	reason string
	req    *models.ChatCompletionRequest
	owner  []int             // per message of req: the index of its field
	sent   []json.RawMessage // per message of req: its content as built
}

// Field is one text of a request that the provider reads.
type Field struct {
	// Role is the role of the message the checks read it as: "user" for
	// the caller's input, "system" for instructions.
	Role string
	// At names where the text is in the request, for an error.
	At string
	// Text is the text the provider reads.
	Text string
	// Set writes a rewrite of Text where the provider reads it. A field
	// without Set cannot be rewritten.
	Set func(string)
}

// NewFields is a request whose texts read gives: the texts the provider
// reads, and, when part or all of the request is not text the checks can
// read (token ids, an audio file, a document), why. It is called once when
// a check first reads the request, and again after a rewrite, to read what
// the provider will get.
func NewFields(model string, read func() ([]Field, string)) *Fields {
	return &Fields{model: model, read: read}
}

// NoChatAnswer marks f (models.NoChatAnswer): every API read as Fields
// answers with something other than model text the checks on the answer
// read (an embedding, an image, audio, a ranking, search results, a
// transcription, OCR, a video job), so they do not read its request.
func (f *Fields) NoChatAnswer() {}

// ChatRequest is the request in the chat shape: a message per text the
// provider reads, in order, each of its field's role. It is built on the
// first call. The reason says why part or all of the request cannot be
// read; the request then holds what can be read, and is nil when nothing
// can.
func (f *Fields) ChatRequest(context.Context) (*models.ChatCompletionRequest, string) {
	if !f.done {
		f.done = true
		fields, reason := f.read()
		f.fields = fields
		if reason != "" {
			f.reason = "the gateway cannot read this request: " + reason
		}
		req := &models.ChatCompletionRequest{Model: f.model}
		for i, fl := range fields {
			if fl.Text == "" {
				continue // nothing to read, and nothing a check could rewrite
			}
			content := marshal(fl.Text)
			req.Messages = append(req.Messages, models.Message{Role: fieldRole(fl), Content: content})
			f.owner = append(f.owner, i)
			f.sent = append(f.sent, content)
		}
		if len(req.Messages) > 0 || f.reason == "" {
			f.req = req
		}
	}
	return f.req, f.reason
}

func fieldRole(fl Field) string {
	if fl.Role == "" {
		return "user"
	}
	return fl.Role
}

// ApplyChecks writes into the request what the checks rewrote in the chat
// request: each rewritten message's text goes into the field it was read
// from. The request is then read again, and every text must read as the
// checks left it; otherwise, or when a message's rewrite cannot be written
// (it is not text, or its field cannot be rewritten), an error says why and
// the guardrail plugin refuses the request rather than send what a check
// rewrote. A message counts as rewritten when its content is not the one it
// was built with. It does nothing when the request was never read.
func (f *Fields) ApplyChecks() error {
	if f.req == nil {
		return nil
	}
	msgs := f.req.Messages
	if len(msgs) != len(f.sent) {
		return fmt.Errorf("a check changed the number of messages (%d, read %d)", len(msgs), len(f.sent))
	}
	type write struct {
		field int
		text  string
	}
	var writes []write
	for i := range msgs {
		if bytes.Equal(msgs[i].Content, f.sent[i]) {
			continue
		}
		fl := f.fields[f.owner[i]]
		rewritten, ok := contentText(msgs[i].Content)
		if !ok {
			return fmt.Errorf("a check rewrote %s into content that is not text", fl.At)
		}
		if fl.Set == nil {
			return fmt.Errorf("the rewrite of %s cannot be written", fl.At)
		}
		writes = append(writes, write{field: f.owner[i], text: rewritten})
	}
	if len(writes) == 0 {
		return nil
	}
	for _, w := range writes {
		f.fields[w.field].Set(w.text)
	}
	again, _ := f.read()
	if len(again) != len(f.fields) {
		return fmt.Errorf("the rewritten request reads as %d texts, not %d", len(again), len(f.fields))
	}
	for i := range msgs {
		want, _ := contentText(msgs[i].Content)
		if got := again[f.owner[i]]; got.Text != want {
			return fmt.Errorf("%s of the rewritten request does not read as the checks left it", got.At)
		}
	}
	// The rewrite is applied: the request now holds it, as the checks'
	// request does, and a later call has nothing more to write.
	f.fields = again
	for i := range msgs {
		f.sent[i] = msgs[i].Content
	}
	return nil
}

// ─── The APIs ────────────────────────────────────────────────────────────────

// Embedding is an embeddings request: its input, a string or a list of
// strings, each read as a user message. Token ids (a list of numbers, or a
// list of such lists), which the provider decodes into the text it embeds,
// are not text the gateway can read, nor is an input of any other type.
func Embedding(req *models.EmbeddingRequest) *Fields {
	return NewFields(req.Model, func() ([]Field, string) {
		return stringOrList("input", req.Input, func(raw json.RawMessage) { req.Input = raw }, "token ids")
	})
}

// Image is an image generation request: its prompt.
func Image(req *models.ImageRequest) *Fields {
	return NewFields(req.Model, func() ([]Field, string) {
		return []Field{{At: "prompt", Text: req.Prompt, Set: func(s string) { req.Prompt = s }}}, ""
	})
}

// Speech is a text-to-speech request: the input it speaks.
func Speech(req *models.SpeechRequest) *Fields {
	return NewFields(req.Model, func() ([]Field, string) {
		return []Field{{At: "input", Text: req.Input, Set: func(s string) { req.Input = s }}}, ""
	})
}

// audioUnread is why a transcription or translation cannot be read: what
// the model reads is the audio, which no check reads.
const audioUnread = "its audio file is not text the checks can read"

// Transcription is a transcription request: its prompt is read, and its
// audio file is not, so the request is never read whole.
func Transcription(req *models.TranscriptionRequest) *Fields {
	return NewFields(req.Model, func() ([]Field, string) {
		return []Field{{At: "prompt", Text: req.Prompt, Set: func(s string) { req.Prompt = s }}}, audioUnread
	})
}

// Translation is a translation request, read as a transcription is.
func Translation(req *models.TranslationRequest) *Fields {
	return NewFields(req.Model, func() ([]Field, string) {
		return []Field{{At: "prompt", Text: req.Prompt, Set: func(s string) { req.Prompt = s }}}, audioUnread
	})
}

// Rerank is a rerank request: its query, then each document.
func Rerank(req *models.RerankRequest) *Fields {
	return NewFields(req.Model, func() ([]Field, string) {
		fields := make([]Field, 0, 1+len(req.Documents))
		fields = append(fields, Field{At: "query", Text: req.Query, Set: func(s string) { req.Query = s }})
		for i, d := range req.Documents {
			i := i
			fields = append(fields, Field{At: fmt.Sprintf("documents[%d]", i), Text: d, Set: func(s string) {
				if i < len(req.Documents) {
					req.Documents[i] = s
				}
			}})
		}
		return fields, ""
	})
}

// Search is a search request: its query, a string or a list of strings.
func Search(req *models.SearchRequest) *Fields {
	return NewFields(req.SearchProvider, func() ([]Field, string) {
		return stringOrList("query", req.QueryRaw, func(raw json.RawMessage) { req.QueryRaw = raw }, "not text")
	})
}

// OCR is an OCR request. What the model reads is the document (a document
// or image URL, inline data, or a file at the provider), which no check
// reads, so the request is never read.
func OCR(req *models.OCRRequest) *Fields {
	kind := req.Document.Type
	if kind == "" {
		kind = "document"
	}
	return NewFields(req.Model, func() ([]Field, string) {
		return nil, fmt.Sprintf("its document (%s) is not text the checks can read", kind)
	})
}

// VideoPrompt is a video generation request's prompt, which set rewrites.
func VideoPrompt(model string, prompt *string) *Fields {
	return NewFields(model, func() ([]Field, string) {
		return []Field{{At: "prompt", Text: *prompt, Set: func(s string) { *prompt = s }}}, ""
	})
}

// stringOrList reads raw, the field name of a request that takes a string
// or a list of strings, as one field per string; set writes the field
// again. Any other value cannot be read: a list that is not all strings is
// list (what such a list is: "token ids").
func stringOrList(name string, raw json.RawMessage, set func(json.RawMessage), list string) ([]Field, string) {
	switch first(raw) {
	case 0, 'n': // absent, null: nothing the provider reads
		return nil, ""
	case '"':
		s, ok := asString(raw)
		if !ok {
			return nil, name + " is not a string"
		}
		return []Field{{At: name, Text: s, Set: func(v string) { set(marshal(v)) }}}, ""
	case '[':
		var values []string
		if json.Unmarshal(raw, &values) != nil {
			return nil, name + " is a list of " + list + ", or not all text"
		}
		fields := make([]Field, len(values))
		for i, s := range values {
			i := i
			fields[i] = Field{At: fmt.Sprintf("%s[%d]", name, i), Text: s, Set: func(v string) {
				values[i] = v
				set(marshal(values))
			}}
		}
		return fields, ""
	}
	return nil, name + " is neither a string nor a list of strings"
}
