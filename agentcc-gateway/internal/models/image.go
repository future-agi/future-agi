package models

// ImageRequest represents an OpenAI-compatible image generation request.
type ImageRequest struct {
	Model          string `json:"model"`
	Prompt         string `json:"prompt"`
	N              *int   `json:"n,omitempty"`
	Size           string `json:"size,omitempty"`            // "256x256"|"512x512"|"1024x1024"|"1792x1024"|"1024x1792"
	Quality        string `json:"quality,omitempty"`         // "standard"|"hd"
	ResponseFormat string `json:"response_format,omitempty"` // "url"|"b64_json"
	Style          string `json:"style,omitempty"`           // "natural"|"vivid"
	User           string `json:"user,omitempty"`
}

// ImageResponse represents an OpenAI-compatible image generation response.
type ImageResponse struct {
	Created int64       `json:"created"`
	Data    []ImageData `json:"data"`
}

// ImageData is a single generated image.
type ImageData struct {
	URL           string `json:"url,omitempty"`
	B64JSON       string `json:"b64_json,omitempty"`
	RevisedPrompt string `json:"revised_prompt,omitempty"`
}
