package language

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func makeInput(content string) *guardrails.CheckInput {
	raw, _ := json.Marshal(content)
	return &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model: "gpt-4o",
			Messages: []models.Message{
				{Role: "user", Content: raw},
			},
		},
		Metadata: map[string]string{},
	}
}

func TestLatinOnly_Pass(t *testing.T) {
	g := New(map[string]interface{}{
		"allowed_scripts": []interface{}{"latin"},
	})
	r := g.Check(context.Background(), makeInput("Hello, how are you today?"))
	if !r.Pass {
		t.Fatal("Latin text should pass when Latin is allowed")
	}
}

func TestCyrillic_Blocked(t *testing.T) {
	g := New(map[string]interface{}{
		"allowed_scripts": []interface{}{"latin"},
	})
	r := g.Check(context.Background(), makeInput("Привет как дела сегодня друзья"))
	if r.Pass {
		t.Fatal("Cyrillic should be blocked when only Latin allowed")
	}
}

func TestChinese_Blocked(t *testing.T) {
	g := New(map[string]interface{}{
		"allowed_scripts": []interface{}{"latin"},
	})
	r := g.Check(context.Background(), makeInput("你好世界今天天气怎么样呢"))
	if r.Pass {
		t.Fatal("Chinese should be blocked when only Latin allowed")
	}
}

func TestMultipleAllowedScripts(t *testing.T) {
	g := New(map[string]interface{}{
		"allowed_scripts": []interface{}{"latin", "cyrillic"},
	})
	r := g.Check(context.Background(), makeInput("Привет как дела сегодня друзья"))
	if !r.Pass {
		t.Fatal("Cyrillic should pass when Cyrillic is allowed")
	}
}

func TestNoAllowedScripts_PassAll(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeInput("任何文字都可以"))
	if !r.Pass {
		t.Fatal("no allowed scripts = pass all")
	}
}

func TestHomoglyphDetection(t *testing.T) {
	// Mix of Latin 'a' and Cyrillic 'а' (looks the same)
	if !DetectHomoglyphs("pаssword") { // 'а' is Cyrillic U+0430
		t.Error("should detect homoglyph attack")
	}
}

func TestHomoglyph_PureLatin(t *testing.T) {
	if DetectHomoglyphs("password") {
		t.Error("pure Latin should not trigger homoglyph")
	}
}

func TestNilInput(t *testing.T) {
	g := New(map[string]interface{}{"allowed_scripts": []interface{}{"latin"}})
	r := g.Check(context.Background(), nil)
	if !r.Pass {
		t.Fatal("nil input should pass")
	}
}

func TestName(t *testing.T) {
	if New(nil).Name() != "language-detection" {
		t.Error("wrong name")
	}
}

func TestSmallNonLatinCount_Pass(t *testing.T) {
	g := New(map[string]interface{}{
		"allowed_scripts": []interface{}{"latin"},
	})
	// Only 2 non-Latin chars (below threshold of 5).
	r := g.Check(context.Background(), makeInput("Hello with а б"))
	if !r.Pass {
		t.Fatal("small non-Latin count should pass")
	}
}
