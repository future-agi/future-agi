package models

// BatchRequest represents a single request within a provider-level batch.
type BatchRequest struct {
	CustomID string                `json:"custom_id"`
	Method   string                `json:"method"`   // POST
	URL      string                `json:"url"`       // /v1/chat/completions
	Body     ChatCompletionRequest `json:"body"`
}

// ProviderBatchStatus represents the unified status of a provider-level batch.
type ProviderBatchStatus struct {
	ID           string `json:"id"`
	Status       string `json:"status"` // queued, processing, completed, failed, cancelled, expired
	Total        int    `json:"total"`
	Completed    int    `json:"completed"`
	Failed       int    `json:"failed"`
	OutputFileID string `json:"output_file_id,omitempty"`
	ErrorFileID  string `json:"error_file_id,omitempty"`
	CreatedAt    int64  `json:"created_at"`
	CompletedAt  *int64 `json:"completed_at,omitempty"`
}

// Provider batch status constants.
const (
	BatchStatusQueued     = "queued"
	BatchStatusProcessing = "processing"
	BatchStatusCompleted  = "completed"
	BatchStatusFailed     = "failed"
	BatchStatusCancelled  = "cancelled"
	BatchStatusExpired    = "expired"
)
