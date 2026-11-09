package requestview

import (
	"encoding/json"
	"errors"
	"strings"
)

// readAnthropic reads a Messages API body into the chat shape, in the order
// the gateway's translator to other formats gives it: the system prompt as a
// system message, then each turn, its tool_result blocks first as tool
// messages and its other text, with its images, as one message. An
// assistant turn's tool_use blocks are its tool calls; the tools are read by
// name, and an MCP server the request connects (mcp_servers, or an
// mcp_toolset tool) as mcp:<its name>. A document's text, its title and
// context, and a search result's title and text are text of the turn that
// carries them. The result of a tool the provider runs (code execution, a
// text editor, web fetch and search) is a tool message with the text the
// model reads in it. A thinking block is the model's own reasoning and is
// not read; an image is a URL, and a file named by id is not read.
func readAnthropic(d *doc) (*chat, error) {
	top, ok := d.object(d.body)
	if !ok {
		return nil, errors.New("the body is not a JSON object")
	}
	r := &anthropicReader{b: newBuilder(d), d: d}
	req := r.b.c.req
	req.Model, _ = stringField(top, nil, "model")
	_ = json.Unmarshal(top["stream"], &req.Stream)
	_ = json.Unmarshal(top["max_tokens"], &req.MaxTokens)
	_ = json.Unmarshal(top["temperature"], &req.Temperature)
	_ = json.Unmarshal(top["top_p"], &req.TopP)

	if raw := top["system"]; !absent(raw) {
		at := path{"system"}
		m := &message{role: "system"}
		if s, ok := asString(raw); ok {
			m.addText(text{at: at, value: s})
		} else if blocks, ok := r.d.array(raw); ok {
			for i, blk := range blocks {
				if err := r.block(m, blk, at.to(i)); err != nil {
					return nil, err
				}
			}
		} else {
			return nil, readError(at, "is neither a string nor a list of text blocks")
		}
		r.b.add(m)
	}

	if raw := top["messages"]; !absent(raw) {
		turns, ok := r.d.array(raw)
		if !ok {
			return nil, readError(path{"messages"}, "is not a list")
		}
		for i, turn := range turns {
			if err := r.turn(turn, path{"messages", i}); err != nil {
				return nil, err
			}
		}
	}

	if raw := top["tools"]; !absent(raw) {
		tools, ok := r.d.array(raw)
		if !ok {
			return nil, readError(path{"tools"}, "is not a list")
		}
		for i, raw := range tools {
			at := path{"tools", i}
			o, ok := r.d.object(raw)
			if !ok {
				return nil, readError(at, "is not an object")
			}
			name, err := stringField(o, at, "name")
			if err != nil {
				return nil, err
			}
			if name == "" {
				// A tool with no name: an MCP server's tools (mcp_toolset),
				// by the server, or another kind, by its type.
				kind, _ := stringField(o, at, "type")
				if kind == "mcp_toolset" {
					server, _ := stringField(o, at, "mcp_server_name")
					name = mcpToolName(server)
				} else {
					name = kind
				}
			}
			description, err := stringField(o, at, "description")
			if err != nil {
				return nil, err
			}
			r.b.tool(name, description, o["input_schema"])
		}
	}

	// The MCP servers the request connects (Anthropic's MCP connector) are
	// tools it offers, each named mcp:<its name>, once: an mcp_toolset tool
	// already names its server.
	if raw := top["mcp_servers"]; !absent(raw) {
		servers, ok := r.d.array(raw)
		if !ok {
			return nil, readError(path{"mcp_servers"}, "is not a list")
		}
		for i, raw := range servers {
			at := path{"mcp_servers", i}
			o, ok := r.d.object(raw)
			if !ok {
				return nil, readError(at, "is not an object")
			}
			name, err := stringField(o, at, "name")
			if err != nil {
				return nil, err
			}
			if !r.b.hasTool(mcpToolName(name)) {
				r.b.tool(mcpToolName(name), "", nil)
			}
		}
	}
	return &r.b.c, nil
}

// mcpToolName is the name the checks read for an MCP server's tools:
// mcp:<the server's name>, or mcp when it has none.
func mcpToolName(server string) string {
	if server == "" {
		return "mcp"
	}
	return "mcp:" + server
}

type anthropicReader struct {
	b *builder
	d *doc
}

// turn reads one message of the body: its tool results, as tool messages,
// then the rest as one message.
func (r *anthropicReader) turn(raw json.RawMessage, at path) error {
	o, ok := r.d.object(raw)
	if !ok {
		return readError(at, "is not an object")
	}
	role, err := stringField(o, at, "role")
	if err != nil {
		return err
	}
	if role != "assistant" {
		role = "user"
	}
	m := &message{role: role}
	content, cat := o["content"], at.to("content")
	switch {
	case absent(content):
	case first(content) == '"':
		s, _ := asString(content)
		m.addText(text{at: cat, value: s})
	case first(content) == '[':
		blocks, ok := r.d.array(content)
		if !ok {
			return readError(cat, "is not a list of content blocks")
		}
		for j, blk := range blocks {
			if err := r.block(m, blk, cat.to(j)); err != nil {
				return err
			}
		}
	default:
		return readError(cat, "is neither a string nor a list of content blocks")
	}
	r.b.add(m)
	return nil
}

// block reads one content block into m, or, for a tool result, into a tool
// message of its own, added before m. A block is in blocks (a tool result's
// content, a search result's, a document's) up to MaxDepth deep: one inside
// MaxDepth others is not read, and is recorded (tooDeep) as in the turn
// that holds it.
func (r *anthropicReader) block(m *message, raw json.RawMessage, at path) error {
	if len(at) >= MaxDepth {
		r.d.tooDeep(outerBlock(at))
		return nil
	}
	o, ok := r.d.object(raw)
	if !ok {
		return readError(at, "is not an object")
	}
	typ, err := stringField(o, at, "type")
	if err != nil {
		return err
	}
	switch typ {
	case "text":
		s, err := stringField(o, at, "text")
		if err != nil {
			return err
		}
		m.addText(text{at: at.to("text"), value: s, part: at})
	case "image":
		m.addImage(anthropicImageURL(r.d, o["source"]))
	case "document":
		return r.document(m, o, at)
	case "search_result":
		title, err := stringField(o, at, "title")
		if err != nil {
			return err
		}
		m.addText(text{at: at.to("title"), value: title})
		return r.blocks(m, o["content"], at.to("content"))
	case "tool_use", "server_tool_use", "mcp_tool_use":
		id, _ := stringField(o, at, "id")
		name, _ := stringField(o, at, "name")
		args, argsAt := "{}", path(nil)
		if input := o["input"]; !absent(input) {
			args, argsAt = string(input), at.to("input")
		}
		m.addCall(id, name, args, argsAt, false)
	case "tool_result", "mcp_tool_result":
		id, _ := stringField(o, at, "tool_use_id")
		tm := &message{role: "tool", toolCallID: id}
		content, cat := o["content"], at.to("content")
		if s, ok := asString(content); ok {
			tm.addText(text{at: cat, value: s})
		} else if err := r.blocks(tm, content, cat); err != nil {
			return err
		}
		r.b.add(tm)
	case "web_fetch_tool_result":
		// A page the provider fetched for the model: its document, as the
		// result of the call.
		id, _ := stringField(o, at, "tool_use_id")
		tm := &message{role: "tool", toolCallID: id}
		if result, ok := r.d.object(o["content"]); ok {
			if page, ok := r.d.object(result["content"]); ok {
				if err := r.document(tm, page, at.to("content", "content")); err != nil {
					return err
				}
			}
		}
		r.b.add(tm)
	case "thinking", "redacted_thinking", "container_upload":
		// The model's own reasoning, and a file named by id: not read.
	default:
		if strings.HasSuffix(typ, "_tool_result") {
			// The result of a tool the provider runs (code execution, a
			// text editor, a web search, ...): the text the model reads in
			// it (pickText: a command's stdout and stderr, a file's text,
			// a result's title), as the result of the call.
			id, _ := stringField(o, at, "tool_use_id")
			tm := &message{role: "tool", toolCallID: id}
			r.d.pickText(tm, raw, at)
			r.b.add(tm)
			return nil
		}
		r.d.pickText(m, raw, at)
	}
	return nil
}

// blocks reads a list of content blocks (absent is none) into m.
func (r *anthropicReader) blocks(m *message, raw json.RawMessage, at path) error {
	if absent(raw) {
		return nil
	}
	if len(at) >= MaxDepth {
		r.d.tooDeep(outerBlock(at))
		return nil
	}
	blocks, ok := r.d.array(raw)
	if !ok {
		return readError(at, "is not a list of content blocks")
	}
	for i, blk := range blocks {
		if err := r.block(m, blk, at.to(i)); err != nil {
			return err
		}
	}
	return nil
}

// document reads a document block: its title and context, and its text when
// its source is plain text or a list of content blocks. A PDF, a URL or a
// file named by id is not read.
func (r *anthropicReader) document(m *message, o map[string]json.RawMessage, at path) error {
	for _, key := range []string{"title", "context"} {
		s, err := stringField(o, at, key)
		if err != nil {
			return err
		}
		m.addText(text{at: at.to(key), value: s})
	}
	source, ok := r.d.object(o["source"])
	if !ok {
		return nil
	}
	sat := at.to("source")
	kind, _ := stringField(source, sat, "type")
	switch kind {
	case "text":
		s, err := stringField(source, sat, "data")
		if err != nil {
			return err
		}
		m.addText(text{at: sat.to("data"), value: s})
	case "content":
		if s, ok := asString(source["content"]); ok {
			m.addText(text{at: sat.to("content"), value: s})
			return nil
		}
		return r.blocks(m, source["content"], sat.to("content"))
	}
	return nil
}

// outerBlock is the block of a turn, or of the system prompt, that holds
// the block at at: its path's first four elements (messages[i].content[j]).
func outerBlock(at path) path {
	if len(at) > 4 {
		return at[:4]
	}
	return at
}

// anthropicImageURL is an image source as a URL: a data URL for base64 data,
// the URL itself for a URL source, and "" for a file named by id.
func anthropicImageURL(d *doc, raw json.RawMessage) string {
	source, ok := d.object(raw)
	if !ok {
		return ""
	}
	kind, _ := asString(source["type"])
	switch kind {
	case "base64":
		media, _ := asString(source["media_type"])
		data, _ := asString(source["data"])
		if media == "" {
			media = "image/jpeg"
		}
		return "data:" + media + ";base64," + data
	case "url":
		url, _ := asString(source["url"])
		return url
	}
	return ""
}
