package restmcp

import (
	"bytes"
	"encoding/json"
	"fmt"
	"mime"
	"net/http"
	"strings"
	"unicode/utf8"
)

// MetaVendor is the key of a tools/call result's _meta that attributes the
// answer: the vendor, the host the runtime called and its HTTP status.
const MetaVendor = "com.futureagi/vendor"

// maxErrorText bounds the vendor message an error result quotes.
const maxErrorText = 2000

// toolResult is an MCP CallToolResult.
type toolResult struct {
	Content    []textContent              `json:"content"`
	Structured json.RawMessage            `json:"structuredContent,omitempty"`
	IsError    bool                       `json:"isError"`
	Meta       map[string]json.RawMessage `json:"_meta,omitempty"`
}

type textContent struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

// attribution is a result's _meta[MetaVendor].
type attribution struct {
	Name   string `json:"name"`
	Host   string `json:"host"`
	Status int    `json:"status,omitempty"`
}

func errorResult(text string) *toolResult {
	return &toolResult{Content: []textContent{{Type: "text", Text: text}}, IsError: true}
}

// vendorResult turns the vendor's answer into the tool's result: a 2xx is
// shaped (unwrap, then fields, then the maxBytes cap); anything else, and a
// 2xx that says ok: false (Slack's Web API), is an isError result naming the
// vendor, the host and the status. Every credential value is scrubbed from
// the answer before it is read, so no cut keeps part of one, and again from
// the result.
func (a *App) vendorResult(t *Tool, status int, header http.Header, body []byte, cred *credential) *toolResult {
	body = cred.scrubBytes(body)
	who := attribution{Name: a.VendorName, Host: t.host, Status: status}
	meta := map[string]json.RawMessage{}
	meta[MetaVendor], _ = json.Marshal(who)
	var res *toolResult
	switch {
	case status >= 300 && status < 400:
		res = errorResult(fmt.Sprintf("%s (%s) answered HTTP %d, a redirect: the runtime follows no redirect, so the credential never leaves %s",
			a.VendorName, t.host, status, t.host))
	case status >= 400:
		res = errorResult(vendorErrorText(a.VendorName, t.host, status, header, body))
	default:
		if msg, failed := okFalse(body); failed {
			res = errorResult(fmt.Sprintf("%s (%s) answered HTTP %d with ok: false: %s", a.VendorName, t.host, status, msg))
		} else {
			res = t.shapeSuccess(a.VendorName, status, header, body)
		}
	}
	for i := range res.Content {
		res.Content[i].Text = cred.scrub(res.Content[i].Text)
	}
	if len(res.Structured) > 0 {
		res.Structured = json.RawMessage(cred.scrub(string(res.Structured)))
	}
	res.Meta = meta
	return res
}

func (t *Tool) shapeSuccess(vendor string, status int, header http.Header, body []byte) *toolResult {
	if len(bytes.TrimSpace(body)) == 0 {
		return &toolResult{Content: []textContent{{Type: "text",
			Text: fmt.Sprintf("Done: %s answered HTTP %d with no content.", vendor, status)}}}
	}
	if !isJSON(header.Get("Content-Type"), body) {
		if !utf8.Valid(body) {
			return &toolResult{Content: []textContent{{Type: "text", Text: fmt.Sprintf(
				"%s answered HTTP %d with %d bytes of %s, which is not text; the runtime returns text and JSON only.",
				vendor, status, len(body), header.Get("Content-Type"))}}}
		}
		return &toolResult{Content: []textContent{{Type: "text", Text: capText(string(body), t.maxBytes)}}}
	}
	v := decodeAny(body)
	if t.unwrap != "" {
		if inner, ok := lookup(v, t.unwrap); ok {
			v = inner
		}
	}
	if len(t.fields) > 0 {
		v = project(v, fieldTree(t.fields))
	}
	text := encodeJSON(v)
	if len(text) <= t.maxBytes {
		res := &toolResult{Content: []textContent{{Type: "text", Text: text}}}
		if _, isObject := v.(map[string]any); isObject && t.structured {
			res.Structured = json.RawMessage(text)
		}
		return res
	}
	if fitted, notice, ok := fitList(v, len(text), t.maxBytes); ok {
		return &toolResult{Content: []textContent{{Type: "text", Text: fitted}, {Type: "text", Text: notice}}}
	}
	return &toolResult{Content: []textContent{{Type: "text", Text: capText(text, t.maxBytes)}}}
}

func isJSON(contentType string, body []byte) bool {
	mt, _, err := mime.ParseMediaType(contentType)
	jsonType := err == nil && (mt == "application/json" || strings.HasSuffix(mt, "+json"))
	return (jsonType || contentType == "") && json.Valid(body)
}

// lookup follows a dotted path through objects.
func lookup(v any, path string) (any, bool) {
	for _, key := range strings.Split(path, ".") {
		obj, ok := v.(map[string]any)
		if !ok {
			return nil, false
		}
		if v, ok = obj[key]; !ok {
			return nil, false
		}
	}
	return v, true
}

// fieldTree turns dotted paths into a tree; a nil subtree keeps the whole
// value at that key.
func fieldTree(paths []string) map[string]any {
	root := map[string]any{}
	for _, p := range paths {
		node := root
		keys := strings.Split(p, ".")
		for i, k := range keys {
			if i == len(keys)-1 {
				node[k] = nil
				break
			}
			next, ok := node[k].(map[string]any)
			if !ok {
				if _, leaf := node[k]; leaf {
					break // a shorter path already keeps the whole value
				}
				next = map[string]any{}
				node[k] = next
			}
			node = next
		}
	}
	return root
}

// project keeps only the tree's members of v, through arrays at any depth.
func project(v any, tree map[string]any) any {
	switch t := v.(type) {
	case []any:
		out := make([]any, len(t))
		for i, el := range t {
			out[i] = project(el, tree)
		}
		return out
	case map[string]any:
		out := map[string]any{}
		for k, sub := range tree {
			val, ok := t[k]
			if !ok {
				continue
			}
			if subtree, ok := sub.(map[string]any); ok {
				out[k] = project(val, subtree)
			} else {
				out[k] = val
			}
		}
		return out
	}
	return v
}

func encodeJSON(v any) string {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	return strings.TrimSuffix(buf.String(), "\n")
}

// capText cuts text to at most max bytes, the notice included, at a UTF-8
// boundary.
func capText(text string, max int) string {
	if len(text) <= max {
		return text
	}
	notice := fmt.Sprintf("\n[truncated: the result is %d bytes; this tool returns at most %d (response.maxBytes). Ask for less, for example fewer results per page or fewer fields.]", len(text), max)
	keep := max - len(notice)
	if keep < 0 {
		keep = 0
	}
	for keep > 0 && !utf8.RuneStart(text[keep]) {
		keep--
	}
	out := text[:keep] + notice
	if len(out) > max {
		out = out[:max]
		for len(out) > 0 && !utf8.ValidString(out) {
			out = out[:len(out)-1]
		}
	}
	return out
}

// okFalse reports a JSON object answer with "ok": false and its error
// (Slack's Web API answers HTTP 200 that way).
func okFalse(body []byte) (string, bool) {
	var o struct {
		OK    *bool  `json:"ok"`
		Error string `json:"error"`
	}
	if json.Unmarshal(body, &o) != nil || o.OK == nil || *o.OK || o.Error == "" {
		return "", false
	}
	return truncate(o.Error, maxErrorText), true
}

// vendorErrorText names the vendor, the host, the status and the vendor's
// own message, from the common error shapes (Google and Microsoft Graph's
// error.{code,message}, error as a string, message, error_description,
// RFC 9457 title and detail, errors[0].message), else the start of the
// body.
func vendorErrorText(vendor, host string, status int, header http.Header, body []byte) string {
	text := fmt.Sprintf("%s API error from %s: HTTP %d %s", vendor, host, status, http.StatusText(status))
	if msg := errorMessage(body); msg != "" {
		text += ": " + msg
	} else if snippet := bytes.TrimSpace(body); len(snippet) > 0 && utf8.Valid(snippet) {
		text += ": " + truncate(string(snippet), 500)
	}
	if ra := header.Get("Retry-After"); ra != "" && (status == http.StatusTooManyRequests || status == http.StatusServiceUnavailable) {
		text += " (Retry-After: " + truncate(ra, 64) + ")"
	}
	return text
}

func errorMessage(body []byte) string {
	var o map[string]json.RawMessage
	if json.Unmarshal(body, &o) != nil {
		return ""
	}
	str := func(raw json.RawMessage) string {
		var s string
		_ = json.Unmarshal(raw, &s)
		return s
	}
	if raw, ok := o["error"]; ok {
		var inner map[string]json.RawMessage
		if json.Unmarshal(raw, &inner) == nil {
			code, msg := str(inner["code"]), str(inner["message"])
			if code == "" {
				code = str(inner["status"])
			}
			switch {
			case code != "" && msg != "":
				return truncate(code+": "+msg, maxErrorText)
			case msg != "":
				return truncate(msg, maxErrorText)
			}
		} else if s := str(raw); s != "" {
			if d := str(o["error_description"]); d != "" {
				s += ": " + d
			}
			return truncate(s, maxErrorText)
		}
	}
	if s := str(o["message"]); s != "" {
		return truncate(s, maxErrorText)
	}
	if title, detail := str(o["title"]), str(o["detail"]); title != "" || detail != "" {
		// RFC 9457 problem details (Adyen, among others), with the vendor's
		// own errorCode when it adds one.
		var parts []string
		for _, p := range []string{str(o["errorCode"]), title, detail} {
			if p != "" {
				parts = append(parts, p)
			}
		}
		return truncate(strings.Join(parts, ": "), maxErrorText)
	}
	var list []struct {
		Message string `json:"message"`
	}
	if json.Unmarshal(o["errors"], &list) == nil && len(list) > 0 && list[0].Message != "" {
		return truncate(list[0].Message, maxErrorText)
	}
	return ""
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	for n > 0 && !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n] + "..."
}
