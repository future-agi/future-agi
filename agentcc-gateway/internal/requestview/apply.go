package requestview

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ApplyChecks writes into the body what the checks rewrote in the chat
// request: a remediation (pii-detection's mask, redact or hash) sets a
// message's content to its rewritten text, and the arguments of its tool
// calls to their rewritten arguments, which on /v1/chat/completions is what
// the provider gets. Here each rewritten message's text is shared back
// among the texts of the body it was read from (split), and each rewritten
// call's arguments go back to the place they were read from: as JSON into a
// JSON value (an Anthropic tool_use's input, a Gemini functionCall's args, a
// Responses shell call's action), or as a string into a string (a Responses
// function call's arguments). The body is then read again, and it is kept
// only when every message reads as the checks left it. Otherwise the body
// stays as it was and an error says why: the guardrail plugin refuses the
// request rather than send what a check rewrote. A message counts as
// rewritten when its content is not the one it was read with (a check
// replaces it, as pii-detection does), and a tool call when its arguments
// are not. It does nothing when the body was never read, or could not be.
// A body read only in part (a value nested more than MaxDepth deep) takes
// no rewrite: the plugin refuses such a request under a check that rewrites
// it before any check runs, and a rewrite of part of it is refused here.
func (v *View) ApplyChecks() error {
	c := v.chat
	if c == nil {
		return nil
	}
	msgs := c.req.Messages
	if len(msgs) != len(c.read) {
		return fmt.Errorf("a check changed the number of messages (%d, read %d)", len(msgs), len(c.read))
	}
	var edits []edit
	for i := range msgs {
		callEdits, err := c.callEdits(i, msgs[i].ToolCalls)
		if err != nil {
			return err
		}
		edits = append(edits, callEdits...)
		if bytes.Equal(msgs[i].Content, c.read[i]) {
			continue
		}
		rewritten, ok := contentText(msgs[i].Content)
		if !ok {
			return fmt.Errorf("a check rewrote message %d into content that is not text", i)
		}
		texts := c.texts[i]
		if len(texts) == 0 {
			return fmt.Errorf("a check wrote text into message %d, which holds no text of the request", i)
		}
		values := make([]string, len(texts))
		for j, t := range texts {
			values[j] = t.value
		}
		shares, keep, err := split(values, c.seps[i], rewritten)
		if err != nil {
			return fmt.Errorf("message %d: %w", i, err)
		}
		for j, t := range texts {
			switch {
			case !keep[j] && t.part == nil:
				return fmt.Errorf("message %d: the rewrite of %s cannot be written", i, t.at)
			case !keep[j]:
				edits = append(edits, edit{at: t.part, drop: true})
			case shares[j] != t.value:
				edits = append(edits, edit{at: t.at, value: shares[j], json: t.json})
			}
		}
	}
	if len(edits) == 0 {
		return nil
	}
	if v.reason != "" {
		return errors.New("a check rewrote a request the gateway cannot read in full: " + v.reason)
	}
	body, err := applyEdits(v.body, edits)
	if err != nil {
		return err
	}
	// The rewritten body is read in one pass too; it is no larger than what
	// the checks left.
	again, why := v.readBody(context.Background(), body)
	if again == nil || why != "" {
		return fmt.Errorf("the rewritten request cannot be read: %s", why)
	}
	if err := sameText(again.req.Messages, msgs); err != nil {
		return err
	}
	if err := sameCalls(again, msgs); err != nil {
		return err
	}
	v.body = body
	// The rewrite is applied: the body now holds it, as the checks' request
	// does, and a later call has nothing more to write.
	again.req = c.req
	for i := range msgs {
		again.read[i] = msgs[i].Content
		for k := range again.calls[i] {
			again.calls[i][k].read = msgs[i].ToolCalls[k].Function.Arguments
		}
	}
	v.chat = again
	return nil
}

// callEdits is the edits that write the arguments of message i's tool calls,
// calls, that a check rewrote. A check that changed how many tool calls the
// message makes, or rewrote arguments the body does not hold, is an error.
func (c *chat) callEdits(i int, calls []models.ToolCall) ([]edit, error) {
	sites := c.calls[i]
	if len(calls) != len(sites) {
		return nil, fmt.Errorf("a check changed the number of tool calls of message %d (%d, read %d)", i, len(calls), len(sites))
	}
	var edits []edit
	for k, site := range sites {
		args := calls[k].Function.Arguments
		if args == site.read {
			continue
		}
		if site.at == nil {
			return nil, fmt.Errorf("message %d: a check rewrote the arguments of tool call %d, which the request does not hold", i, k)
		}
		edits = append(edits, edit{at: site.at, value: args, json: !site.str})
	}
	return edits, nil
}

// sameCalls says whether again, the rewritten body read again, holds the
// tool calls' arguments as the checks left them in want: a string as it is,
// a JSON value as written (without the white space around it).
func sameCalls(again *chat, want []models.Message) error {
	for i := range want {
		got := again.req.Messages[i].ToolCalls
		if len(got) != len(want[i].ToolCalls) {
			return fmt.Errorf("message %d of the rewritten request does not make the tool calls the checks left", i)
		}
		for k, tc := range want[i].ToolCalls {
			w := tc.Function.Arguments
			if !again.calls[i][k].str {
				w = strings.TrimSpace(w)
			}
			if got[k].Function.Arguments != w {
				return fmt.Errorf("message %d of the rewritten request does not hold the arguments the checks left in tool call %d", i, k)
			}
		}
	}
	return nil
}

// sameText says whether got, the messages of the rewritten body, read as
// want, the checks' messages: the same roles, and the same text.
func sameText(got, want []models.Message) error {
	if len(got) != len(want) {
		return fmt.Errorf("the rewritten request reads as %d messages, not %d", len(got), len(want))
	}
	for i := range want {
		if got[i].Role == want[i].Role && bytes.Equal(got[i].Content, want[i].Content) {
			continue // the same JSON: the same text, read without decoding it
		}
		g, _ := contentText(got[i].Content)
		w, _ := contentText(want[i].Content)
		if got[i].Role != want[i].Role || g != w {
			return fmt.Errorf("message %d of the rewritten request does not read as the checks left it", i)
		}
	}
	return nil
}

// contentText is the text a check reads in a message's content, as
// pii-detection reads it: a string itself, or the text of the content's text
// parts, joined with a space.
func contentText(raw json.RawMessage) (string, bool) {
	if len(raw) == 0 {
		return "", true
	}
	if s, ok := asString(raw); ok {
		return s, true
	}
	var parts []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if json.Unmarshal(raw, &parts) != nil {
		return "", false
	}
	var texts []string
	for _, p := range parts {
		if p.Type == "text" && p.Text != "" {
			texts = append(texts, p.Text)
		}
	}
	return strings.Join(texts, " "), true
}

// split shares rewritten, a message's text after a check rewrote it, among
// the message's texts, values, which that text joined with sep. Texts the
// rewrite left as they were keep their values: those at the start and at the
// end are matched first. When one text in between changed, it takes what
// they leave. When several did, they share it at sep, if the rewrite kept
// every sep between them (a remediation writes none, but one that matched
// across texts removed one); otherwise the first of them takes the whole of
// it and the others are dropped (keep false).
func split(values []string, sep, rewritten string) (shares []string, keep []bool, err error) {
	n := len(values)
	shares, keep = make([]string, n), make([]bool, n)
	for i := range keep {
		keep[i] = true
	}
	start, pos := 0, 0
	for ; start < n; start++ {
		want := values[start]
		if start < n-1 {
			want += sep
		} else if pos+len(want) != len(rewritten) {
			break
		}
		if !strings.HasPrefix(rewritten[pos:], want) {
			break
		}
		shares[start] = values[start]
		pos += len(want)
	}
	if start == n {
		return shares, keep, nil
	}
	end, epos := n-1, len(rewritten)
	for ; end > start; end-- {
		want := sep + values[end]
		if epos-pos < len(want) || !strings.HasSuffix(rewritten[pos:epos], want) {
			break
		}
		shares[end] = values[end]
		epos -= len(want)
	}
	if epos < pos {
		return nil, nil, errors.New("the rewrite cannot be shared among the request's texts")
	}
	mid := rewritten[pos:epos]
	if start == end {
		shares[start] = mid
		return shares, keep, nil
	}
	seps := end - start
	for k := start; k <= end; k++ {
		seps += strings.Count(values[k], sep)
	}
	if sep != "" && strings.Count(mid, sep) == seps {
		pieces := strings.Split(mid, sep)
		for k := start; k <= end; k++ {
			lines := strings.Count(values[k], sep) + 1
			shares[k] = strings.Join(pieces[:lines], sep)
			pieces = pieces[lines:]
		}
		return shares, keep, nil
	}
	shares[start] = mid
	for k := start + 1; k <= end; k++ {
		keep[k] = false
	}
	return shares, keep, nil
}

// ─── Editing the body ────────────────────────────────────────────────────────

// edit is one change to a JSON document: the value at path at becomes value
// (a string, or, when json is set, the JSON text value), or, with drop, the
// array element at at is removed.
type edit struct {
	at    path
	value string
	json  bool
	drop  bool
}

// applyEdits is doc with edits made. Only the objects and arrays on the
// edits' paths are written again, in their own order; every other value
// keeps its bytes. doc is decoded in one pass (doc), and written once, into
// one buffer: each object and list on the paths is decoded once and each
// byte written once, however deep the edits are.
func applyEdits(doc []byte, edits []edit) ([]byte, error) {
	d := newDoc(context.Background(), doc)
	if !d.valid {
		return nil, errors.New("the request is not JSON")
	}
	// Room for the body and every rewrite (and its escapes), so the buffer
	// is allocated once.
	size := len(doc)
	for _, e := range edits {
		size += len(e.value) + len(e.value)/8 + 2
	}
	var buf bytes.Buffer
	buf.Grow(size)
	if err := d.applyAt(&buf, doc, edits, 0); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// applyAt writes to buf raw, the value at the path the edits' paths share
// their first depth elements of, with the edits made.
func (d *doc) applyAt(buf *bytes.Buffer, raw json.RawMessage, edits []edit, depth int) error {
	for _, e := range edits {
		if len(e.at) != depth {
			continue
		}
		if len(edits) != 1 || e.drop {
			return fmt.Errorf("conflicting rewrites of %s", e.at)
		}
		if !e.json {
			writeMarshalled(buf, e.value)
			return nil
		}
		value := []byte(strings.TrimSpace(e.value))
		if !json.Valid(value) || first(value) != first(raw) {
			return fmt.Errorf("the rewrite of %s is not JSON of its type", e.at)
		}
		buf.Write(value)
		return nil
	}
	switch first(raw) {
	case '{':
		keys, values, ok := d.members(raw)
		if !ok {
			return errors.New("an object of the request cannot be read")
		}
		last := lastOf(keys)
		index := make(map[string]int, len(keys))
		for i, k := range keys {
			if last(i) {
				index[k] = i
			}
		}
		at := make(map[int][]edit)
		for key, group := range groupByKey(edits, depth) {
			i, ok := index[key]
			if !ok {
				return fmt.Errorf("%s is not in the request", group[0].at[:depth+1])
			}
			at[i] = group
		}
		buf.WriteByte('{')
		for i, key := range keys {
			if i > 0 {
				buf.WriteByte(',')
			}
			buf.Write(marshal(key))
			buf.WriteByte(':')
			if group, ok := at[i]; ok {
				if err := d.applyAt(buf, values[i], group, depth+1); err != nil {
					return err
				}
				continue
			}
			buf.Write(values[i])
		}
		buf.WriteByte('}')
		return nil
	case '[':
		elems, ok := d.array(raw)
		if !ok {
			return errors.New("a list of the request cannot be read")
		}
		groups := groupByIndex(edits, depth)
		dropped := make(map[int]bool)
		for index, group := range groups {
			if index < 0 || index >= len(elems) {
				return fmt.Errorf("%s is not in the request", group[0].at[:depth+1])
			}
			if len(group) == 1 && group[0].drop && len(group[0].at) == depth+1 {
				dropped[index] = true
			}
		}
		buf.WriteByte('[')
		n := 0
		for i, elem := range elems {
			if dropped[i] {
				continue
			}
			if n > 0 {
				buf.WriteByte(',')
			}
			n++
			if group, ok := groups[i]; ok {
				if err := d.applyAt(buf, elem, group, depth+1); err != nil {
					return err
				}
				continue
			}
			buf.Write(elem)
		}
		buf.WriteByte(']')
		return nil
	}
	return fmt.Errorf("%s is not in the request", edits[0].at[:depth+1])
}

// writeMarshalled writes s to buf as marshal does (a JSON string, without
// HTML escaping), into buf's own room.
func writeMarshalled(buf *bytes.Buffer, s string) {
	buf.Write(appendJSONString(buf.AvailableBuffer(), s))
}

func groupByKey(edits []edit, depth int) map[string][]edit {
	groups := make(map[string][]edit)
	for _, e := range edits {
		key, _ := e.at[depth].(string)
		groups[key] = append(groups[key], e)
	}
	return groups
}

func groupByIndex(edits []edit, depth int) map[int][]edit {
	groups := make(map[int][]edit)
	for _, e := range edits {
		index, ok := e.at[depth].(int)
		if !ok {
			index = -1
		}
		groups[index] = append(groups[index], e)
	}
	return groups
}
