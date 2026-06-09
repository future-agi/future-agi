package a2a

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Provider implements the providers.Provider interface for A2A agents.
// Requests with model "a2a/<agent-name>" are forwarded to the named agent
// via the A2A protocol and results are translated back to OpenAI format.
type Provider struct {
	registry *Registry
}

// NewProvider creates a provider backed by the A2A agent registry.
func NewProvider(registry *Registry) *Provider {
	return &Provider{registry: registry}
}

// ID returns the provider identifier.
func (p *Provider) ID() string { return "a2a" }

// ChatCompletion sends the request to an A2A agent and returns an OpenAI-compatible response.
// The model field must be "a2a/<agent-name>".
func (p *Provider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	agentName := extractAgentName(req.Model)
	if agentName == "" {
		return nil, fmt.Errorf("a2a provider: model must be 'a2a/<agent-name>', got %q", req.Model)
	}

	agent, ok := p.registry.Get(agentName)
	if !ok {
		return nil, fmt.Errorf("a2a provider: agent %q not found in registry", agentName)
	}
	if !agent.Healthy() {
		return nil, fmt.Errorf("a2a provider: agent %q is unhealthy", agentName)
	}

	// Convert OpenAI messages to a single A2A text message.
	text := extractTextFromMessages(req.Messages)
	if text == "" {
		return nil, fmt.Errorf("a2a provider: no text content in messages")
	}

	msg := TextMessage("user", text)
	client := NewClient(agent)

	task, err := client.SendMessage(ctx, msg)
	if err != nil {
		return nil, fmt.Errorf("a2a provider: send to %q: %w", agentName, err)
	}

	if task.Status.State == TaskStatusFailed {
		errMsg := "task failed"
		if len(task.Status.Message) > 0 {
			errMsg = task.Status.Message[0].Text
		}
		return nil, fmt.Errorf("a2a provider: agent %q failed: %s", agentName, errMsg)
	}

	// Convert A2A task back to OpenAI ChatCompletionResponse.
	return taskToResponse(task, req.Model), nil
}

// StreamChatCompletion is not supported for A2A agents (they use task-based async).
// Returns a single chunk with the full response.
func (p *Provider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk, 2)
	errCh := make(chan error, 1)

	go func() {
		defer close(chunks)
		defer close(errCh)

		resp, err := p.ChatCompletion(ctx, req)
		if err != nil {
			errCh <- err
			return
		}

		// Emit a single chunk with the full response content.
		if len(resp.Choices) > 0 {
			var content string
			if err := json.Unmarshal(resp.Choices[0].Message.Content, &content); err != nil {
				content = string(resp.Choices[0].Message.Content)
			}
			finish := "stop"
			chunks <- models.StreamChunk{
				ID:      resp.ID,
				Object:  "chat.completion.chunk",
				Created: resp.Created,
				Model:   resp.Model,
				Choices: []models.StreamChoice{
					{
						Index: 0,
						Delta: models.Delta{
							Role:    "assistant",
							Content: &content,
						},
						FinishReason: &finish,
					},
				},
			}
		}
	}()

	return chunks, errCh
}

// ListModels returns A2A agents as model objects.
func (p *Provider) ListModels(ctx context.Context) ([]models.ModelObject, error) {
	agents := p.registry.List()
	result := make([]models.ModelObject, 0, len(agents))
	for _, a := range agents {
		result = append(result, models.ModelObject{
			ID:      "a2a/" + a.Name,
			Object:  "model",
			OwnedBy: "a2a",
		})
	}
	return result, nil
}

// Close releases resources.
func (p *Provider) Close() error { return nil }

// extractAgentName strips the "a2a/" prefix from a model name.
func extractAgentName(model string) string {
	if strings.HasPrefix(model, "a2a/") {
		return model[4:]
	}
	return ""
}

// extractTextFromMessages concatenates user message content into a single text.
func extractTextFromMessages(messages []models.Message) string {
	var parts []string
	for _, msg := range messages {
		if msg.Role == "system" || msg.Role == "user" {
			// Content is json.RawMessage — can be a string or array of content parts.
			var text string
			if err := json.Unmarshal(msg.Content, &text); err == nil {
				if text != "" {
					parts = append(parts, text)
				}
				continue
			}

			// Try array of content parts (multimodal format).
			var contentParts []struct {
				Type string `json:"type"`
				Text string `json:"text"`
			}
			if err := json.Unmarshal(msg.Content, &contentParts); err == nil {
				for _, cp := range contentParts {
					if cp.Type == "text" && cp.Text != "" {
						parts = append(parts, cp.Text)
					}
				}
			}
		}
	}
	return strings.Join(parts, "\n")
}

// taskToResponse converts an A2A Task into an OpenAI ChatCompletionResponse.
func taskToResponse(task *Task, model string) *models.ChatCompletionResponse {
	text := ExtractText(task)
	contentJSON, _ := json.Marshal(text)

	return &models.ChatCompletionResponse{
		ID:      "a2a-" + task.ID,
		Object:  "chat.completion",
		Created: time.Now().Unix(),
		Model:   model,
		Choices: []models.Choice{
			{
				Index: 0,
				Message: models.Message{
					Role:    "assistant",
					Content: contentJSON,
				},
				FinishReason: "stop",
			},
		},
	}
}
