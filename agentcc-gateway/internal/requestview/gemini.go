package requestview

import (
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"unicode"
)

// Gemini is a generateContent or streamGenerateContent body (stream says
// which) sent to model.
func Gemini(body []byte, model string, stream bool) *View {
	return &View{body: body, read: func(d *doc) (*chat, error) {
		c, err := readGemini(d)
		if c != nil {
			c.req.Model, c.req.Stream = model, stream
		}
		return c, err
	}}
}

// GeminiCountTokens is a countTokens body sent to model: the request whose
// tokens the provider counts, given as contents (with a system instruction
// and tools, as Vertex AI takes them) or as a generateContentRequest, under
// either name. Each is read as a generateContent body is, in that order.
// Its answer is a token count, so the checks on the answer do not read it
// (NoChatAnswerView).
func GeminiCountTokens(body []byte, model string) NoChatAnswerView {
	return NoChatAnswerView{&View{body: body, read: func(d *doc) (*chat, error) {
		top, ok := d.object(d.body)
		if !ok {
			return nil, errors.New("the body is not a JSON object")
		}
		b := newBuilder(d)
		if err := readGeminiAt(b, top, nil); err != nil {
			return nil, err
		}
		if raw, key := field(top, "generateContentRequest", "generate_content_request"); raw != nil {
			inner, ok := d.object(raw)
			if !ok {
				return nil, readError(path{key}, "is not an object")
			}
			if err := readGeminiAt(b, inner, path{key}); err != nil {
				return nil, err
			}
		}
		b.c.req.Model = model
		return &b.c, nil
	}}}
}

// readGemini reads a generateContent body into the chat shape, in the order
// the gateway's translator to other formats gives it: the system instruction
// as a system message, then each content, its function responses first as
// tool messages and its other text, with its images, as one message (a
// "model" content is the assistant's); its function calls are tool calls,
// and the function declarations are the tools, with each tool Google runs
// (Google Search, code execution, URL context, ...) named by its field in
// snake_case (google_search). Google's parser takes each
// field in lowerCamelCase or in snake_case, so both are read. A thought part
// is the model's own and is not read; inline data and a file named by URI
// are read as an image when they are one, and not read otherwise. Google's
// parser also takes one value where a field repeats, and its own examples
// send contents and parts as one object: a repeated field given as one
// object is read as a list of one (geminiList).
func readGemini(d *doc) (*chat, error) {
	top, ok := d.object(d.body)
	if !ok {
		return nil, errors.New("the body is not a JSON object")
	}
	b := newBuilder(d)
	if err := readGeminiAt(b, top, nil); err != nil {
		return nil, err
	}
	return &b.c, nil
}

// readGeminiAt reads top, a generateContent request at path prefix in the
// body, into b (readGemini).
func readGeminiAt(b *builder, top map[string]json.RawMessage, prefix path) error {
	d := b.d
	if raw, key := field(top, "systemInstruction", "system_instruction"); raw != nil {
		at := prefix.to(key)
		m := &message{role: "system"}
		if s, ok := asString(raw); ok {
			m.addText(text{at: at, value: s})
		} else if o, ok := d.object(raw); ok {
			if err := geminiParts(b, m, o, at); err != nil {
				return err
			}
		} else {
			return readError(at, "is not a content")
		}
		b.add(m)
	}

	if raw, key := field(top, "contents"); raw != nil {
		contents, paths, ok := geminiList(d, raw, prefix.to(key))
		if !ok {
			return readError(prefix.to(key), "is neither a list nor an object")
		}
		for i, c := range contents {
			at := paths[i]
			o, ok := d.object(c)
			if !ok {
				return readError(at, "is not an object")
			}
			role, err := stringField(o, at, "role")
			if err != nil {
				return err
			}
			m := &message{role: "user"}
			if role == "model" {
				m.role = "assistant"
			}
			if err := geminiParts(b, m, o, at); err != nil {
				return err
			}
			b.add(m)
		}
	}

	req := b.c.req
	if raw, _ := field(top, "generationConfig", "generation_config"); raw != nil {
		if cfg, ok := d.object(raw); ok {
			if v, _ := field(cfg, "maxOutputTokens", "max_output_tokens"); v != nil {
				_ = json.Unmarshal(v, &req.MaxTokens)
			}
			_ = json.Unmarshal(cfg["temperature"], &req.Temperature)
			if v, _ := field(cfg, "topP", "top_p"); v != nil {
				_ = json.Unmarshal(v, &req.TopP)
			}
		}
	}

	if raw, key := field(top, "tools"); raw != nil {
		tools, tpaths, ok := geminiList(d, raw, prefix.to(key))
		if !ok {
			return readError(prefix.to(key), "is neither a list nor an object")
		}
		for i, raw := range tools {
			if first(raw) != '{' {
				return readError(tpaths[i], "is not an object")
			}
			keys, values, ok := d.members(raw)
			if !ok {
				return readError(tpaths[i], "is not an object")
			}
			for k, dkey := range keys {
				if absent(values[k]) {
					continue
				}
				if dkey != "functionDeclarations" && dkey != "function_declarations" {
					// A tool Google runs (Google Search, code execution, URL
					// context, ...), by its field, in one spelling.
					b.tool(snakeCase(dkey), "", nil)
					continue
				}
				list, dpaths, ok := geminiList(d, values[k], tpaths[i].to(dkey))
				if !ok {
					return readError(tpaths[i].to(dkey), "is neither a list nor an object")
				}
				for j, decl := range list {
					at := dpaths[j]
					fn, ok := d.object(decl)
					if !ok {
						return readError(at, "is not an object")
					}
					name, err := stringField(fn, at, "name")
					if err != nil {
						return err
					}
					description, err := stringField(fn, at, "description")
					if err != nil {
						return err
					}
					b.tool(name, description, fn["parameters"])
				}
			}
		}
	}
	return nil
}

// snakeCase is a field name of a Google API body in snake_case, the
// spelling Google's parser takes besides lowerCamelCase: googleSearch is
// google_search, and google_search is itself.
func snakeCase(name string) string {
	var b strings.Builder
	for i, r := range name {
		if unicode.IsUpper(r) {
			if i > 0 {
				b.WriteByte('_')
			}
			r = unicode.ToLower(r)
		}
		b.WriteRune(r)
	}
	return b.String()
}

// geminiParts reads the parts of content o (at path at) into m, and each
// function response, or code execution result, into a tool message of its
// own, added before m. A function response that is not a string is read as
// its JSON text, decoded as Google decodes it (canonicalJSON).
func geminiParts(b *builder, m *message, o map[string]json.RawMessage, at path) error {
	d := b.d
	raw, key := field(o, "parts")
	if raw == nil {
		return nil
	}
	parts, paths, ok := geminiList(d, raw, at.to(key))
	if !ok {
		return readError(at.to(key), "is neither a list nor an object")
	}
	// A text part is an element of the list that a rewrite may drop; one
	// part given as an object is not.
	inList := first(raw) == '['
	for j, part := range parts {
		pat := paths[j]
		p, ok := d.object(part)
		if !ok {
			return readError(pat, "is not an object")
		}
		if raw, tkey := field(p, "text"); raw != nil {
			s, ok := asString(raw)
			if !ok {
				return readError(pat.to(tkey), "is not a string")
			}
			if thought, _ := field(p, "thought"); strings.TrimSpace(string(thought)) != "true" {
				t := text{at: pat.to(tkey), value: s}
				if inList {
					t.part = pat
				}
				m.addText(t)
			}
		}
		if raw, dkey := field(p, "inlineData", "inline_data"); raw != nil {
			if inline, ok := d.object(raw); ok {
				mime := geminiString(inline, "mimeType", "mime_type")
				data := geminiString(inline, "data")
				if isImage(mime) {
					m.addImage("data:" + mime + ";base64," + data)
				}
			} else {
				return readError(pat.to(dkey), "is not an object")
			}
		}
		if raw, fkey := field(p, "fileData", "file_data"); raw != nil {
			if file, ok := d.object(raw); ok {
				if isImage(geminiString(file, "mimeType", "mime_type")) {
					m.addImage(geminiString(file, "fileUri", "file_uri"))
				}
			} else {
				return readError(pat.to(fkey), "is not an object")
			}
		}
		if raw, ckey := field(p, "functionCall", "function_call"); raw != nil {
			call, ok := d.object(raw)
			if !ok {
				return readError(pat.to(ckey), "is not an object")
			}
			args, argsAt := "{}", path(nil)
			if a := call["args"]; !absent(a) {
				args, argsAt = string(a), pat.to(ckey, "args")
			}
			id := geminiString(call, "id")
			if id == "" {
				id = "call_" + strconv.Itoa(len(b.c.req.Messages)) + "_" + strconv.Itoa(j)
			}
			m.addCall(id, geminiString(call, "name"), args, argsAt, false)
		}
		if raw, rkey := field(p, "functionResponse", "function_response"); raw != nil {
			resp, ok := d.object(raw)
			if !ok {
				return readError(pat.to(rkey), "is not an object")
			}
			tm := &message{role: "tool", name: geminiString(resp, "name"), toolCallID: geminiString(resp, "id")}
			if v := resp["response"]; !absent(v) {
				rat := pat.to(rkey, "response")
				if s, ok := asString(v); ok {
					tm.addText(text{at: rat, value: s})
				} else {
					// Read as Google decodes it before the model reads it:
					// escapes decoded, so text an encoder escaped is read.
					// One nested more than MaxDepth deep is not read.
					value, err := d.canonicalJSON(v, rat)
					switch {
					case errors.Is(err, errTooDeep):
						d.tooDeep(rat)
					case err != nil:
						return readError(rat, "is not JSON: %v", err)
					default:
						tm.addText(text{at: rat, value: string(value), json: true})
					}
				}
			}
			b.add(tm)
		}
		if raw, rkey := field(p, "codeExecutionResult", "code_execution_result"); raw != nil {
			if res, ok := d.object(raw); ok {
				tm := &message{role: "tool"}
				out, err := stringField(res, pat.to(rkey), "output")
				if err != nil {
					return err
				}
				tm.addText(text{at: pat.to(rkey, "output"), value: out})
				b.add(tm)
			}
		}
	}
	return nil
}

// geminiList is the items of a repeated field of a Google API body (raw, at
// path at), with where each is: a list's elements, or, as Google's parser
// takes one value for a repeated field, an object as the only item, at the
// field itself. ok is false for any other value.
func geminiList(d *doc, raw json.RawMessage, at path) (items []json.RawMessage, paths []path, ok bool) {
	switch first(raw) {
	case '[':
		items, ok = d.array(raw)
		if !ok {
			return nil, nil, false
		}
		paths = make([]path, len(items))
		for i := range items {
			paths[i] = at.to(i)
		}
		return items, paths, true
	case '{':
		return []json.RawMessage{raw}, []path{at}, true
	}
	return nil, nil, false
}

// geminiString is the first of keys in o that is a string, or "".
func geminiString(o map[string]json.RawMessage, keys ...string) string {
	raw, _ := field(o, keys...)
	s, _ := asString(raw)
	return s
}

func isImage(mime string) bool {
	return strings.HasPrefix(strings.ToLower(mime), "image/")
}
