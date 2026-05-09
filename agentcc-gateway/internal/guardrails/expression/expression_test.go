package expression

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func makeInput(model string, messages []models.Message, metadata map[string]string) *guardrails.CheckInput {
	return &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model:    model,
			Messages: messages,
		},
		Metadata: metadata,
	}
}

func msg(role, content string) models.Message {
	raw, _ := json.Marshal(content)
	return models.Message{Role: role, Content: raw}
}

// --- Basic Expression Tests ---

func TestExpr_ModelEquals(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model == "gpt-4o"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, nil))
	if r.Pass {
		t.Fatal("expected trigger when model matches")
	}
}

func TestExpr_ModelNotEquals(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model == "gpt-4o"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-3.5-turbo", nil, nil))
	if !r.Pass {
		t.Fatal("should not trigger for different model")
	}
}

func TestExpr_MetadataEquals(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `metadata.environment == "production"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, map[string]string{"environment": "production"}))
	if r.Pass {
		t.Fatal("expected trigger")
	}
}

func TestExpr_MetadataMissing(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `metadata.environment == "production"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, nil))
	if !r.Pass {
		t.Fatal("should pass when metadata key missing")
	}
}

func TestExpr_Contains(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model contains "gpt"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, nil))
	if r.Pass {
		t.Fatal("expected trigger")
	}
}

func TestExpr_ContainsFalse(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model contains "claude"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, nil))
	if !r.Pass {
		t.Fatal("should pass")
	}
}

func TestExpr_GreaterThan(t *testing.T) {
	temp := 1.5
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model:       "gpt-4o",
			Temperature: &temp,
		},
	}
	g := New("test", map[string]interface{}{
		"expression": `request.temperature > 1.0`,
	})
	r := g.Check(context.Background(), input)
	if r.Pass {
		t.Fatal("expected trigger for temp > 1.0")
	}
}

func TestExpr_LenMessages(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `len(request.messages) > 5`,
	})
	msgs := make([]models.Message, 6)
	for i := range msgs {
		msgs[i] = msg("user", "hello")
	}
	r := g.Check(context.Background(), makeInput("gpt-4o", msgs, nil))
	if r.Pass {
		t.Fatal("expected trigger for 6 messages > 5")
	}
}

func TestExpr_LenMessagesBelowThreshold(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `len(request.messages) > 5`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", []models.Message{msg("user", "hi")}, nil))
	if !r.Pass {
		t.Fatal("should pass for 1 message")
	}
}

// --- Logical Operators ---

func TestExpr_And(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model contains "gpt" && metadata.team == "eng"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, map[string]string{"team": "eng"}))
	if r.Pass {
		t.Fatal("expected trigger for both conditions true")
	}
}

func TestExpr_AndPartialFalse(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model contains "gpt" && metadata.team == "research"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, map[string]string{"team": "eng"}))
	if !r.Pass {
		t.Fatal("should pass when second condition false")
	}
}

func TestExpr_Or(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model == "gpt-4o" || request.model == "gpt-4"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4", nil, nil))
	if r.Pass {
		t.Fatal("expected trigger for second OR branch")
	}
}

func TestExpr_Not(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `!(metadata.allowed == "true")`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, map[string]string{"allowed": "false"}))
	if r.Pass {
		t.Fatal("expected trigger when not-allowed")
	}
}

func TestExpr_NotTrue(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `!(metadata.allowed == "true")`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, map[string]string{"allowed": "true"}))
	if !r.Pass {
		t.Fatal("should pass when allowed is true")
	}
}

// --- In Operator ---

func TestExpr_In(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model in ["gpt-4o", "gpt-4", "gpt-3.5-turbo"]`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4", nil, nil))
	if r.Pass {
		t.Fatal("expected trigger for model in list")
	}
}

func TestExpr_InNotFound(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model in ["gpt-4o", "gpt-4"]`,
	})
	r := g.Check(context.Background(), makeInput("claude-3", nil, nil))
	if !r.Pass {
		t.Fatal("should pass for model not in list")
	}
}

// --- String Functions ---

func TestExpr_Lower(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `lower(request.model) == "gpt-4o"`,
	})
	r := g.Check(context.Background(), makeInput("GPT-4o", nil, nil))
	if r.Pass {
		t.Fatal("expected trigger after lowercasing")
	}
}

func TestExpr_Matches(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model matches "^gpt-"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, nil))
	if r.Pass {
		t.Fatal("expected trigger for regex match")
	}
}

func TestExpr_StartsWith(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model startsWith "gpt"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, nil))
	if r.Pass {
		t.Fatal("expected trigger")
	}
}

func TestExpr_EndsWith(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model endsWith "4o"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, nil))
	if r.Pass {
		t.Fatal("expected trigger")
	}
}

// --- Has Function ---

func TestExpr_Has(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `has(request.temperature)`,
	})
	temp := 0.7
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model:       "gpt-4o",
			Temperature: &temp,
		},
	}
	r := g.Check(context.Background(), input)
	if r.Pass {
		t.Fatal("expected trigger when temperature exists")
	}
}

func TestExpr_HasMissing(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `has(metadata.nonexistent)`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, nil))
	if !r.Pass {
		t.Fatal("should pass when field doesn't exist")
	}
}

// --- Array Access ---

func TestExpr_ArrayIndex(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.messages[0].role == "system"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", []models.Message{msg("system", "You are a helper")}, nil))
	if r.Pass {
		t.Fatal("expected trigger for system role")
	}
}

func TestExpr_ArrayIndexNotSystem(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.messages[0].role == "system"`,
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", []models.Message{msg("user", "hello")}, nil))
	if !r.Pass {
		t.Fatal("should pass for non-system role")
	}
}

// --- Custom Message ---

func TestExpr_CustomMessage(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model == "gpt-4o"`,
		"message":    "Custom block message",
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, nil))
	if r.Message != "Custom block message" {
		t.Errorf("message = %q", r.Message)
	}
}

// --- Edge Cases ---

func TestExpr_NilInput(t *testing.T) {
	g := New("test", map[string]interface{}{"expression": `request.model == "test"`})
	r := g.Check(context.Background(), nil)
	if !r.Pass {
		t.Fatal("nil input should pass")
	}
}

func TestExpr_EmptyExpression(t *testing.T) {
	g := New("test", map[string]interface{}{"expression": ""})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, nil))
	if !r.Pass {
		t.Fatal("empty expression should pass")
	}
}

func TestExpr_NilConfig(t *testing.T) {
	g := New("test", nil)
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, nil))
	if !r.Pass {
		t.Fatal("nil config should pass")
	}
}

func TestExpr_Name(t *testing.T) {
	g := New("my-rule", nil)
	if g.Name() != "my-rule" {
		t.Errorf("name = %q", g.Name())
	}
}

func TestIsExpressionConfig(t *testing.T) {
	if !IsExpressionConfig(map[string]interface{}{"expression": "request.model == 'test'"}) {
		t.Error("should detect expression config")
	}
	if IsExpressionConfig(map[string]interface{}{"url": "https://example.com"}) {
		t.Error("url config should return false")
	}
	if IsExpressionConfig(nil) {
		t.Error("nil should return false")
	}
}

// --- Complex Expressions ---

func TestExpr_Complex(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model contains "o1" && !(metadata.team == "research")`,
	})
	r := g.Check(context.Background(), makeInput("o1-preview", nil, map[string]string{"team": "engineering"}))
	if r.Pass {
		t.Fatal("expected trigger: o1 model and non-research team")
	}
}

func TestExpr_ComplexResearchAllowed(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": `request.model contains "o1" && !(metadata.team == "research")`,
	})
	r := g.Check(context.Background(), makeInput("o1-preview", nil, map[string]string{"team": "research"}))
	if !r.Pass {
		t.Fatal("should pass: research team allowed")
	}
}

func TestExpr_BooleanLiteral(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": "true",
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, nil))
	if r.Pass {
		t.Fatal("literal true should trigger")
	}
}

func TestExpr_BooleanLiteralFalse(t *testing.T) {
	g := New("test", map[string]interface{}{
		"expression": "false",
	})
	r := g.Check(context.Background(), makeInput("gpt-4o", nil, nil))
	if !r.Pass {
		t.Fatal("literal false should pass")
	}
}
