package a2a

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// Client sends A2A messages to external agents.
type Client struct {
	httpClient *http.Client
	agent      *Agent
}

// NewClient creates an A2A client for a specific agent.
func NewClient(agent *Agent) *Client {
	return &Client{
		httpClient: &http.Client{
			Timeout: 120 * time.Second,
			Transport: &http.Transport{
				MaxIdleConnsPerHost: 5,
				IdleConnTimeout:     90 * time.Second,
			},
		},
		agent: agent,
	}
}

// SendMessage sends a message to the agent and returns the completed task.
func (c *Client) SendMessage(ctx context.Context, msg *Message) (*Task, error) {
	params := MessageSendParams{
		Message: *msg,
	}
	paramsData, err := json.Marshal(params)
	if err != nil {
		return nil, fmt.Errorf("marshal params: %w", err)
	}

	// Build JSON-RPC request (reuse MCP's JSON-RPC message type).
	rpcMsg := &mcp.Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodMessageSend,
		Params:  paramsData,
	}

	body, err := json.Marshal(rpcMsg)
	if err != nil {
		return nil, fmt.Errorf("marshal rpc: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST", c.agent.URL, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	applyAuth(req, c.agent.Auth)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		c.agent.healthy.Store(false)
		return nil, fmt.Errorf("http request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return nil, fmt.Errorf("a2a http %d: %s", resp.StatusCode, string(respBody))
	}

	// Parse JSON-RPC response.
	var rpcResp mcp.Message
	if err := json.NewDecoder(resp.Body).Decode(&rpcResp); err != nil {
		return nil, fmt.Errorf("decode response: %w", err)
	}

	if rpcResp.Error != nil {
		return nil, fmt.Errorf("a2a error %d: %s", rpcResp.Error.Code, rpcResp.Error.Message)
	}

	var task Task
	if err := json.Unmarshal(rpcResp.Result, &task); err != nil {
		return nil, fmt.Errorf("decode task: %w", err)
	}

	c.agent.healthy.Store(true)
	return &task, nil
}

// GetTask retrieves a task by ID from the agent.
func (c *Client) GetTask(ctx context.Context, taskID string) (*Task, error) {
	params := TaskGetParams{TaskID: taskID}
	paramsData, _ := json.Marshal(params)

	rpcMsg := &mcp.Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodTasksGet,
		Params:  paramsData,
	}

	body, _ := json.Marshal(rpcMsg)

	req, err := http.NewRequestWithContext(ctx, "POST", c.agent.URL, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}

	req.Header.Set("Content-Type", "application/json")
	applyAuth(req, c.agent.Auth)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return nil, fmt.Errorf("a2a http %d: %s", resp.StatusCode, string(respBody))
	}

	var rpcResp mcp.Message
	if err := json.NewDecoder(resp.Body).Decode(&rpcResp); err != nil {
		return nil, err
	}
	if rpcResp.Error != nil {
		return nil, fmt.Errorf("a2a error: %s", rpcResp.Error.Message)
	}

	var task Task
	if err := json.Unmarshal(rpcResp.Result, &task); err != nil {
		return nil, err
	}
	return &task, nil
}

// ExtractText collects all text parts from a task's artifacts into a single string.
func ExtractText(task *Task) string {
	var text string
	for _, a := range task.Artifacts {
		for _, p := range a.Parts {
			if p.Type == "text" {
				if text != "" {
					text += "\n"
				}
				text += p.Text
			}
		}
	}
	return text
}

// TextMessage creates a simple text message.
func TextMessage(role, text string) *Message {
	return &Message{
		Role: role,
		Parts: []MessagePart{
			{Type: "text", Text: text},
		},
	}
}
