// Package gemini — local Gemini wire-format type definitions.
//
// These are minimal copies of the types in internal/providers/gemini/translate.go.
// We DO NOT import that package because it belongs to a different concern
// (outbound translation) and carries heavy dependencies. Copy only what's
// needed for inbound translation.
package gemini

import "encoding/json"

// ── Inbound request types ───────────────────────────────────────────────────

type geminiRequest struct {
	Contents          []geminiContent          `json:"contents"`
	SystemInstruction *geminiContent           `json:"systemInstruction,omitempty"`
	GenerationConfig  *geminiGenerationConfig  `json:"generationConfig,omitempty"`
	Tools             []geminiToolDeclarations `json:"tools,omitempty"`
	ToolConfig        *geminiToolConfig        `json:"toolConfig,omitempty"`
	SafetySettings    []json.RawMessage        `json:"safetySettings,omitempty"`
	CachedContent     string                   `json:"cachedContent,omitempty"`
}

type geminiContent struct {
	Role  string       `json:"role,omitempty"`
	Parts []geminiPart `json:"parts"`
}

type geminiPart struct {
	Text             string              `json:"text,omitempty"`
	InlineData       *geminiInlineData   `json:"inlineData,omitempty"`
	FileData         *geminiFileData     `json:"fileData,omitempty"`
	FunctionCall     *geminiFunctionCall `json:"functionCall,omitempty"`
	FunctionResponse *geminiFuncResponse `json:"functionResponse,omitempty"`
}

type geminiInlineData struct {
	MimeType string `json:"mimeType"`
	Data     string `json:"data"`
}

type geminiFileData struct {
	MimeType string `json:"mimeType"`
	FileURI  string `json:"fileUri"`
}

type geminiFunctionCall struct {
	Name string          `json:"name"`
	Args json.RawMessage `json:"args"`
}

type geminiFuncResponse struct {
	Name     string          `json:"name"`
	Response json.RawMessage `json:"response"`
}

type geminiGenerationConfig struct {
	Temperature        *float64        `json:"temperature,omitempty"`
	TopP               *float64        `json:"topP,omitempty"`
	TopK               *float64        `json:"topK,omitempty"`
	MaxOutputTokens    *int            `json:"maxOutputTokens,omitempty"`
	CandidateCount     *int            `json:"candidateCount,omitempty"`
	StopSequences      []string        `json:"stopSequences,omitempty"`
	ResponseMimeType   string          `json:"responseMimeType,omitempty"`
	ResponseSchema     json.RawMessage `json:"responseSchema,omitempty"`
	ResponseModalities []string        `json:"responseModalities,omitempty"`
}

type geminiToolDeclarations struct {
	FunctionDeclarations []geminiFuncDecl `json:"functionDeclarations,omitempty"`
}

type geminiFuncDecl struct {
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	Parameters  json.RawMessage `json:"parameters,omitempty"`
}

type geminiToolConfig struct {
	FunctionCallingConfig *geminiFunctionCallingConfig `json:"functionCallingConfig,omitempty"`
}

type geminiFunctionCallingConfig struct {
	Mode                 string   `json:"mode"`
	AllowedFunctionNames []string `json:"allowedFunctionNames,omitempty"`
}

// ── Outbound response types ─────────────────────────────────────────────────

type geminiResponse struct {
	Candidates    []geminiCandidate    `json:"candidates"`
	UsageMetadata *geminiUsageMetadata `json:"usageMetadata,omitempty"`
	ModelVersion  string               `json:"modelVersion,omitempty"`
}

type geminiCandidate struct {
	Content      geminiContent `json:"content"`
	FinishReason string        `json:"finishReason,omitempty"`
}

type geminiUsageMetadata struct {
	PromptTokenCount     int `json:"promptTokenCount"`
	CandidatesTokenCount int `json:"candidatesTokenCount"`
	TotalTokenCount      int `json:"totalTokenCount"`
}

type geminiErrorResponse struct {
	Error geminiErrorDetail `json:"error"`
}

type geminiErrorDetail struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Status  string `json:"status"`
}
