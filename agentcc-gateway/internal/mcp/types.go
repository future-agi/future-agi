// Package mcp implements the Model Context Protocol (MCP) for the Agentcc gateway.
// Agentcc acts as both an MCP server (agents connect to Agentcc) and an MCP client
// (Agentcc connects to upstream MCP tool servers).
//
// Protocol version: 2025-11-25
// Transport: Streamable HTTP (JSON-RPC 2.0 over HTTP + SSE)
package mcp

import (
	"encoding/json"
	"fmt"
	"regexp"
)

// Protocol version supported by this implementation.
const ProtocolVersion = "2025-11-25"

// --- JSON-RPC 2.0 Base Types ---

// Message is a JSON-RPC 2.0 message (request, response, or notification).
type Message struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`      // number or string; nil for notifications
	Method  string          `json:"method,omitempty"`   // present for requests/notifications
	Params  json.RawMessage `json:"params,omitempty"`   // present for requests/notifications
	Result  json.RawMessage `json:"result,omitempty"`   // present for success responses
	Error   *RPCError       `json:"error,omitempty"`    // present for error responses
}

// IsRequest returns true if this message is a request (has method and id).
func (m *Message) IsRequest() bool {
	return m.Method != "" && m.ID != nil
}

// IsNotification returns true if this message is a notification (has method, no id).
func (m *Message) IsNotification() bool {
	return m.Method != "" && m.ID == nil
}

// IsResponse returns true if this message is a response (has result or error, and id).
func (m *Message) IsResponse() bool {
	return m.Method == "" && m.ID != nil
}

// RPCError is a JSON-RPC 2.0 error object.
type RPCError struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data,omitempty"`
}

func (e *RPCError) Error() string {
	return fmt.Sprintf("JSON-RPC error %d: %s", e.Code, e.Message)
}

// Standard JSON-RPC error codes.
const (
	ErrCodeParse          = -32700
	ErrCodeInvalidRequest = -32600
	ErrCodeMethodNotFound = -32601
	ErrCodeInvalidParams  = -32602
	ErrCodeInternal       = -32603
)

// NewResponse creates a success response for a given request ID.
func NewResponse(id json.RawMessage, result interface{}) (*Message, error) {
	data, err := json.Marshal(result)
	if err != nil {
		return nil, err
	}
	return &Message{
		JSONRPC: "2.0",
		ID:      id,
		Result:  data,
	}, nil
}

// NewErrorResponse creates an error response for a given request ID.
func NewErrorResponse(id json.RawMessage, code int, message string) *Message {
	return &Message{
		JSONRPC: "2.0",
		ID:      id,
		Error: &RPCError{
			Code:    code,
			Message: message,
		},
	}
}

// NewNotification creates a notification message (no ID, no response expected).
func NewNotification(method string, params interface{}) (*Message, error) {
	var data json.RawMessage
	if params != nil {
		var err error
		data, err = json.Marshal(params)
		if err != nil {
			return nil, err
		}
	}
	return &Message{
		JSONRPC: "2.0",
		Method:  method,
		Params:  data,
	}, nil
}

// --- MCP Initialize Types ---

// InitializeParams is sent by the client to start a session.
type InitializeParams struct {
	ProtocolVersion string             `json:"protocolVersion"`
	Capabilities    ClientCapabilities `json:"capabilities"`
	ClientInfo      Implementation     `json:"clientInfo"`
}

// InitializeResult is the server's response to initialize.
type InitializeResult struct {
	ProtocolVersion string             `json:"protocolVersion"`
	Capabilities    ServerCapabilities `json:"capabilities"`
	ServerInfo      Implementation     `json:"serverInfo"`
	Instructions    string             `json:"instructions,omitempty"`
}

// Implementation describes a client or server implementation.
type Implementation struct {
	Name    string `json:"name"`
	Version string `json:"version"`
}

// ClientCapabilities declares what the client supports.
type ClientCapabilities struct {
	Sampling *SamplingCapability `json:"sampling,omitempty"`
	Roots    *RootsCapability    `json:"roots,omitempty"`
}

// SamplingCapability declares the client supports server-initiated LLM calls.
type SamplingCapability struct{}

// RootsCapability declares the client can provide filesystem roots.
type RootsCapability struct {
	ListChanged bool `json:"listChanged,omitempty"`
}

// ServerCapabilities declares what the server supports.
type ServerCapabilities struct {
	Tools     *ToolsCapability     `json:"tools,omitempty"`
	Resources *ResourcesCapability `json:"resources,omitempty"`
	Prompts   *PromptsCapability   `json:"prompts,omitempty"`
}

// ToolsCapability declares the server exposes tools.
type ToolsCapability struct {
	ListChanged bool `json:"listChanged,omitempty"`
}

// ResourcesCapability declares the server exposes resources.
type ResourcesCapability struct {
	Subscribe   bool `json:"subscribe,omitempty"`
	ListChanged bool `json:"listChanged,omitempty"`
}

// PromptsCapability declares the server exposes prompts.
type PromptsCapability struct {
	ListChanged bool `json:"listChanged,omitempty"`
}

// --- MCP Tool Types ---

// Tool describes an MCP tool exposed by a server.
type Tool struct {
	Name         string          `json:"name"`
	Description  string          `json:"description,omitempty"`
	InputSchema  json.RawMessage `json:"inputSchema"`
	OutputSchema json.RawMessage `json:"outputSchema,omitempty"`
	Annotations  *ToolAnnotations `json:"annotations,omitempty"`
}

// ToolAnnotations provides hints about tool behavior.
type ToolAnnotations struct {
	Title           string `json:"title,omitempty"`
	ReadOnlyHint    *bool  `json:"readOnlyHint,omitempty"`
	DestructiveHint *bool  `json:"destructiveHint,omitempty"`
	IdempotentHint  *bool  `json:"idempotentHint,omitempty"`
	OpenWorldHint   *bool  `json:"openWorldHint,omitempty"`
}

// ToolCallParams is the params for tools/call.
type ToolCallParams struct {
	Name      string                 `json:"name"`
	Arguments map[string]interface{} `json:"arguments,omitempty"`
}

// ToolCallResult is the result of tools/call.
type ToolCallResult struct {
	Content []ContentPart `json:"content"`
	IsError bool          `json:"isError,omitempty"`
}

// ContentPart is a piece of content in a tool result.
type ContentPart struct {
	Type     string `json:"type"` // "text", "image", "resource"
	Text     string `json:"text,omitempty"`
	Data     string `json:"data,omitempty"`
	MIMEType string `json:"mimeType,omitempty"`
}

// ListToolsResult is the result of tools/list.
type ListToolsResult struct {
	Tools      []Tool `json:"tools"`
	NextCursor string `json:"nextCursor,omitempty"`
}

// --- MCP Resource Types ---

// Resource describes an MCP resource exposed by a server.
type Resource struct {
	URI         string `json:"uri"`
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	MIMEType    string `json:"mimeType,omitempty"`
}

// ResourceTemplate describes a parameterized resource URI template.
type ResourceTemplate struct {
	URITemplate string `json:"uriTemplate"`
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	MIMEType    string `json:"mimeType,omitempty"`
}

// ListResourcesResult is the result of resources/list.
type ListResourcesResult struct {
	Resources []Resource `json:"resources"`
}

// ReadResourceParams is the params for resources/read.
type ReadResourceParams struct {
	URI string `json:"uri"`
}

// ReadResourceResult is the result of resources/read.
type ReadResourceResult struct {
	Contents []ResourceContent `json:"contents"`
}

// ResourceContent is the content of a resource.
type ResourceContent struct {
	URI      string `json:"uri"`
	MIMEType string `json:"mimeType,omitempty"`
	Text     string `json:"text,omitempty"`
	Blob     string `json:"blob,omitempty"` // base64-encoded binary
}

// --- MCP Prompt Types ---

// Prompt describes an MCP prompt template exposed by a server.
type Prompt struct {
	Name        string           `json:"name"`
	Description string           `json:"description,omitempty"`
	Arguments   []PromptArgument `json:"arguments,omitempty"`
}

// PromptArgument describes a parameter for a prompt.
type PromptArgument struct {
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	Required    bool   `json:"required,omitempty"`
}

// ListPromptsResult is the result of prompts/list.
type ListPromptsResult struct {
	Prompts []Prompt `json:"prompts"`
}

// GetPromptParams is the params for prompts/get.
type GetPromptParams struct {
	Name      string            `json:"name"`
	Arguments map[string]string `json:"arguments,omitempty"`
}

// GetPromptResult is the result of prompts/get.
type GetPromptResult struct {
	Description string          `json:"description,omitempty"`
	Messages    []PromptMessage `json:"messages"`
}

// PromptMessage is a message in a prompt result.
type PromptMessage struct {
	Role    string      `json:"role"` // "user" or "assistant"
	Content ContentPart `json:"content"`
}

// --- Tool Name Validation ---

// toolNameRegex validates MCP tool names: 1-128 chars, [A-Za-z0-9_\-.]
var toolNameRegex = regexp.MustCompile(`^[A-Za-z0-9_\-.]{1,128}$`)

// ValidateToolName checks if a tool name is valid per the MCP spec.
func ValidateToolName(name string) bool {
	return toolNameRegex.MatchString(name)
}

// --- MCP Method Constants ---

const (
	MethodInitialize          = "initialize"
	MethodInitialized         = "notifications/initialized"
	MethodToolsList           = "tools/list"
	MethodToolsCall           = "tools/call"
	MethodResourcesList       = "resources/list"
	MethodResourcesRead       = "resources/read"
	MethodPromptsList         = "prompts/list"
	MethodPromptsGet          = "prompts/get"
	MethodPing                = "ping"
	MethodToolsListChanged    = "notifications/tools/list_changed"
	MethodCancelled           = "notifications/cancelled"
)
