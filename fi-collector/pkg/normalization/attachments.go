package normalization

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"regexp"
	"strings"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

const AttachmentPrefix = "fi.attachment."
const AttachmentSchema = AttachmentPrefix + "schema_version"
const AttachmentRefs = AttachmentPrefix + "refs"
const AttachmentSession = AttachmentPrefix + "capture_session_id"
const AttachmentNonce = AttachmentPrefix + "correlation_nonce"
const MaxAttachmentBytes = 16384
const MaxAttachmentRefs = 64

var ErrAttachmentReference = errors.New("invalid_attachment_reference")
var attachmentUUID = regexp.MustCompile(`^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$`)
var attachmentTrace = regexp.MustCompile(`^[0-9a-f]{32}$`)
var attachmentSpan = regexp.MustCompile(`^[0-9a-f]{16}$`)
var attachmentToken = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,128}$`)
var attachmentMIMEs = map[string]string{
	"audio":    "audio/wav|audio/ogg|audio/mpeg|audio/mp4|audio/webm|audio/flac|audio/L16",
	"image":    "image/png|image/jpeg|image/webp",
	"video":    "video/mp4|video/webm",
	"document": "application/pdf",
	"file":     "application/octet-stream",
}

func ValidAttachmentUUID(value string) bool {
	return attachmentUUID.MatchString(value) && value != "00000000-0000-0000-0000-000000000000"
}

// CanonicalAttachmentTraceID preserves the exact bytes, including variant bits.
func CanonicalAttachmentTraceID(value string) (string, error) {
	if attachmentTrace.MatchString(value) && value != strings.Repeat("0", 32) {
		return value[:8] + "-" + value[8:12] + "-" + value[12:16] + "-" + value[16:20] + "-" + value[20:], nil
	}
	if ValidAttachmentUUID(value) {
		return value, nil
	}
	return "", ErrAttachmentReference
}

func ValidAttachmentSpanID(value string) bool {
	return attachmentSpan.MatchString(value) && value != strings.Repeat("0", 16)
}

// AttachmentProjection contains no storage identity, URL, filename or payload.
// It is not authority. Carriers must be checked against the authenticated writer.
type AttachmentProjection struct {
	Refs             []map[string]any
	CaptureSessionID string
	CorrelationNonce string
}

func referenceEnum(value any, choices string) bool {
	s, ok := value.(string)
	if !ok {
		return false
	}
	for _, choice := range strings.Split(choices, "|") {
		if s == choice {
			return true
		}
	}
	return false
}

func validAttachmentRef(ref map[string]any) bool {
	for _, key := range []string{"attachment_id", "occurrence_id", "direction", "modality", "mime_type"} {
		if _, ok := ref[key]; !ok {
			return false
		}
	}
	for key, value := range ref {
		switch key {
		case "attachment_id", "occurrence_id":
			s, ok := value.(string)
			if !ok || !ValidAttachmentUUID(s) {
				return false
			}
		case "direction":
			if !referenceEnum(value, "input|output|mixed") {
				return false
			}
		case "modality":
			if !referenceEnum(value, "audio|image|video|document|file") {
				return false
			}
		case "mime_type":
			// Checked against the validated modality below, not as an independent enum.
		case "conversation_id", "turn_id", "tool_call_id":
			s, ok := value.(string)
			if !ok || !attachmentToken.MatchString(s) {
				return false
			}
		case "purpose":
			if !referenceEnum(value, "conversation_recording|turn_recording|model_input|model_output|tool_result|computer_screenshot|diagnostic|document") {
				return false
			}
		case "role":
			if !referenceEnum(value, "user|assistant|tool|system|unknown") {
				return false
			}
		case "message_index", "part_index", "channel":
			number, ok := value.(json.Number)
			if !ok {
				return false
			}
			n, err := number.Int64()
			maximum := int64(65535)
			if key == "channel" {
				maximum = 31
			}
			if err != nil || n < 0 || n > maximum {
				return false
			}
		default:
			return false
		}
	}
	return referenceEnum(ref["mime_type"], attachmentMIMEs[ref["modality"].(string)])
}

// Decode the shallow closed shape token-by-token to reject duplicate members,
// nested values and oversized arrays before allocating an unbounded tree.
func ParseAttachmentRefs(version int64, raw string) ([]map[string]any, error) {
	if version != 1 || len(raw) > MaxAttachmentBytes {
		return nil, ErrAttachmentReference
	}
	d := json.NewDecoder(strings.NewReader(raw))
	d.UseNumber()
	token, err := d.Token()
	if err != nil || token != json.Delim('[') {
		return nil, ErrAttachmentReference
	}
	refs := make([]map[string]any, 0)
	seen := make(map[string]bool)
	for d.More() {
		if len(refs) == MaxAttachmentRefs {
			return nil, ErrAttachmentReference
		}
		token, err = d.Token()
		if err != nil || token != json.Delim('{') {
			return nil, ErrAttachmentReference
		}
		ref := make(map[string]any)
		for d.More() {
			token, err = d.Token()
			if err != nil {
				return nil, ErrAttachmentReference
			}
			key, ok := token.(string)
			if !ok {
				return nil, ErrAttachmentReference
			}
			if _, exists := ref[key]; exists {
				return nil, ErrAttachmentReference
			}
			value, err := d.Token()
			if err != nil {
				return nil, ErrAttachmentReference
			}
			if _, nested := value.(json.Delim); nested {
				return nil, ErrAttachmentReference
			}
			ref[key] = value
		}
		if token, err = d.Token(); err != nil || token != json.Delim('}') || !validAttachmentRef(ref) {
			return nil, ErrAttachmentReference
		}
		occurrence := ref["occurrence_id"].(string)
		if seen[occurrence] {
			return nil, ErrAttachmentReference
		}
		seen[occurrence] = true
		refs = append(refs, ref)
	}
	if token, err = d.Token(); err != nil || token != json.Delim(']') {
		return nil, ErrAttachmentReference
	}
	if _, err = d.Token(); err != io.EOF {
		return nil, ErrAttachmentReference
	}
	return refs, nil
}

func ExtractAttachmentProjection(attrs pcommon.Map) (*AttachmentProjection, error) {
	present, unknown := false, false
	attrs.Range(func(key string, _ pcommon.Value) bool {
		if strings.HasPrefix(key, AttachmentPrefix) {
			present = true
			if key != AttachmentSchema && key != AttachmentRefs && key != AttachmentSession && key != AttachmentNonce {
				unknown = true
			}
		}
		return true
	})
	if !present {
		return nil, nil
	}
	if unknown {
		return nil, ErrAttachmentReference
	}
	version, ok := attrs.Get(AttachmentSchema)
	if !ok || version.Type() != pcommon.ValueTypeInt {
		return nil, ErrAttachmentReference
	}
	raw, ok := attrs.Get(AttachmentRefs)
	if !ok || raw.Type() != pcommon.ValueTypeStr {
		return nil, ErrAttachmentReference
	}
	refs, err := ParseAttachmentRefs(version.Int(), raw.Str())
	if err != nil {
		return nil, err
	}
	projection := &AttachmentProjection{Refs: refs}
	session, hasSession := attrs.Get(AttachmentSession)
	nonce, hasNonce := attrs.Get(AttachmentNonce)
	if hasSession || hasNonce {
		if !hasSession || !hasNonce || session.Type() != pcommon.ValueTypeStr || nonce.Type() != pcommon.ValueTypeStr || !ValidAttachmentUUID(session.Str()) || !ValidAttachmentUUID(nonce.Str()) {
			return nil, ErrAttachmentReference
		}
		projection.CaptureSessionID, projection.CorrelationNonce = session.Str(), nonce.Str()
	}
	return projection, nil
}

// NormalizeAttachmentRefs removes forged authority fields and invalid projections.
// No media fetch or upload belongs in the collector. References alone never bind.
func NormalizeAttachmentRefs(attrs pcommon.Map) {
	projection, err := ExtractAttachmentProjection(attrs)
	var keys []string
	attrs.Range(func(key string, _ pcommon.Value) bool {
		if strings.HasPrefix(key, AttachmentPrefix) {
			keys = append(keys, key)
		}
		return true
	})
	for _, key := range keys {
		attrs.Remove(key)
	}
	if err != nil || projection == nil {
		return
	}
	// Disable HTML escaping to match Python's canonical, ASCII-only projection.
	var b bytes.Buffer
	encoder := json.NewEncoder(&b)
	encoder.SetEscapeHTML(false)
	if encoder.Encode(projection.Refs) != nil {
		return
	}
	attrs.PutInt(AttachmentSchema, 1)
	attrs.PutStr(AttachmentRefs, strings.TrimSuffix(b.String(), "\n"))
}
