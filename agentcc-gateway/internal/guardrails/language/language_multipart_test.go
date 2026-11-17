package language

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The same user text must receive the same language
// decision whether the chat request carries a string or standard text parts.
func TestLanguageMultipartDecisionParity(t *testing.T) {
	for _, tc := range []struct {
		name    string
		text    string
		allowed []interface{}
		pass    bool
	}{
		{"latin_allowed", "Hello, how are you today?", []interface{}{"latin"}, true},
		{"cyrillic_blocked", "Привет как дела сегодня друзья", []interface{}{"latin"}, false},
		{"han_blocked", "你好世界今天天气怎么样呢", []interface{}{"latin"}, false},
		{"cyrillic_allowed", "Привет как дела сегодня друзья", []interface{}{"latin", "cyrillic"}, true},
		{"below_threshold", "Hello with а б", []interface{}{"latin"}, true},
		{"at_threshold", "абвгд", []interface{}{"latin"}, true},
		{"above_threshold", "абвгде", []interface{}{"latin"}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := New(map[string]interface{}{"allowed_scripts": tc.allowed})
			baseline := g.Check(context.Background(), multipartLanguageInput(t, tc.text))
			if baseline.Pass != tc.pass {
				t.Fatalf("string decision Pass=%v, want %v: %+v", baseline.Pass, tc.pass, baseline)
			}

			var split []map[string]interface{}
			for _, r := range tc.text {
				split = append(split, map[string]interface{}{"type": "text", "text": string(r)})
			}
			for _, representation := range []struct {
				name    string
				content interface{}
			}{
				{"single_text_part", []map[string]interface{}{{"type": "text", "text": tc.text}}},
				{"split_text_parts", split},
				{"text_with_image", []map[string]interface{}{
					{"type": "image_url", "image_url": map[string]string{"url": "https://example.invalid/image.png"}},
					{"type": "text", "text": tc.text},
				}},
			} {
				t.Run(representation.name, func(t *testing.T) {
					got := g.Check(context.Background(), multipartLanguageInput(t, representation.content))
					if got.Pass != tc.pass || got.Score != baseline.Score || got.Message != baseline.Message || !reflect.DeepEqual(got.Details, baseline.Details) {
						t.Fatalf("multipart decision %+v differs from string decision %+v; want Pass=%v", got, baseline, tc.pass)
					}
				})
			}
		})
	}
}

func TestLanguageMultipartCombinesUserMessages(t *testing.T) {
	// Neither message alone exceeds the threshold; together they must block,
	// including when only one of the messages uses multipart content.
	for _, first := range []interface{}{"абв", []map[string]string{{"type": "text", "text": "абв"}}} {
		in := multipartLanguageInput(t, first)
		in.Request.Messages = append(in.Request.Messages, multipartLanguageInput(t, []map[string]string{{"type": "text", "text": "где"}}).Request.Messages[0])
		g := New(map[string]interface{}{"allowed_scripts": []interface{}{"latin"}})
		got := g.Check(context.Background(), in)
		if got.Pass || got.Score != 1 || !strings.Contains(got.Message, "cyrillic(6)") {
			t.Errorf("combined user messages must produce a Cyrillic block decision: %+v", got)
		}
	}
}

func TestLanguageMultipartIgnoresOtherRoles(t *testing.T) {
	for _, role := range []string{"system", "developer", "assistant", "tool"} {
		t.Run(role, func(t *testing.T) {
			in := multipartLanguageInput(t, []map[string]string{{"type": "text", "text": "Hello, how are you today?"}})
			other := multipartLanguageInput(t, []map[string]string{{"type": "text", "text": "Привет как дела сегодня друзья"}}).Request.Messages[0]
			other.Role = role
			in.Request.Messages = append(in.Request.Messages, other)
			g := New(map[string]interface{}{"allowed_scripts": []interface{}{"latin"}})
			if got := g.Check(context.Background(), in); !got.Pass || got.Score != 0 {
				t.Fatalf("non-user %s content must not affect the language decision: %+v", role, got)
			}
		})
	}
}

func TestLanguageMultipartIgnoresNonTextParts(t *testing.T) {
	for _, content := range []interface{}{
		nil,
		[]map[string]interface{}{},
		[]map[string]interface{}{{"type": "text", "text": ""}},
		[]map[string]interface{}{{"type": "image_url", "image_url": map[string]string{"url": "https://example.invalid/Привет"}}},
		[]map[string]interface{}{
			{"type": "text", "text": "Hello, how are you today?"},
			{"type": "image_url", "text": "Привет как дела сегодня друзья", "image_url": map[string]string{"url": "https://example.invalid/image.png"}},
		},
	} {
		g := New(map[string]interface{}{"allowed_scripts": []interface{}{"latin"}})
		if got := g.Check(context.Background(), multipartLanguageInput(t, content)); !got.Pass || got.Score != 0 {
			t.Errorf("non-text parts must not affect the language decision: %+v", got)
		}
	}
}

func multipartLanguageInput(t *testing.T, content interface{}) *guardrails.CheckInput {
	t.Helper()
	raw, err := json.Marshal(content)
	if err != nil {
		t.Fatal(err)
	}
	return &guardrails.CheckInput{Request: &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Messages: []models.Message{{Role: "user", Content: raw}},
	}}
}
