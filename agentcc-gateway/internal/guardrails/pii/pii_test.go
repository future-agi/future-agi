package pii

import (
	"context"
	"encoding/json"
	"strings"
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

func makeResponseInput(content string) *guardrails.CheckInput {
	raw, _ := json.Marshal(content)
	return &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "gpt-4o"},
		Response: &models.ChatCompletionResponse{
			Choices: []models.Choice{
				{Message: models.Message{Role: "assistant", Content: raw}},
			},
		},
		Metadata: map[string]string{},
	}
}

// --- Entity Detection Tests ---

func TestDetect_Email(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeInput("Contact me at alice@example.com please"))
	if r.Pass {
		t.Fatal("expected PII detection for email")
	}
	assertEntityFound(t, r, "email")
}

func TestDetect_Email_Negative(t *testing.T) {
	g := New(map[string]interface{}{"entities": []interface{}{"email"}})
	r := g.Check(context.Background(), makeInput("No email here, just text"))
	if !r.Pass {
		t.Fatal("expected no PII detection")
	}
}

func TestDetect_Phone(t *testing.T) {
	tests := []string{
		"Call me at (555) 123-4567",
		"Phone: +1-555-123-4567",
		"Reach me at 555.123.4567",
	}
	g := New(map[string]interface{}{"entities": []interface{}{"phone"}})
	for _, text := range tests {
		r := g.Check(context.Background(), makeInput(text))
		if r.Pass {
			t.Errorf("expected phone detection for %q", text)
		}
	}
}

func TestDetect_SSN(t *testing.T) {
	g := New(map[string]interface{}{"entities": []interface{}{"ssn"}})
	r := g.Check(context.Background(), makeInput("My SSN is 123-45-6789"))
	if r.Pass {
		t.Fatal("expected SSN detection")
	}
	assertEntityFound(t, r, "ssn")
}

func TestDetect_SSN_Invalid(t *testing.T) {
	g := New(map[string]interface{}{"entities": []interface{}{"ssn"}})
	// SSNs starting with 000 or 666 are invalid.
	r := g.Check(context.Background(), makeInput("Invalid SSN: 000-12-3456"))
	if !r.Pass {
		t.Fatal("expected no SSN detection for 000-xx-xxxx")
	}
}

func TestDetect_CreditCard(t *testing.T) {
	tests := []string{
		"Visa: 4111-1111-1111-1111",
		"MC: 5100 1234 5678 9012",
		"Amex: 3782 822463 10005",
	}
	g := New(map[string]interface{}{"entities": []interface{}{"credit_card"}})
	for _, text := range tests {
		r := g.Check(context.Background(), makeInput(text))
		if r.Pass {
			t.Errorf("expected credit card detection for %q", text)
		}
	}
}

func TestDetect_IPv4(t *testing.T) {
	g := New(map[string]interface{}{"entities": []interface{}{"ipv4"}})
	r := g.Check(context.Background(), makeInput("Server at 192.168.1.100"))
	if r.Pass {
		t.Fatal("expected IPv4 detection")
	}
	assertEntityFound(t, r, "ipv4")
}

func TestDetect_IPv6(t *testing.T) {
	g := New(map[string]interface{}{"entities": []interface{}{"ipv6"}})
	r := g.Check(context.Background(), makeInput("IPv6: 2001:0db8:85a3:0000:0000:8a2e:0370:7334"))
	if r.Pass {
		t.Fatal("expected IPv6 detection")
	}
}

func TestDetect_AWSKey(t *testing.T) {
	g := New(map[string]interface{}{"entities": []interface{}{"aws_key"}})
	r := g.Check(context.Background(), makeInput("Key: AKIAIOSFODNN7EXAMPLE"))
	if r.Pass {
		t.Fatal("expected AWS key detection")
	}
	assertEntityFound(t, r, "aws_key")
}

func TestDetect_APIKey(t *testing.T) {
	g := New(map[string]interface{}{"entities": []interface{}{"api_key"}})
	r := g.Check(context.Background(), makeInput("Use sk-abcdefghijklmnopqrstuvwxyz for auth"))
	if r.Pass {
		t.Fatal("expected API key detection")
	}
}

func TestDetect_URLCredentials(t *testing.T) {
	g := New(map[string]interface{}{"entities": []interface{}{"url_credentials"}})
	r := g.Check(context.Background(), makeInput("Connect to https://admin:password123@db.example.com"))
	if r.Pass {
		t.Fatal("expected URL credentials detection")
	}
}

func TestDetect_MACAddress(t *testing.T) {
	g := New(map[string]interface{}{"entities": []interface{}{"mac_address"}})
	r := g.Check(context.Background(), makeInput("Device MAC: 00:1A:2B:3C:4D:5E"))
	if r.Pass {
		t.Fatal("expected MAC address detection")
	}
}

func TestDetect_IBAN(t *testing.T) {
	g := New(map[string]interface{}{"entities": []interface{}{"iban"}})
	r := g.Check(context.Background(), makeInput("Transfer to GB29NWBK60161331926819"))
	if r.Pass {
		t.Fatal("expected IBAN detection")
	}
}

func TestDetect_MRN(t *testing.T) {
	g := New(map[string]interface{}{"entities": []interface{}{"mrn"}})
	r := g.Check(context.Background(), makeInput("Patient MRN-12345678"))
	if r.Pass {
		t.Fatal("expected MRN detection")
	}
}

// --- Remediation Tests ---

func TestRemediation_Mask(t *testing.T) {
	g := New(map[string]interface{}{
		"remediation": "mask",
		"entities":    []interface{}{"email"},
	})
	input := makeInput("Email: alice@example.com")
	r := g.Check(context.Background(), input)
	if r.Pass {
		t.Fatal("expected PII detection")
	}

	// Check message was mutated.
	var content string
	json.Unmarshal(input.Request.Messages[0].Content, &content)
	if !strings.Contains(content, "***") {
		t.Errorf("expected masked content, got %q", content)
	}
	if strings.Contains(content, "alice@example.com") {
		t.Errorf("original email should be replaced")
	}
}

func TestRemediation_Redact(t *testing.T) {
	g := New(map[string]interface{}{
		"remediation": "redact",
		"entities":    []interface{}{"ssn"},
	})
	input := makeInput("SSN: 123-45-6789")
	g.Check(context.Background(), input)

	var content string
	json.Unmarshal(input.Request.Messages[0].Content, &content)
	if !strings.Contains(content, "[REDACTED:ssn]") {
		t.Errorf("expected redacted content, got %q", content)
	}
}

func TestRemediation_Hash(t *testing.T) {
	g := New(map[string]interface{}{
		"remediation": "hash",
		"entities":    []interface{}{"email"},
	})
	input := makeInput("Email: alice@example.com")
	g.Check(context.Background(), input)

	var content string
	json.Unmarshal(input.Request.Messages[0].Content, &content)
	if !strings.Contains(content, "[SHA:") {
		t.Errorf("expected hashed content, got %q", content)
	}
}

func TestRemediation_Block_NoMutation(t *testing.T) {
	g := New(map[string]interface{}{
		"remediation": "block",
		"entities":    []interface{}{"email"},
	})
	input := makeInput("Email: alice@example.com")
	r := g.Check(context.Background(), input)
	if r.Pass {
		t.Fatal("expected PII detection")
	}

	// Message should NOT be mutated in block mode.
	var content string
	json.Unmarshal(input.Request.Messages[0].Content, &content)
	if !strings.Contains(content, "alice@example.com") {
		t.Error("block mode should not mutate content")
	}
}

// --- Score Calculation Tests ---

func TestScore_SingleDetection(t *testing.T) {
	g := New(map[string]interface{}{
		"entities":     []interface{}{"email"},
		"max_entities": 1,
	})
	r := g.Check(context.Background(), makeInput("alice@example.com"))
	if r.Score != 1.0 {
		t.Errorf("expected score 1.0, got %f", r.Score)
	}
}

func TestScore_MultipleDetections(t *testing.T) {
	g := New(map[string]interface{}{
		"entities":     []interface{}{"email"},
		"max_entities": 5,
	})
	r := g.Check(context.Background(), makeInput("Emails: a@b.com and c@d.com"))
	expected := 2.0 / 5.0
	if r.Score != expected {
		t.Errorf("expected score %f, got %f", expected, r.Score)
	}
}

func TestScore_CappedAtOne(t *testing.T) {
	g := New(map[string]interface{}{
		"entities":     []interface{}{"email"},
		"max_entities": 1,
	})
	r := g.Check(context.Background(), makeInput("Emails: a@b.com and c@d.com and e@f.com"))
	if r.Score != 1.0 {
		t.Errorf("expected score capped at 1.0, got %f", r.Score)
	}
}

// --- Entity Filtering Tests ---

func TestEntityFiltering_OnlySpecified(t *testing.T) {
	g := New(map[string]interface{}{
		"entities": []interface{}{"email"},
	})
	// SSN should NOT be detected when only email is enabled.
	r := g.Check(context.Background(), makeInput("My SSN is 123-45-6789"))
	if !r.Pass {
		t.Fatal("SSN should not be detected when only email is enabled")
	}
}

func TestEntityFiltering_AllWhenEmpty(t *testing.T) {
	g := New(nil) // nil config = all entities enabled
	r := g.Check(context.Background(), makeInput("Email: alice@example.com"))
	if r.Pass {
		t.Fatal("expected detection with all entities enabled")
	}
}

// --- Content Format Tests ---

func TestMultimodalContent_ArrayForm(t *testing.T) {
	parts := []map[string]interface{}{
		{"type": "text", "text": "My email is alice@example.com"},
		{"type": "image_url", "image_url": map[string]string{"url": "https://img.example.com/pic.png"}},
	}
	raw, _ := json.Marshal(parts)
	g := New(map[string]interface{}{"entities": []interface{}{"email"}})
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model: "gpt-4o",
			Messages: []models.Message{
				{Role: "user", Content: raw},
			},
		},
		Metadata: map[string]string{},
	}
	r := g.Check(context.Background(), input)
	if r.Pass {
		t.Fatal("expected email detection in array content")
	}
}

func TestMultimodalContent_ImageOnly(t *testing.T) {
	parts := []map[string]interface{}{
		{"type": "image_url", "image_url": map[string]string{"url": "https://img.example.com/pic.png"}},
	}
	raw, _ := json.Marshal(parts)
	g := New(map[string]interface{}{"entities": []interface{}{"email"}})
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model:    "gpt-4o",
			Messages: []models.Message{{Role: "user", Content: raw}},
		},
		Metadata: map[string]string{},
	}
	r := g.Check(context.Background(), input)
	if !r.Pass {
		t.Fatal("no text content should mean pass")
	}
}

// --- Edge Cases ---

func TestNilInput(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), nil)
	if !r.Pass {
		t.Fatal("nil input should pass")
	}
}

func TestNilRequest(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), &guardrails.CheckInput{})
	if !r.Pass {
		t.Fatal("nil request should pass")
	}
}

func TestEmptyMessages(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "gpt-4o"},
	})
	if !r.Pass {
		t.Fatal("empty messages should pass")
	}
}

func TestEmptyContent(t *testing.T) {
	g := New(map[string]interface{}{"entities": []interface{}{"email"}})
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model:    "gpt-4o",
			Messages: []models.Message{{Role: "user"}},
		},
		Metadata: map[string]string{},
	}
	r := g.Check(context.Background(), input)
	if !r.Pass {
		t.Fatal("empty content should pass")
	}
}

func TestCleanText_NoPII(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeInput("Hello, how are you today?"))
	if !r.Pass {
		t.Fatal("clean text should pass")
	}
	if r.Score != 0 {
		t.Errorf("expected score 0, got %f", r.Score)
	}
}

// --- Post-Stage (Response) Tests ---

func TestPostStage_ResponseDetection(t *testing.T) {
	g := New(map[string]interface{}{"entities": []interface{}{"email"}})
	input := makeResponseInput("Contact support at help@company.com")
	r := g.Check(context.Background(), input)
	if r.Pass {
		t.Fatal("expected email detection in response")
	}
	assertEntityFound(t, r, "email")
}

func TestPostStage_ResponseRemediation(t *testing.T) {
	g := New(map[string]interface{}{
		"remediation": "redact",
		"entities":    []interface{}{"email"},
	})
	input := makeResponseInput("Email: admin@corp.com")
	g.Check(context.Background(), input)

	var content string
	json.Unmarshal(input.Response.Choices[0].Message.Content, &content)
	if !strings.Contains(content, "[REDACTED:email]") {
		t.Errorf("expected redacted response content, got %q", content)
	}
}

// --- Multiple Entities Tests ---

func TestMultipleEntities_SameMessage(t *testing.T) {
	g := New(map[string]interface{}{
		"entities": []interface{}{"email", "ssn"},
	})
	r := g.Check(context.Background(), makeInput("Email: a@b.com, SSN: 123-45-6789"))
	if r.Pass {
		t.Fatal("expected multiple detections")
	}
	details := r.Details["entities_found"].(int)
	if details < 2 {
		t.Errorf("expected at least 2 entities, got %d", details)
	}
}

func TestMultipleMessages(t *testing.T) {
	raw1, _ := json.Marshal("First message with a@b.com")
	raw2, _ := json.Marshal("Second message with 123-45-6789")
	g := New(map[string]interface{}{
		"entities": []interface{}{"email", "ssn"},
	})
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model: "gpt-4o",
			Messages: []models.Message{
				{Role: "user", Content: raw1},
				{Role: "assistant", Content: raw2},
			},
		},
		Metadata: map[string]string{},
	}
	r := g.Check(context.Background(), input)
	if r.Pass {
		t.Fatal("expected detections across messages")
	}
	details := r.Details["entities_found"].(int)
	if details < 2 {
		t.Errorf("expected 2+ entities across messages, got %d", details)
	}
}

// --- Name & Stage Tests ---

func TestName(t *testing.T) {
	g := New(nil)
	if g.Name() != "pii-detection" {
		t.Errorf("expected name pii-detection, got %s", g.Name())
	}
}

func TestStage(t *testing.T) {
	g := New(nil)
	if g.Stage() != guardrails.StagePre {
		t.Error("expected StagePre")
	}
}

// --- Config Parsing Tests ---

func TestConfig_DefaultRemediation(t *testing.T) {
	g := New(nil)
	if g.remediation != RemediationBlock {
		t.Errorf("expected default remediation block, got %d", g.remediation)
	}
}

func TestConfig_DefaultMaxEntities(t *testing.T) {
	g := New(nil)
	if g.maxEntities != 1 {
		t.Errorf("expected default max_entities 1, got %d", g.maxEntities)
	}
}

func TestConfig_InvalidMaxEntities(t *testing.T) {
	g := New(map[string]interface{}{"max_entities": 0})
	if g.maxEntities != 1 {
		t.Errorf("expected max_entities corrected to 1, got %d", g.maxEntities)
	}
}

func TestConfig_FloatMaxEntities(t *testing.T) {
	g := New(map[string]interface{}{"max_entities": float64(3)})
	if g.maxEntities != 3 {
		t.Errorf("expected max_entities 3 from float64, got %d", g.maxEntities)
	}
}

// --- Remediation Application Order ---

func TestRemediation_MultipleInSameMessage(t *testing.T) {
	g := New(map[string]interface{}{
		"remediation": "redact",
		"entities":    []interface{}{"email"},
	})
	input := makeInput("first@a.com and second@b.com are contacts")
	g.Check(context.Background(), input)

	var content string
	json.Unmarshal(input.Request.Messages[0].Content, &content)
	count := strings.Count(content, "[REDACTED:email]")
	if count != 2 {
		t.Errorf("expected 2 redacted emails, got %d in %q", count, content)
	}
	if strings.Contains(content, "@") {
		t.Error("original emails should be fully replaced")
	}
}

// --- Integration with Engine ---

func TestIntegration_EngineBlock(t *testing.T) {
	g := New(map[string]interface{}{
		"entities": []interface{}{"ssn"},
	})

	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model: "gpt-4o",
			Messages: []models.Message{
				{Role: "user", Content: json.RawMessage(`"My SSN is 123-45-6789"`)},
			},
		},
		Metadata: map[string]string{},
	}

	result := g.Check(context.Background(), input)
	if result.Pass {
		t.Fatal("expected block on SSN")
	}
	if result.Score == 0 {
		t.Error("expected non-zero score")
	}
	if !strings.Contains(result.Message, "ssn") {
		t.Errorf("expected message mentioning ssn, got %q", result.Message)
	}
}

// --- Details Tests ---

func TestDetails_Structure(t *testing.T) {
	g := New(map[string]interface{}{
		"entities": []interface{}{"email"},
	})
	r := g.Check(context.Background(), makeInput("Email: test@example.com"))
	if r.Pass {
		t.Fatal("expected detection")
	}

	count, ok := r.Details["entities_found"].(int)
	if !ok || count != 1 {
		t.Errorf("expected entities_found=1, got %v", r.Details["entities_found"])
	}

	detections, ok := r.Details["detections"].([]interface{})
	if !ok || len(detections) != 1 {
		t.Fatalf("expected 1 detection, got %v", r.Details["detections"])
	}

	det := detections[0].(map[string]interface{})
	if det["type"] != "email" {
		t.Errorf("expected type email, got %v", det["type"])
	}
}

// helpers

func assertEntityFound(t *testing.T, r *guardrails.CheckResult, entityType string) {
	t.Helper()
	if r.Details == nil {
		t.Fatal("expected details in result")
	}
	detections, ok := r.Details["detections"].([]interface{})
	if !ok {
		t.Fatal("expected detections in details")
	}
	for _, d := range detections {
		det := d.(map[string]interface{})
		if det["type"] == entityType {
			return
		}
	}
	t.Errorf("entity type %q not found in detections", entityType)
}
