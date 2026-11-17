package toolview

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/requestview"
)

func TestNativeResponsesImageNullableLocator(t *testing.T) {
	ctx := context.Background()
	for _, tc := range []struct {
		name, fields string
		hasURL       bool
	}{
		{"omitted-file", `,"file_id":"file-local","detail":"auto"`, false},
		{"null-file", `,"image_url":null,"file_id":"file-local","detail":"auto"`, false},
		{"string-file", `,"image_url":"drop","file_id":"file-local","detail":"auto"`, true},
		{"string-url-only", `,"image_url":"drop"`, true},
	} {
		body := " \n" + `{"model":"gpt-4o","input":[{"role":"user","content":[{"type":"input_image"` + tc.fields + `}]}],"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}],"opaque":{"name":"drop","n":1e+03,"s":"drop"}}` + "\n "
		t.Run("reader/"+tc.name, func(t *testing.T) {
			reader := requestview.Responses([]byte(body))
			chat, why := reader.ChatRequest(ctx)
			if why != "" || chat == nil || len(chat.Tools) != 2 || string(reader.Body()) != body {
				t.Fatal("inherited reader refused image locator or changed original bytes")
			}
			if !tc.hasURL {
				// A missing/null URL is absent; the reader does not resolve file IDs.
				if len(chat.Messages) != 0 {
					t.Fatal("inherited reader exposed an absent image URL or resolved file content")
				}
				return
			}
			if len(chat.Messages) != 1 || chat.Messages[0].Role != "user" {
				t.Fatal("inherited reader lost the string image URL")
			}
			var parts []struct {
				Type     string `json:"type"`
				ImageURL struct {
					URL string `json:"url"`
				} `json:"image_url"`
			}
			if err := json.Unmarshal(chat.Messages[0].Content, &parts); err != nil || len(parts) != 1 || parts[0].Type != "image_url" || parts[0].ImageURL.URL != "drop" {
				t.Fatal("inherited reader changed image URL content")
			}
		})
		t.Run("strip/"+tc.name, func(t *testing.T) {
			r := Responses([]byte(body))
			inherited, why := requestview.Responses([]byte(body)).ChatRequest(ctx)
			before, nativeWhy := r.ChatRequest(ctx)
			if why != "" || nativeWhy != "" || inherited == nil || !reflect.DeepEqual(before, inherited) {
				t.Fatal("native wrapper disagrees with inherited reader before strip")
			}
			generation := r.Generation()
			old := compatKeep(t, r, "keep")
			want := strings.Replace(body, `,{"type":"function","name":"drop"}`, "", 1)
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
			expected, why := requestview.Responses([]byte(want)).ChatRequest(ctx)
			got, nativeWhy := r.ChatRequest(ctx)
			if why != "" || nativeWhy != "" || expected == nil || !reflect.DeepEqual(got, expected) || !reflect.DeepEqual(got.Messages, before.Messages) {
				t.Fatal("strip changed the inherited image view")
			}
			compatKeep(t, r, "keep")
			if string(r.Body()) != want || r.Generation() != generation+1 {
				t.Fatal("reapplying the effective filter was not idempotent")
			}
			if r.ApplyToolFilter(ctx, old, nil) == nil || string(r.Body()) != want || r.Generation() != generation+1 {
				t.Fatal("stale snapshot accepted or mutated the stripped request")
			}
			foreign := Responses([]byte(body))
			foreignGeneration := foreign.Generation()
			if foreign.ApplyToolFilter(ctx, old, nil) == nil || string(foreign.Body()) != body || foreign.Generation() != foreignGeneration {
				t.Fatal("foreign snapshot accepted or mutated another request")
			}
		})
	}
}

func TestNativeResponsesImageLocatorClosed(t *testing.T) {
	for _, tc := range []struct{ name, fields string }{
		{"url-number", `,"image_url":7,"file_id":"file-local"`},
		{"url-bool", `,"image_url":true,"file_id":"file-local"`},
		{"url-array", `,"image_url":["drop"],"file_id":"file-local"`},
		{"url-object", `,"image_url":{"url":"drop"},"file_id":"file-local"`},
		{"no-locator", ""},
		{"null-url-no-file", `,"image_url":null`},
		{"null-file-no-url", `,"file_id":null`},
		{"all-null", `,"image_url":null,"file_id":null`},
		{"file-null", `,"image_url":"drop","file_id":null`},
		{"file-number", `,"image_url":"drop","file_id":7`},
		{"file-bool", `,"image_url":"drop","file_id":true`},
		{"file-array", `,"image_url":"drop","file_id":["file-local"]`},
		{"file-object", `,"image_url":"drop","file_id":{"id":"file-local"}`},
		{"detail-null", `,"image_url":"drop","file_id":"file-local","detail":null`},
		{"detail-number", `,"image_url":"drop","file_id":"file-local","detail":7`},
		{"detail-bool", `,"image_url":"drop","file_id":"file-local","detail":true`},
		{"detail-array", `,"image_url":"drop","file_id":"file-local","detail":["auto"]`},
		{"detail-object", `,"image_url":"drop","file_id":"file-local","detail":{"value":"auto"}`},
		{"image-reference", `,"image_url":"drop","file_id":"file-local","tool_call_id":null`},
		{"nullable-url-reference", `,"image_url":null,"file_id":"file-local","tool_use_id":null`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := `{"model":"gpt-4o","input":[{"role":"user","content":[{"type":"input_image"` + tc.fields + `}]}],"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]}`
			r := Responses([]byte(body))
			// Repeated refusals must leave both the body and generation unchanged.
			compatReference(t, r, true)
			compatReference(t, r, true)
		})
	}
}
