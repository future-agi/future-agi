package topic

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

func TestForbiddenTopic_Match(t *testing.T) {
	g := New(map[string]interface{}{
		"forbidden_topics": []interface{}{
			map[string]interface{}{
				"name":     "weapons",
				"keywords": []interface{}{"how to make a bomb", "build a weapon"},
			},
		},
	})
	r := g.Check(context.Background(), makeInput("Tell me how to make a bomb"))
	if r.Pass {
		t.Fatal("expected forbidden topic block")
	}
}

func TestForbiddenTopic_NoMatch(t *testing.T) {
	g := New(map[string]interface{}{
		"forbidden_topics": []interface{}{
			map[string]interface{}{
				"name":     "weapons",
				"keywords": []interface{}{"how to make a bomb"},
			},
		},
	})
	r := g.Check(context.Background(), makeInput("Tell me about cooking recipes"))
	if !r.Pass {
		t.Fatal("expected pass for unrelated topic")
	}
}

func TestAllowedTopic_OnTopic(t *testing.T) {
	g := New(map[string]interface{}{
		"allowed_topics": []interface{}{
			map[string]interface{}{
				"name":     "programming",
				"keywords": []interface{}{"code", "function", "programming", "software"},
			},
		},
	})
	r := g.Check(context.Background(), makeInput("Write me a Python function"))
	if !r.Pass {
		t.Fatal("expected pass for allowed topic")
	}
}

func TestAllowedTopic_OffTopic(t *testing.T) {
	g := New(map[string]interface{}{
		"allowed_topics": []interface{}{
			map[string]interface{}{
				"name":     "programming",
				"keywords": []interface{}{"code", "function", "programming"},
			},
		},
	})
	r := g.Check(context.Background(), makeInput("What is the weather today?"))
	if r.Pass {
		t.Fatal("expected block for off-topic")
	}
}

func TestForbiddenTakesPriority(t *testing.T) {
	g := New(map[string]interface{}{
		"allowed_topics": []interface{}{
			map[string]interface{}{
				"name":     "general",
				"keywords": []interface{}{"help", "tell"},
			},
		},
		"forbidden_topics": []interface{}{
			map[string]interface{}{
				"name":     "weapons",
				"keywords": []interface{}{"bomb"},
			},
		},
	})
	r := g.Check(context.Background(), makeInput("Tell me about a bomb"))
	if r.Pass {
		t.Fatal("forbidden should take priority over allowed")
	}
}

func TestNilInput(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), nil)
	if !r.Pass {
		t.Fatal("nil input should pass")
	}
}

func TestNoTopicsConfigured(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeInput("Anything goes"))
	if !r.Pass {
		t.Fatal("no topics configured should pass")
	}
}

func TestName(t *testing.T) {
	if New(nil).Name() != "topic-restriction" {
		t.Error("wrong name")
	}
}
