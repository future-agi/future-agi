package requestview

import (
	"bytes"
	"encoding/json"
	"errors"
)

// The Assistants API and the Vector Stores API's search, which the handler
// proxies to OpenAI with the body as the client sent it, and Gemini's
// embedContent, passed through to a Google-format provider. Each is a View:
// the checks read the body in the chat shape, and a check's rewrite is
// written into the body sent.

// Assistant is the body of an assistant's creation or update
// (POST /v1/assistants, POST /v1/assistants/{id}): its instructions, as a
// system message, and its tools by name. An assistant's name, description
// and metadata are not prompt text the model reads, and are not read.
func Assistant(body []byte) *View { return &View{body: body, read: readAssistant} }

// Thread is the body of a thread's creation (POST /v1/threads): its
// messages.
func Thread(body []byte) *View { return &View{body: body, read: readThread} }

// ThreadMessage is the body of a message's creation
// (POST /v1/threads/{id}/messages).
func ThreadMessage(body []byte) *View { return &View{body: body, read: readThreadMessage} }

// Run is the body of a run's creation (POST /v1/threads/{id}/runs): its
// instructions and additional instructions, as system messages, its
// additional messages, and its tools by name.
func Run(body []byte) *View { return &View{body: body, read: readRun} }

// ThreadAndRun is the body of POST /v1/threads/runs: the thread's messages,
// then the run's instructions and tools, as on a run.
func ThreadAndRun(body []byte) *View { return &View{body: body, read: readThreadAndRun} }

// ToolOutputs is the body of POST /v1/threads/{id}/runs/{id}/submit_tool_outputs:
// each output, as a tool message.
func ToolOutputs(body []byte) *View { return &View{body: body, read: readToolOutputs} }

// VectorStoreSearch is the body of POST /v1/vector_stores/{id}/search: its
// query, a string or a list of strings, each as a user message.
func VectorStoreSearch(body []byte) *View { return &View{body: body, read: readVectorStoreSearch} }

// GeminiEmbedContent is an embedContent body: its content's text parts, as
// a user message, and its title, as another. Its answer is an embedding, so
// the checks on the answer do not read it (NoChatAnswerView).
func GeminiEmbedContent(body []byte) NoChatAnswerView {
	return NoChatAnswerView{&View{body: body, read: readGeminiEmbedContent}}
}

// assistantsObject is an Assistants API body as an object. An empty body, or
// one of white space only (as an empty thread is created), reads as an empty
// object: it holds nothing to read, and the request goes on as it does with
// {}.
func assistantsObject(d *doc) (map[string]json.RawMessage, bool) {
	if len(bytes.TrimSpace(d.body)) == 0 {
		return map[string]json.RawMessage{}, true
	}
	return d.object(d.body)
}

func readAssistant(d *doc) (*chat, error) {
	top, ok := assistantsObject(d)
	if !ok {
		return nil, errors.New("the body is not a JSON object")
	}
	b := newBuilder(d)
	b.c.req.Model, _ = stringField(top, nil, "model")
	if err := assistantsInstructions(b, top, nil, "instructions"); err != nil {
		return nil, err
	}
	if err := assistantsTools(b, top, nil); err != nil {
		return nil, err
	}
	return &b.c, nil
}

func readThread(d *doc) (*chat, error) {
	top, ok := assistantsObject(d)
	if !ok {
		return nil, errors.New("the body is not a JSON object")
	}
	b := newBuilder(d)
	if err := assistantsMessages(b, top, nil, "messages"); err != nil {
		return nil, err
	}
	return &b.c, nil
}

func readThreadMessage(d *doc) (*chat, error) {
	b := newBuilder(d)
	if err := assistantsMessage(b, d.body, nil); err != nil {
		return nil, err
	}
	return &b.c, nil
}

func readRun(d *doc) (*chat, error) {
	top, ok := assistantsObject(d)
	if !ok {
		return nil, errors.New("the body is not a JSON object")
	}
	b := newBuilder(d)
	if err := readRunAt(b, top); err != nil {
		return nil, err
	}
	if err := assistantsMessages(b, top, nil, "additional_messages"); err != nil {
		return nil, err
	}
	return &b.c, nil
}

// readRunAt reads a run's own fields: its model, its instructions and
// additional instructions, and its tools.
func readRunAt(b *builder, top map[string]json.RawMessage) error {
	b.c.req.Model, _ = stringField(top, nil, "model")
	_ = json.Unmarshal(top["stream"], &b.c.req.Stream)
	if err := assistantsInstructions(b, top, nil, "instructions"); err != nil {
		return err
	}
	if err := assistantsInstructions(b, top, nil, "additional_instructions"); err != nil {
		return err
	}
	return assistantsTools(b, top, nil)
}

func readThreadAndRun(d *doc) (*chat, error) {
	top, ok := assistantsObject(d)
	if !ok {
		return nil, errors.New("the body is not a JSON object")
	}
	b := newBuilder(d)
	if err := readRunAt(b, top); err != nil {
		return nil, err
	}
	if raw := top["thread"]; !absent(raw) {
		thread, ok := d.object(raw)
		if !ok {
			return nil, readError(path{"thread"}, "is not an object")
		}
		if err := assistantsMessages(b, thread, path{"thread"}, "messages"); err != nil {
			return nil, err
		}
	}
	return &b.c, nil
}

func readToolOutputs(d *doc) (*chat, error) {
	top, ok := assistantsObject(d)
	if !ok {
		return nil, errors.New("the body is not a JSON object")
	}
	b := newBuilder(d)
	_ = json.Unmarshal(top["stream"], &b.c.req.Stream)
	raw := top["tool_outputs"]
	if absent(raw) {
		return &b.c, nil
	}
	at := path{"tool_outputs"}
	outputs, ok := d.array(raw)
	if !ok {
		return nil, readError(at, "is not a list")
	}
	for i, raw := range outputs {
		oat := at.to(i)
		o, ok := d.object(raw)
		if !ok {
			return nil, readError(oat, "is not an object")
		}
		id, err := stringField(o, oat, "tool_call_id")
		if err != nil {
			return nil, err
		}
		output, err := stringField(o, oat, "output")
		if err != nil {
			return nil, err
		}
		m := &message{role: "tool", toolCallID: id}
		m.addText(text{at: oat.to("output"), value: output})
		b.add(m)
	}
	return &b.c, nil
}

func readVectorStoreSearch(d *doc) (*chat, error) {
	top, ok := d.object(d.body)
	if !ok {
		return nil, errors.New("the body is not a JSON object")
	}
	b := newBuilder(d)
	raw := top["query"]
	at := path{"query"}
	switch first(raw) {
	case 0, 'n':
	case '"':
		s, _ := asString(raw)
		m := &message{role: "user"}
		m.addText(text{at: at, value: s})
		b.add(m)
	case '[':
		queries, ok := d.array(raw)
		if !ok {
			return nil, readError(at, "is not a list")
		}
		for i, q := range queries {
			s, ok := asString(q)
			if !ok {
				return nil, readError(at.to(i), "is not a string")
			}
			m := &message{role: "user"}
			m.addText(text{at: at.to(i), value: s})
			b.add(m)
		}
	default:
		return nil, readError(at, "is neither a string nor a list of strings")
	}
	return &b.c, nil
}

func readGeminiEmbedContent(d *doc) (*chat, error) {
	top, ok := d.object(d.body)
	if !ok {
		return nil, errors.New("the body is not a JSON object")
	}
	b := newBuilder(d)
	if raw, key := field(top, "content"); raw != nil {
		content, ok := d.object(raw)
		if !ok {
			return nil, readError(path{key}, "is not an object")
		}
		m := &message{role: "user"}
		if err := geminiParts(b, m, content, path{key}); err != nil {
			return nil, err
		}
		b.add(m)
	}
	if raw, key := field(top, "title"); raw != nil {
		s, ok := asString(raw)
		if !ok {
			return nil, readError(path{key}, "is not a string")
		}
		m := &message{role: "user"}
		m.addText(text{at: path{key}, value: s})
		b.add(m)
	}
	return &b.c, nil
}

// assistantsInstructions reads o[key] (at path at), a string the model
// reads as instructions, as a system message.
func assistantsInstructions(b *builder, o map[string]json.RawMessage, at path, key string) error {
	s, err := stringField(o, at, key)
	if err != nil {
		return err
	}
	m := &message{role: "system"}
	m.addText(text{at: at.to(key), value: s})
	b.add(m)
	return nil
}

// assistantsMessages reads o[key] (at path at), a list of messages.
func assistantsMessages(b *builder, o map[string]json.RawMessage, at path, key string) error {
	raw := o[key]
	if absent(raw) {
		return nil
	}
	list, ok := b.d.array(raw)
	if !ok {
		return readError(at.to(key), "is not a list")
	}
	for i, raw := range list {
		if err := assistantsMessage(b, raw, at.to(key, i)); err != nil {
			return err
		}
	}
	return nil
}

// assistantsMessage reads a message of a thread (at path at): its content,
// a string or a list of parts, as a message of its role. A text part is its
// text; an image_url part its image. An image_file part and the message's
// attachments name files stored at the provider, which are not read. The
// text of a part of a type the reader does not know is read (pickText).
func assistantsMessage(b *builder, raw json.RawMessage, at path) error {
	d := b.d
	o, ok := d.object(raw)
	if !ok {
		return readError(at, "is not an object")
	}
	role, err := stringField(o, at, "role")
	if err != nil {
		return err
	}
	if role == "" {
		role = "user"
	}
	m := &message{role: role}
	content := o["content"]
	cat := at.to("content")
	switch first(content) {
	case 0, 'n':
	case '"':
		s, _ := asString(content)
		m.addText(text{at: cat, value: s})
	case '[':
		parts, ok := d.array(content)
		if !ok {
			return readError(cat, "is not a list")
		}
		for j, raw := range parts {
			pat := cat.to(j)
			p, ok := d.object(raw)
			if !ok {
				return readError(pat, "is not an object")
			}
			kind, _ := stringField(p, pat, "type")
			switch kind {
			case "text":
				s, err := stringField(p, pat, "text")
				if err != nil {
					return err
				}
				m.addText(text{at: pat.to("text"), value: s, part: pat})
			case "image_url":
				if u, ok := d.object(p["image_url"]); ok {
					url, _ := stringField(u, pat, "url")
					m.addImage(url)
				}
			case "image_file":
				// A file stored at the provider, named by id: not read.
			default:
				d.pickText(m, raw, pat)
			}
		}
	default:
		return readError(cat, "is neither a string nor a list of parts")
	}
	b.add(m)
	return nil
}

// assistantsTools reads o's tools (at path at): a function by its name, and
// a tool the provider runs (code_interpreter, file_search) by its type.
func assistantsTools(b *builder, o map[string]json.RawMessage, at path) error {
	raw := o["tools"]
	if absent(raw) {
		return nil
	}
	tat := at.to("tools")
	tools, ok := b.d.array(raw)
	if !ok {
		return readError(tat, "is not a list")
	}
	for i, raw := range tools {
		iat := tat.to(i)
		t, ok := b.d.object(raw)
		if !ok {
			return readError(iat, "is not an object")
		}
		kind, _ := stringField(t, iat, "type")
		if kind != "function" {
			b.tool(kind, "", nil)
			continue
		}
		fn, ok := b.d.object(t["function"])
		if !ok {
			return readError(iat.to("function"), "is not an object")
		}
		name, err := stringField(fn, iat.to("function"), "name")
		if err != nil {
			return err
		}
		description, _ := stringField(fn, iat.to("function"), "description")
		b.tool(name, description, fn["parameters"])
	}
	return nil
}
