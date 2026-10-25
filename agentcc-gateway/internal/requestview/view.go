// Package requestview reads a request the handler sets no chat request for
// into the chat shape the guardrail checks read: a body that the gateway
// passes through to a provider's own API as the client sent it
// (/v1/messages to an Anthropic-format provider, /v1beta to a Google-format
// one, /v1/responses, the Assistants API and vector store search: View), and
// a request of an API other than chat that the handler sends from its own
// type (embeddings, images, audio, rerank, search, OCR, a video job:
// Fields). The handler leaves rc.Request nil on those paths; the checks on
// the request read them through these views.
//
// The chat shape is the one the gateway's translators give such a request on
// its way to a provider of another format: the system prompt as a system
// message, each turn's text as one message (with its images, when it has
// any), tool results as tool messages, tool calls as tool calls, and the
// tools by name. Text the provider reads that has no place in a chat message
// (a document's text, a search result, a prompt's variables) is read as text
// of the turn that carries it. What the checks do not read: images, files
// and inline data (on every route), and content stored at the provider and
// named by id.
package requestview

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// View is a body the gateway sends to a provider as the client sent it, as
// the guardrail checks read it. It implements models.PassThroughRequest. A
// View is read on the request's goroutine; it is not safe for concurrent use.
type View struct {
	body []byte
	read func(d *doc) (*chat, error)

	done   bool   // read has run
	chat   *chat  // what read gave, nil when it failed
	reason string // why read failed, or could not read all of the body
}

// Anthropic is a Messages API body.
func Anthropic(body []byte) *View { return &View{body: body, read: readAnthropic} }

// AnthropicCountTokens is a /v1/messages/count_tokens body, read as a
// Messages API body. Its answer is a token count, so the checks on the
// answer do not read it (NoChatAnswerView).
func AnthropicCountTokens(body []byte) NoChatAnswerView {
	return NoChatAnswerView{Anthropic(body)}
}

// NoChatAnswerView is a View of a body whose API answers with something
// other than model text (embedContent): it implements models.NoChatAnswer,
// so the checks on the answer do not read it.
type NoChatAnswerView struct{ *View }

// NoChatAnswer marks the view (models.NoChatAnswer).
func (NoChatAnswerView) NoChatAnswer() {}

// Body is the body the handler sends.
func (v *View) Body() []byte { return v.body }

// ChatRequest is the body in the chat shape, read on the first call, in one
// pass over the body (doc) that stops when ctx ends. It is nil, and reason
// says why, when the body cannot be read into that shape, or when ctx ended
// first; what is read then is kept, so the body is never read again. When a
// value the readers walk is nested more than MaxDepth deep, it holds what is
// read above that, and reason says where (a check that would block or
// rewrite the request refuses it).
func (v *View) ChatRequest(ctx context.Context) (*models.ChatCompletionRequest, string) {
	if !v.done {
		v.done = true
		var why string
		v.chat, why = v.readBody(ctx, v.body)
		if why != "" {
			v.reason = "the gateway cannot read this request: " + why
		}
	}
	if v.chat == nil {
		return nil, v.reason
	}
	return v.chat.req, v.reason
}

// readBody reads body under ctx: the chat request it holds, nil when it
// cannot be read, and why not all of it can be read ("" when it can).
func (v *View) readBody(ctx context.Context, body []byte) (*chat, string) {
	d := newDoc(ctx, body)
	c, err := v.read(d)
	switch {
	case d.err != nil:
		return nil, "the request ended before the gateway read it"
	case d.deep != "":
		why := fmt.Sprintf("%s holds a value nested more than %d levels deep", d.deep, MaxDepth)
		if err != nil {
			return nil, why
		}
		return c, why
	case err != nil:
		return nil, err.Error()
	}
	return c, ""
}

// chat is a body read into the chat shape, and where each message's text is
// in the body.
type chat struct {
	req *models.ChatCompletionRequest
	// Per message of req: the body's texts it holds, in order; what joins
	// them in the text the checks read ("\n" in a string content, " "
	// between content parts, as pii-detection joins parts); and the content
	// as read, to tell a message a check rewrote (ApplyChecks).
	texts [][]text
	seps  []string
	read  []json.RawMessage
	// Per message of req: where each of its tool calls' arguments is in the
	// body, and the arguments as read, to tell a call a check rewrote
	// (ApplyChecks).
	calls [][]call
}

// call is where a tool call's arguments are in the body: a JSON value (an
// Anthropic tool_use's input, a Gemini functionCall's args, a Responses
// shell call's action), or, with str, a string (a Responses function call's
// arguments, a custom tool call's input, a code interpreter's code). at is
// nil when the body holds no arguments (an Anthropic tool_use without
// input, read as {}), which nothing can be written into.
type call struct {
	at   path
	str  bool
	read string
}

// path is where a value is in a JSON document: object keys and array
// indices, from the top.
type path []any

func (p path) String() string {
	var b strings.Builder
	for _, e := range p {
		switch e := e.(type) {
		case int:
			b.WriteString("[" + strconv.Itoa(e) + "]")
		case string:
			if b.Len() > 0 {
				b.WriteByte('.')
			}
			b.WriteString(e)
		}
	}
	return b.String()
}

// to is p with elems appended, in a new slice.
func (p path) to(elems ...any) path {
	out := make(path, 0, len(p)+len(elems))
	return append(append(out, p...), elems...)
}

// text is one text of the body that a chat message holds.
type text struct {
	at    path   // where the value is
	value string // what the checks read
	// json: the value is a JSON value other than a string (a Gemini
	// function response), which the checks read as its JSON text as the
	// provider decodes it (canonicalJSON).
	json bool
	// part is the array element that holds this text and nothing else the
	// checks read (a text block or part), nil when there is none.
	part path
}

// message is a chat message being assembled from the body.
type message struct {
	role       string
	name       string
	toolCallID string
	toolCalls  []models.ToolCall
	calls      []call  // where each of toolCalls' arguments is
	pieces     []piece // its texts and images, in order
}

// addCall adds a tool call whose arguments are at, a JSON value (or, with
// str, a string), nil when the body holds none.
func (m *message) addCall(id, name, arguments string, at path, str bool) {
	m.toolCalls = append(m.toolCalls, models.ToolCall{ID: id, Type: "function",
		Function: models.FunctionCall{Name: name, Arguments: arguments}})
	m.calls = append(m.calls, call{at: at, str: str, read: arguments})
}

// piece is one text or one image of a message.
type piece struct {
	text  *text
	image string // the image's URL, when the piece is an image
}

func (m *message) addText(t text) {
	if t.value == "" {
		return // nothing to read, and nothing a check could rewrite
	}
	m.pieces = append(m.pieces, piece{text: &t})
}

func (m *message) addImage(url string) {
	if url != "" {
		m.pieces = append(m.pieces, piece{image: url})
	}
}

// builder assembles the chat request from d.
type builder struct {
	c chat
	d *doc
}

func newBuilder(d *doc) *builder {
	return &builder{c: chat{req: &models.ChatCompletionRequest{}}, d: d}
}

// add finishes m and appends it, unless it holds nothing: no text, no image
// and no tool call. A message with an image holds its pieces as content
// parts, whose text the checks read joined with a space; any other holds
// its texts as one string, joined with a newline.
func (b *builder) add(m *message) {
	if len(m.pieces) == 0 && len(m.toolCalls) == 0 {
		return
	}
	msg := models.Message{Role: m.role, Name: m.name, ToolCallID: m.toolCallID, ToolCalls: m.toolCalls}
	var texts []text
	hasImage := false
	for _, p := range m.pieces {
		if p.text != nil {
			texts = append(texts, *p.text)
		} else {
			hasImage = true
		}
	}
	sep := "\n"
	switch {
	case hasImage:
		sep = " "
		parts := make([]map[string]any, 0, len(m.pieces))
		for _, p := range m.pieces {
			if p.text != nil {
				parts = append(parts, map[string]any{"type": "text", "text": p.text.value})
			} else {
				parts = append(parts, map[string]any{"type": "image_url", "image_url": map[string]string{"url": p.image}})
			}
		}
		msg.Content = marshal(parts)
	case len(texts) > 0:
		values := make([]string, len(texts))
		for i, t := range texts {
			values[i] = t.value
		}
		msg.Content = marshalString(strings.Join(values, "\n"))
	}
	b.c.req.Messages = append(b.c.req.Messages, msg)
	b.c.texts = append(b.c.texts, texts)
	b.c.seps = append(b.c.seps, sep)
	b.c.read = append(b.c.read, msg.Content)
	b.c.calls = append(b.c.calls, m.calls)
}

// tool adds a tool the request offers. Its parameters are a copy: the
// readers' values are slices of the body.
func (b *builder) tool(name, description string, parameters json.RawMessage) {
	if name == "" {
		return
	}
	if parameters != nil {
		parameters = append(json.RawMessage(nil), parameters...)
	}
	b.c.req.Tools = append(b.c.req.Tools, models.Tool{Type: "function",
		Function: models.ToolFunction{Name: name, Description: description, Parameters: parameters}})
}

// hasTool says whether the request already offers a tool named name.
func (b *builder) hasTool(name string) bool {
	for _, t := range b.c.req.Tools {
		if t.Function.Name == name {
			return true
		}
	}
	return false
}

// marshal is v as JSON, without HTML escaping, so the checks read text as
// the client wrote it.
func marshal(v any) json.RawMessage {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	return bytes.TrimSuffix(buf.Bytes(), []byte("\n"))
}

// errTooDeep is a value nested more than MaxDepth deep, which
// canonicalJSON does not write.
var errTooDeep = fmt.Errorf("nested more than %d levels deep", MaxDepth)

// canonicalJSON is raw, a JSON value of the body at path at, written again
// as a parser that decodes it reads it: each string and key with its
// escapes decoded, written with only the escapes JSON requires (marshal),
// and no white space between values. Keys keep their order and numbers
// their digits. A rewrite of this text that is still canonical reads the
// same when read again. Each object and list is decoded once (doc); one
// inside MaxDepth others is not written, and the error is errTooDeep.
func (d *doc) canonicalJSON(raw json.RawMessage, at path) ([]byte, error) {
	var buf bytes.Buffer
	if err := d.writeCanonical(&buf, raw, len(at)); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// writeCanonical writes raw, a value inside depth objects and lists of the
// body (canonicalJSON).
func (d *doc) writeCanonical(buf *bytes.Buffer, raw json.RawMessage, depth int) error {
	raw = bytes.TrimSpace(raw)
	switch first(raw) {
	case '{':
		if depth >= MaxDepth {
			return errTooDeep
		}
		keys, values, ok := d.members(raw)
		if !ok {
			return d.notJSON("an object")
		}
		buf.WriteByte('{')
		for i, key := range keys {
			if i > 0 {
				buf.WriteByte(',')
			}
			buf.Write(marshal(key))
			buf.WriteByte(':')
			if err := d.writeCanonical(buf, values[i], depth+1); err != nil {
				return err
			}
		}
		buf.WriteByte('}')
	case '[':
		if depth >= MaxDepth {
			return errTooDeep
		}
		elems, ok := d.array(raw)
		if !ok {
			return d.notJSON("a list")
		}
		buf.WriteByte('[')
		for i, elem := range elems {
			if i > 0 {
				buf.WriteByte(',')
			}
			if err := d.writeCanonical(buf, elem, depth+1); err != nil {
				return err
			}
		}
		buf.WriteByte(']')
	case '"':
		s, ok := asString(raw)
		if !ok {
			return errors.New("a string is not JSON")
		}
		buf.Write(appendJSONString(buf.AvailableBuffer(), s))
	default:
		if !json.Valid(raw) {
			return errors.New("a value is not JSON")
		}
		buf.Write(raw)
	}
	return nil
}

// notJSON is the error of a value (what) that could not be decoded: the
// read stopped (its context ended, or the value is nested too deep), or the
// value is not JSON.
func (d *doc) notJSON(what string) error {
	switch {
	case d.err != nil:
		return d.err
	case d.deep != "":
		return errTooDeep
	}
	return errors.New(what + " is not JSON")
}

// ─── Reading JSON ────────────────────────────────────────────────────────────

// readError is a body that does not have the shape its API defines, at path.
func readError(at path, format string, args ...any) error {
	return fmt.Errorf("%s %s", at, fmt.Sprintf(format, args...))
}

// absent says whether raw is missing or null.
func absent(raw json.RawMessage) bool {
	raw = bytes.TrimSpace(raw)
	return len(raw) == 0 || bytes.Equal(raw, []byte("null"))
}

// first is raw's first byte that is not white space, or 0.
func first(raw json.RawMessage) byte {
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 {
		return 0
	}
	return raw[0]
}

// asString is raw, with white space around it, when it is a JSON string:
// its text as json.Unmarshal decodes it.
func asString(raw json.RawMessage) (string, bool) {
	if first(raw) != '"' {
		return "", false
	}
	return decodeString(bytes.TrimSpace(raw))
}

// stringField is o[key] when it is a string; "" when it is absent. A value of
// another type is an error.
func stringField(o map[string]json.RawMessage, at path, key string) (string, error) {
	raw, ok := o[key]
	if !ok || absent(raw) {
		return "", nil
	}
	s, ok := asString(raw)
	if !ok {
		return "", readError(at.to(key), "is not a string")
	}
	return s, nil
}

// field is the first of keys that o has, with its key, for an API whose
// parser takes a field under more than one name (Google's takes both
// lowerCamelCase and snake_case).
func field(o map[string]json.RawMessage, keys ...string) (json.RawMessage, string) {
	for _, k := range keys {
		if raw, ok := o[k]; ok && !absent(raw) {
			return raw, k
		}
	}
	return nil, ""
}

// textKeys are the fields that hold text the model reads in the blocks and
// items of the providers' APIs: a text, a title, and a tool's output
// (output, content, a command's stdout and stderr, a code interpreter's
// logs, a changed file's lines). Fields that hold ids, URLs or encoded data
// are not among them.
var textKeys = map[string]bool{
	"text": true, "title": true, "output": true, "content": true,
	"stdout": true, "stderr": true, "logs": true, "lines": true,
}

// pickText adds to m every string under a text field (textKeys) in raw, at
// any depth, and each string of a list under one, for a block or item of a
// type the reader does not know or does not read field by field: text the
// provider may read is read rather than skipped. Values under "input" and
// "arguments" (a tool call's arguments) are not text the provider reads as
// a prompt and are left out. An object's fields are read in the body's
// order; of a repeated key, the last, which a parser keeps. raw is at path
// at; an object or a list inside MaxDepth others is not read, and is
// recorded (tooDeep) as in at. Each object and list is decoded once (doc).
func (d *doc) pickText(m *message, raw json.RawMessage, at path) {
	w := &walk{d: d, m: m, root: at}
	w.pick(raw)
}

// walk is pickText's walk from root: where it is below root (steps), kept
// as keys and indices and made a path only for a text it reads.
type walk struct {
	d     *doc
	m     *message
	root  path
	steps []step
}

// step is one element of a path below a walk's root: a key, or an index.
type step struct {
	key   string
	index int
	isKey bool
}

// at is the path of the value the walk is at, with more appended.
func (w *walk) at(more ...any) path {
	p := make(path, 0, len(w.root)+len(w.steps)+len(more))
	p = append(p, w.root...)
	for _, s := range w.steps {
		if s.isKey {
			p = append(p, s.key)
		} else {
			p = append(p, s.index)
		}
	}
	return append(p, more...)
}

// pick reads raw, the value at w.at().
func (w *walk) pick(raw json.RawMessage) {
	d, depth := w.d, len(w.root)+len(w.steps)
	switch first(raw) {
	case '{':
		if depth >= MaxDepth {
			d.tooDeep(w.root)
			return
		}
		keys, values, ok := d.members(raw)
		if !ok {
			return
		}
		last := lastOf(keys)
		for i, k := range keys {
			if !last(i) || k == "input" || k == "arguments" {
				continue
			}
			v := values[i]
			w.steps = append(w.steps, step{key: k, isKey: true})
			w.member(k, v, depth+1)
			w.steps = w.steps[:len(w.steps)-1]
		}
	case '[':
		if depth >= MaxDepth {
			d.tooDeep(w.root)
			return
		}
		a, ok := d.array(raw)
		if !ok {
			return
		}
		for i, v := range a {
			w.steps = append(w.steps, step{index: i})
			w.pick(v)
			w.steps = w.steps[:len(w.steps)-1]
		}
	}
}

// member reads v, the value of key k, inside depth objects and lists: a
// string under a text field is a text, and so is each string of a list
// under one.
func (w *walk) member(k string, v json.RawMessage, depth int) {
	if textKeys[k] {
		if s, ok := asString(v); ok {
			w.m.addText(text{at: w.at(), value: s})
			return
		}
		if first(v) == '[' && depth < MaxDepth {
			if list, ok := w.d.array(v); ok {
				for j, e := range list {
					if s, ok := asString(e); ok {
						w.m.addText(text{at: w.at(j), value: s})
						continue
					}
					w.steps = append(w.steps, step{index: j})
					w.pick(e)
					w.steps = w.steps[:len(w.steps)-1]
				}
				return
			}
		}
	}
	w.pick(v)
}
