package toolview

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/requestview"
)

func nullableDocumentFields(title, context string) string {
	fields := ""
	if title != "" {
		fields += `,"title":` + title
	}
	if context != "" {
		fields += `,"context":` + context
	}
	return fields
}

func nullableDocumentBody(block string) string {
	return " \n" + `{"model":"document-model","max_tokens":64,"messages":[{"role":"user","content":[` + block + `]}],"tools":[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}],"opaque":{"name":"drop","n":1e+03,"s":"drop"}}` + "\n "
}

func nullableTextDocument(title, context string) string {
	return `{"type":"document"` + nullableDocumentFields(title, context) + `,"source":{"type":"text","media_type":"text/plain","data":"hello"}}`
}

var nullableDocumentValues = []struct{ name, raw, text string }{
	{"omitted", "", ""},
	{"null", "null", ""},
	{"string", `"drop"`, "drop"},
}

func TestNativeDocumentMetadataInheritedReader(t *testing.T) {
	for _, title := range nullableDocumentValues {
		for _, contextValue := range nullableDocumentValues {
			t.Run("title-"+title.name+"/context-"+contextValue.name, func(t *testing.T) {
				body := nullableDocumentBody(nullableTextDocument(title.raw, contextValue.raw))
				reader := requestview.Anthropic([]byte(body))
				chat, why := reader.ChatRequest(context.Background())
				if why != "" || chat == nil || len(chat.Messages) != 1 || len(chat.Tools) != 2 {
					t.Fatal("inherited reader did not accept optional document metadata")
				}
				var parts []string
				for _, text := range []string{title.text, contextValue.text, "hello"} {
					if text != "" {
						parts = append(parts, text)
					}
				}
				var text string
				if err := json.Unmarshal(chat.Messages[0].Content, &text); err != nil || text != strings.Join(parts, "\n") {
					t.Fatal("inherited reader changed document text or null-as-absence semantics")
				}
				if string(reader.Body()) != body {
					t.Fatal("inherited read changed original bytes")
				}
			})
		}
	}
}

func TestNativeStripDocumentNullableMetadata(t *testing.T) {
	ctx := context.Background()
	for _, title := range nullableDocumentValues {
		for _, contextValue := range nullableDocumentValues {
			t.Run("title-"+title.name+"/context-"+contextValue.name, func(t *testing.T) {
				body := nullableDocumentBody(nullableTextDocument(title.raw, contextValue.raw))
				r := Anthropic([]byte(body))
				inherited, why := requestview.Anthropic([]byte(body)).ChatRequest(ctx)
				before, nativeWhy := r.ChatRequest(ctx)
				if why != "" || nativeWhy != "" || inherited == nil || !reflect.DeepEqual(before, inherited) {
					t.Fatal("native wrapper disagrees with inherited reader before strip")
				}
				generation := r.Generation()
				old := compatKeep(t, r, "keep")
				want := strings.Replace(body, `,{"name":"drop","input_schema":{}}`, "", 1)
				if old.Len() != 2 || string(r.Body()) != want || r.Generation() != generation+1 {
					t.Fatal("strip did not remove only the denied declaration in one generation")
				}
				after, err := r.ToolSnapshot(ctx)
				if err != nil || after.Len() != 1 {
					t.Fatal("strip lost retained declaration")
				}
				d, ok := after.Tool(0)
				if !ok || d.Kind() != Function || d.PolicyName() != "keep" {
					t.Fatal("strip changed retained function identity")
				}
				expected, why := requestview.Anthropic([]byte(want)).ChatRequest(ctx)
				got, nativeWhy := r.ChatRequest(ctx)
				if why != "" || nativeWhy != "" || expected == nil || !reflect.DeepEqual(got, expected) || !reflect.DeepEqual(got.Messages, before.Messages) {
					t.Fatal("strip changed the inherited document view")
				}
				compatKeep(t, r, "keep")
				if string(r.Body()) != want || r.Generation() != generation+1 {
					t.Fatal("reapplying the effective filter was not idempotent")
				}
				if r.ApplyToolFilter(ctx, old, nil) == nil || string(r.Body()) != want || r.Generation() != generation+1 {
					t.Fatal("stale snapshot accepted or mutated the stripped request")
				}
				foreign := Anthropic([]byte(body))
				foreignGeneration := foreign.Generation()
				if foreign.ApplyToolFilter(ctx, old, nil) == nil || string(foreign.Body()) != body || foreign.Generation() != foreignGeneration {
					t.Fatal("foreign snapshot accepted or mutated another request")
				}
			})
		}
	}
}

func TestNativeStripDocumentMetadataInvalidValues(t *testing.T) {
	for _, field := range []string{"title", "context"} {
		for _, value := range []struct{ name, raw string }{
			{"number", "7"}, {"bool", "true"}, {"array", `["drop"]`}, {"object", `{"text":"drop"}`},
		} {
			t.Run(field+"/"+value.name, func(t *testing.T) {
				title, contextValue := `"title"`, `"context"`
				if field == "title" {
					title = value.raw
				} else {
					contextValue = value.raw
				}
				body := nullableDocumentBody(nullableTextDocument(title, contextValue))
				if chat, why := requestview.Anthropic([]byte(body)).ChatRequest(context.Background()); why == "" || chat != nil {
					t.Fatal("invalid metadata was accepted by inherited reader")
				}
				r := Anthropic([]byte(body))
				// Repeated refusals must leave both the body and generation unchanged.
				compatReference(t, r, true)
				compatReference(t, r, true)
			})
		}
	}
}

func TestNativeStripDocumentRequiredFieldsStayClosed(t *testing.T) {
	for _, tc := range []struct{ name, source, extra string }{
		{"source-null", `null`, ""},
		{"source-type-null", `{"type":null,"data":"hello"}`, ""},
		{"text-data-null", `{"type":"text","media_type":"text/plain","data":null}`, ""},
		{"base64-media-null", `{"type":"base64","media_type":null,"data":"aGVsbG8="}`, ""},
		{"url-null", `{"type":"url","url":null}`, ""},
		{"file-id-null", `{"type":"file","file_id":null}`, ""},
		{"content-null", `{"type":"content","content":null}`, ""},
		{"document-reference-null", `{"type":"text","media_type":"text/plain","data":"hello"}`, `,"tool_use_id":null`},
		{"source-reference-null", `{"type":"text","media_type":"text/plain","data":"hello","tool_call_id":null}`, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// String metadata isolates the structural refusal on the pre-fix head too.
			block := `{"type":"document","title":"title","context":"context","source":` + tc.source + tc.extra + `}`
			compatReference(t, Anthropic([]byte(nullableDocumentBody(block))), true)
		})
	}
}
