package normalization

import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"strings"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

const attachmentRefFixture = `{"attachment_id":"12345678-1234-1234-1234-123456789abc","occurrence_id":"87654321-4321-4321-4321-cba987654321","direction":"mixed","modality":"audio","mime_type":"audio/wav"}`

func TestAttachmentSharedFixtures(t *testing.T) {
	raw, err := os.ReadFile("../../testdata/attachment_references.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixtures struct {
		References []struct {
			Name    string
			Version int64
			Raw     string
			Valid   bool
		}
		Identities []struct{ Input, Output string }
	}
	if err := json.Unmarshal(raw, &fixtures); err != nil {
		t.Fatal(err)
	}
	for _, c := range fixtures.References {
		t.Run(c.Name, func(t *testing.T) {
			refs, err := ParseAttachmentRefs(c.Version, c.Raw)
			if (err == nil) != c.Valid {
				t.Fatalf("valid=%v, err=%v", c.Valid, err)
			}
			attrs := pcommon.NewMap()
			attrs.PutInt(AttachmentSchema, c.Version)
			attrs.PutStr(AttachmentRefs, c.Raw)
			NormalizeAttachmentRefs(attrs)
			value, retained := attrs.Get(AttachmentRefs)
			if retained != c.Valid {
				t.Fatalf("projection retained=%v, want=%v", retained, c.Valid)
			}
			if c.Valid {
				normalized, err := ParseAttachmentRefs(1, value.Str())
				if err != nil || !reflect.DeepEqual(refs, normalized) {
					t.Fatal("normalization changed reference fields")
				}
			}
		})
	}
	for _, c := range fixtures.Identities {
		t.Run(c.Input, func(t *testing.T) {
			actual, err := CanonicalAttachmentTraceID(c.Input)
			if actual != c.Output || (err == nil) != (c.Output != "") {
				t.Fatalf("identity mismatch: %s %v", actual, err)
			}
		})
	}
}

func TestAttachmentReferencePrivacyAndBounds(t *testing.T) {
	for _, extra := range []string{`,"source_url":"canary-secret"`, `,"filename":"canary-secret"`, `,"object_key":"canary-secret"`, `,"hash":"canary-secret"`, `,"channel":true`, `,"channel":32`, `,"part_index":1.0`} {
		raw := "[" + strings.TrimSuffix(attachmentRefFixture, "}") + extra + "}]"
		if _, err := ParseAttachmentRefs(1, raw); err != ErrAttachmentReference {
			t.Fatal("expected content-free refusal")
		}
	}
	for _, count := range []int{64, 65} {
		refs := make([]string, count)
		for i := range refs {
			refs[i] = strings.Replace(attachmentRefFixture, "87654321-4321-4321-4321-cba987654321", fmt.Sprintf("00000000-0000-0000-0000-%012x", i+1), 1)
		}
		raw := "[" + strings.Join(refs, ",") + "]"
		if _, err := ParseAttachmentRefs(1, raw); (err == nil) != (count == 64) {
			t.Fatalf("count=%d: %v", count, err)
		}
	}
	if _, err := ParseAttachmentRefs(1, strings.Repeat(" ", 16385)); err == nil {
		t.Fatal("byte cap bypass")
	}
	if _, err := ParseAttachmentRefs(1, "["+strings.Replace(attachmentRefFixture, `"mixed"`, `"input|output"`, 1)+"]"); err == nil {
		t.Fatal("enum injection")
	}
}

func TestAttachmentNormalizeStripsCarriersAndForgedAuthority(t *testing.T) {
	attrs := pcommon.NewMap()
	attrs.PutInt(AttachmentSchema, 1)
	attrs.PutStr(AttachmentRefs, "["+attachmentRefFixture+"]")
	attrs.PutStr(AttachmentSession, "12345678-1234-1234-1234-123456789abc")
	attrs.PutStr(AttachmentNonce, "87654321-4321-4321-4321-cba987654321")
	attrs.PutStr("ordinary", "retained")
	Normalize(attrs, nil)
	if attrs.Len() != 3 {
		t.Fatal("carriers persisted")
	}
	before := attrs.AsRaw()
	Normalize(attrs, nil)
	if !reflect.DeepEqual(before, attrs.AsRaw()) {
		t.Fatal("normalization not idempotent")
	}
	attrs.PutBool(AttachmentPrefix+"verified", true)
	Normalize(attrs, nil)
	if attrs.Len() != 1 || String(attrs, "ordinary") != "retained" {
		t.Fatal("forged authority persisted")
	}
}
