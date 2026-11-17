package meter

import (
	"bytes"
	"encoding/json"
	"strings"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// A record's content modes (plan sections 6.13 and 6.27): full keeps the
// call's arguments and result, metadata and none keep neither. Tool and
// server names are kept under every mode.
const (
	ModeFull     = "full"
	ModeMetadata = "metadata"
	ModeNone     = "none"
)

// MaxContentBytes caps each of a record's arguments and result.
const MaxContentBytes = 16 << 10

// retentionFull is the one terms retention that keeps content (V20's
// TermsRetention "full"); "" is no constraint.
const retentionFull = "full"

// ModeSources are the inputs of a record's content mode.
type ModeSources struct {
	// OrgLoaded is false when no org config could be loaded for the call:
	// the record then keeps no content, whatever the other sources say.
	OrgLoaded bool
	Org       string // the org's resolved content mode, OrgConfig.ContentMode()
	Key       string // the key's log_content
	Request   string // the x-agentcc-log-content header
	Default   string // the global privacy.default_content_mode
	// Retention is the call's terms retention (V20): "" and full keep the
	// mode, metadata_only, hashed and any other value tighten full to
	// metadata.
	Retention string
}

// ResolveMode is the record's content mode: the most restrictive of the
// org, key, request and global sources, through D7's resolver, then the
// stricter of that and the call's terms retention; none when no org config
// was loaded.
func ResolveMode(s ModeSources) string {
	if !s.OrgLoaded {
		return ModeNone
	}
	mode := string(privacy.ResolveContentMode(privacy.ContentModeSources{
		Org:     s.Org,
		Key:     s.Key,
		Request: s.Request,
		Default: s.Default,
	}).ContentMode)
	if mode == ModeFull && s.Retention != "" && s.Retention != retentionFull {
		return ModeMetadata
	}
	return mode
}

// Content is raw as a record keeps it under mode: nothing unless mode is
// full; then every string in it redacted by r (when r redacts) and the
// whole capped at MaxContentBytes. A capped value is replaced by
// {"truncated": true, "original_bytes": n, "prefix": "<the start of it>"},
// so it is still JSON.
func Content(raw json.RawMessage, mode string, r *privacy.Redactor) json.RawMessage {
	raw = bytes.TrimSpace(raw)
	if mode != ModeFull || len(raw) == 0 {
		return nil
	}
	out := raw
	if r != nil && r.ShouldRedact() {
		out = redactJSON(raw, r)
	}
	if len(out) <= MaxContentBytes {
		return append(json.RawMessage(nil), out...)
	}
	return truncated(out)
}

// redactJSON redacts every string value in raw; a value that is not JSON
// is redacted as one string.
func redactJSON(raw json.RawMessage, r *privacy.Redactor) json.RawMessage {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		v = string(raw)
	}
	out, err := marshalPlain(redactValue(v, r))
	if err != nil {
		return nil
	}
	return out
}

func redactValue(v any, r *privacy.Redactor) any {
	switch t := v.(type) {
	case string:
		return r.Redact(t)
	case []any:
		for i := range t {
			t[i] = redactValue(t[i], r)
		}
		return t
	case map[string]any:
		for k, e := range t {
			t[k] = redactValue(e, r)
		}
		return t
	default:
		return v
	}
}

// truncatedContent is what a record keeps of content over the cap.
type truncatedContent struct {
	Truncated     bool   `json:"truncated"`
	OriginalBytes int    `json:"original_bytes"`
	Prefix        string `json:"prefix"`
}

// truncated keeps as much of out's start as fits in MaxContentBytes once
// encoded, cut on a UTF-8 boundary.
func truncated(out []byte) json.RawMessage {
	n := MaxContentBytes
	for n > 0 {
		enc, err := marshalPlain(truncatedContent{Truncated: true, OriginalBytes: len(out), Prefix: utf8Prefix(out, n)})
		if err != nil {
			return nil
		}
		if len(enc) <= MaxContentBytes {
			return enc
		}
		n -= len(enc) - MaxContentBytes
	}
	enc, _ := marshalPlain(truncatedContent{Truncated: true, OriginalBytes: len(out)})
	return enc
}

// utf8Prefix is at most n bytes of b's start, never splitting a rune.
func utf8Prefix(b []byte, n int) string {
	if n >= len(b) {
		return strings.ToValidUTF8(string(b), "")
	}
	for n > 0 && !utf8.RuneStart(b[n]) {
		n--
	}
	return strings.ToValidUTF8(string(b[:n]), "")
}

// marshalPlain is json.Marshal without HTML escaping and without the
// encoder's trailing newline.
func marshalPlain(v any) (json.RawMessage, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return bytes.TrimRight(buf.Bytes(), "\n"), nil
}
