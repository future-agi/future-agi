package models

// FileObject represents an uploaded file in the OpenAI Files API format.
type FileObject struct {
	ID        string `json:"id"`
	Object    string `json:"object"` // always "file"
	Bytes     int    `json:"bytes"`
	CreatedAt int64  `json:"created_at"`
	Filename  string `json:"filename"`
	Purpose   string `json:"purpose"`
	Status    string `json:"status,omitempty"` // "uploaded", "processed", "error"
}

// FileListResponse is the response for GET /v1/files.
type FileListResponse struct {
	Object string       `json:"object"` // "list"
	Data   []FileObject `json:"data"`
}

// FileDeleteResponse is the response for DELETE /v1/files/{file_id}.
type FileDeleteResponse struct {
	ID      string `json:"id"`
	Object  string `json:"object"` // "file"
	Deleted bool   `json:"deleted"`
}
