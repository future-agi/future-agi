package requestview

import (
	"encoding/json"
	"errors"
	"sort"
)

// Responses is a Responses API body.
func Responses(body []byte) *View { return &View{body: body, read: readResponses} }

// readResponses reads a Responses API body into the chat shape: the
// instructions as a system message, a stored prompt's variables as a user
// message, then the input: a string as a user message, or each item in
// order. A message item is a message of its role, its input_text,
// output_text and refusal parts its text and an input_image its image; a
// function, custom tool, shell, apply_patch, code interpreter or MCP call is
// the assistant's tool call (its arguments, input or code read as the string
// they are, or as JSON when they are another JSON value), and its output a
// tool message (a shell
// command's stdout and stderr, a code interpreter's logs, read by their
// fields). A reasoning item is the model's own and is not read. What is stored at the provider and named by id is not read: an
// item_reference, a file, an image by file id, the turns a
// previous_response_id or a conversation names, and a stored prompt's own
// text. The text of an item or part of a type the reader does not know is
// read as the user's.
func readResponses(d *doc) (*chat, error) {
	top, ok := d.object(d.body)
	if !ok {
		return nil, errors.New("the body is not a JSON object")
	}
	b := newBuilder(d)
	req := b.c.req
	req.Model, _ = stringField(top, nil, "model")
	req.User, _ = stringField(top, nil, "user")
	_ = json.Unmarshal(top["stream"], &req.Stream)
	_ = json.Unmarshal(top["max_output_tokens"], &req.MaxTokens)
	_ = json.Unmarshal(top["temperature"], &req.Temperature)
	_ = json.Unmarshal(top["top_p"], &req.TopP)

	if raw := top["instructions"]; !absent(raw) {
		s, ok := asString(raw)
		if !ok {
			return nil, readError(path{"instructions"}, "is not a string")
		}
		m := &message{role: "system"}
		m.addText(text{at: path{"instructions"}, value: s})
		b.add(m)
	}

	if raw := top["prompt"]; !absent(raw) {
		if err := responsesPrompt(b, raw); err != nil {
			return nil, err
		}
	}

	if raw := top["input"]; !absent(raw) {
		at := path{"input"}
		switch first(raw) {
		case '"':
			s, _ := asString(raw)
			m := &message{role: "user"}
			m.addText(text{at: at, value: s})
			b.add(m)
		case '[':
			items, ok := d.array(raw)
			if !ok {
				return nil, readError(at, "is not a list of items")
			}
			for i, item := range items {
				if err := responsesItem(b, item, at.to(i)); err != nil {
					return nil, err
				}
			}
		default:
			return nil, readError(at, "is neither a string nor a list of items")
		}
	}

	if raw := top["tools"]; !absent(raw) {
		tools, ok := d.array(raw)
		if !ok {
			return nil, readError(path{"tools"}, "is not a list")
		}
		for i, raw := range tools {
			at := path{"tools", i}
			o, ok := d.object(raw)
			if !ok {
				return nil, readError(at, "is not an object")
			}
			kind, _ := stringField(o, at, "type")
			switch kind {
			case "function", "custom":
				name, err := stringField(o, at, "name")
				if err != nil {
					return nil, err
				}
				description, _ := stringField(o, at, "description")
				b.tool(name, description, o["parameters"])
			case "mcp":
				// An MCP server's tools, by the server: mcp:<its label>,
				// as on /v1/messages.
				label, _ := stringField(o, at, "server_label")
				b.tool(mcpToolName(label), "", nil)
			default:
				// A tool the provider runs (web search, file search, ...),
				// by its type.
				b.tool(kind, "", nil)
			}
		}
	}
	return &b.c, nil
}

// responsesPrompt reads a stored prompt's variables, which the provider puts
// into the prompt's text: each a string or an input part.
func responsesPrompt(b *builder, raw json.RawMessage) error {
	at := path{"prompt"}
	o, ok := b.d.object(raw)
	if !ok {
		return readError(at, "is not an object")
	}
	vars := o["variables"]
	if absent(vars) {
		return nil
	}
	vat := at.to("variables")
	vo, ok := b.d.object(vars)
	if !ok {
		return readError(vat, "is not an object")
	}
	names := make([]string, 0, len(vo))
	for name := range vo {
		names = append(names, name)
	}
	sort.Strings(names)
	m := &message{role: "user"}
	for _, name := range names {
		v, at := vo[name], vat.to(name)
		if s, ok := asString(v); ok {
			m.addText(text{at: at, value: s})
			continue
		}
		if err := responsesPart(b.d, m, v, at); err != nil {
			return err
		}
	}
	b.add(m)
	return nil
}

// responsesItem reads one input item.
func responsesItem(b *builder, raw json.RawMessage, at path) error {
	d := b.d
	o, ok := d.object(raw)
	if !ok {
		return readError(at, "is not an object")
	}
	kind, err := stringField(o, at, "type")
	if err != nil {
		return err
	}
	_, hasRole := o["role"]
	switch {
	case kind == "message" || (kind == "" && hasRole):
		role, err := stringField(o, at, "role")
		if err != nil {
			return err
		}
		if role == "" {
			role = "user"
		}
		m := &message{role: role}
		if err := responsesContent(d, m, o["content"], at.to("content")); err != nil {
			return err
		}
		b.add(m)
	case kind == "function_call" || kind == "custom_tool_call" || kind == "mcp_call" || kind == "local_shell_call" ||
		kind == "shell_call" || kind == "apply_patch_call" || kind == "code_interpreter_call":
		m := &message{role: "assistant"}
		id, _ := stringField(o, at, "call_id")
		if id == "" {
			id, _ = stringField(o, at, "id")
		}
		name, _ := stringField(o, at, "name")
		// Where the call's arguments are: a string field, or a JSON value.
		key, str := "arguments", true
		switch kind {
		case "custom_tool_call":
			key = "input"
		case "local_shell_call":
			name, key, str = "local_shell", "action", false
		case "shell_call":
			name, key, str = "shell", "action", false
		case "apply_patch_call":
			name, key, str = "apply_patch", "operation", false
		case "code_interpreter_call":
			name, key = "code_interpreter", "code"
		}
		// A value the API defines as a string that is another JSON value
		// (an object, a list, a number) is read as JSON, and a rewrite of
		// it is written back as JSON, as for a shell call's action.
		if raw := o[key]; str && !absent(raw) && first(raw) != '"' {
			str = false
		}
		var args string
		var argsAt path
		if str {
			args, _ = stringField(o, at, key)
		} else if raw := o[key]; !absent(raw) {
			args = string(raw)
		}
		if raw, ok := o[key]; ok && !absent(raw) && (!str || first(raw) == '"') {
			argsAt = at.to(key)
		}
		m.addCall(id, name, args, argsAt, str)
		b.add(m)
		switch kind {
		case "mcp_call":
			// The MCP server's output is read as the call's result.
			tm := &message{role: "tool", toolCallID: id}
			out, err := stringField(o, at, "output")
			if err != nil {
				return err
			}
			tm.addText(text{at: at.to("output"), value: out})
			b.add(tm)
		case "code_interpreter_call":
			// Its outputs are read as the call's result: the logs (an image
			// output is not text).
			tm := &message{role: "tool", toolCallID: id}
			d.pickText(tm, o["outputs"], at.to("outputs"))
			b.add(tm)
		}
	case kind == "function_call_output" || kind == "custom_tool_call_output" || kind == "local_shell_call_output" ||
		kind == "shell_call_output" || kind == "apply_patch_call_output":
		// A string, or a list of parts; a shell call's output is a list of
		// a command's stdout and stderr, read by their fields (pickText).
		id, _ := stringField(o, at, "call_id")
		if id == "" {
			id, _ = stringField(o, at, "id")
		}
		tm := &message{role: "tool", toolCallID: id}
		if err := responsesContent(d, tm, o["output"], at.to("output")); err != nil {
			return err
		}
		b.add(tm)
	case kind == "reasoning" || kind == "item_reference":
		// The model's own reasoning, and an item stored at the provider.
	default:
		m := &message{role: "user"}
		d.pickText(m, raw, at)
		b.add(m)
	}
	return nil
}

// responsesContent reads a message's content, or a tool call's output: a
// string, or a list of content parts.
func responsesContent(d *doc, m *message, raw json.RawMessage, at path) error {
	switch {
	case absent(raw):
		return nil
	case first(raw) == '"':
		s, _ := asString(raw)
		m.addText(text{at: at, value: s})
		return nil
	case first(raw) == '[':
		parts, ok := d.array(raw)
		if !ok {
			return readError(at, "is not a list of content parts")
		}
		for j, part := range parts {
			if err := responsesPart(d, m, part, at.to(j)); err != nil {
				return err
			}
		}
		return nil
	}
	return readError(at, "is neither a string nor a list of content parts")
}

// responsesPart reads one content part (or a prompt variable that is one).
func responsesPart(d *doc, m *message, raw json.RawMessage, at path) error {
	o, ok := d.object(raw)
	if !ok {
		return readError(at, "is not an object")
	}
	kind, err := stringField(o, at, "type")
	if err != nil {
		return err
	}
	switch kind {
	case "input_text", "output_text", "text", "summary_text":
		s, err := stringField(o, at, "text")
		if err != nil {
			return err
		}
		m.addText(text{at: at.to("text"), value: s, part: at})
	case "refusal":
		s, err := stringField(o, at, "refusal")
		if err != nil {
			return err
		}
		m.addText(text{at: at.to("refusal"), value: s, part: at})
	case "input_image":
		url, _ := stringField(o, at, "image_url")
		m.addImage(url)
	case "input_file", "input_audio":
		// A file or audio, by id or inline: not read.
	default:
		d.pickText(m, raw, at)
	}
	return nil
}
