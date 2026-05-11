package models

import "net/http"

// OCRDocument represents a document to be processed by OCR.
type OCRDocument struct {
	Type        string `json:"type"`
	DocumentURL string `json:"document_url,omitempty"`
	ImageURL    string `json:"image_url,omitempty"`
}

// OCRRequest represents an OCR API request.
type OCRRequest struct {
	Model              string      `json:"model"`
	Document           OCRDocument `json:"document"`
	IncludeImageBase64 bool        `json:"include_image_base64"`
	Pages              *int        `json:"pages,omitempty"`
	ImageLimit         *int        `json:"image_limit,omitempty"`
}

// Validate checks that the OCR request is valid.
func (r *OCRRequest) Validate() *APIError {
	if r.Model == "" {
		return ErrBadRequest("missing_model", "model is required")
	}
	if r.Document.Type != "document_url" && r.Document.Type != "image_url" {
		return &APIError{Status: http.StatusBadRequest, Type: ErrTypeInvalidRequest, Code: "invalid_document_type", Message: "document.type must be 'document_url' or 'image_url'"}
	}
	if r.Document.Type == "document_url" && r.Document.DocumentURL == "" {
		return ErrBadRequest("missing_document_url", "document.document_url is required when type is 'document_url'")
	}
	if r.Document.Type == "image_url" && r.Document.ImageURL == "" {
		return ErrBadRequest("missing_image_url", "document.image_url is required when type is 'image_url'")
	}
	if r.Pages != nil && *r.Pages <= 0 {
		return ErrBadRequest("invalid_pages", "pages must be greater than 0")
	}
	if r.ImageLimit != nil && *r.ImageLimit <= 0 {
		return ErrBadRequest("invalid_image_limit", "image_limit must be greater than 0")
	}
	return nil
}

// OCRBoundingBox represents a bounding box for an image region.
type OCRBoundingBox struct {
	X      float64 `json:"x"`
	Y      float64 `json:"y"`
	Width  float64 `json:"width"`
	Height float64 `json:"height"`
}

// OCRImage represents an extracted image from OCR processing.
type OCRImage struct {
	ImageBase64 string          `json:"image_base64,omitempty"`
	BBox        *OCRBoundingBox `json:"bbox,omitempty"`
}

// OCRDimensions represents page dimensions.
type OCRDimensions struct {
	DPI    int `json:"dpi"`
	Height int `json:"height"`
	Width  int `json:"width"`
}

// OCRPage represents a single page of OCR output.
type OCRPage struct {
	Index      int            `json:"index"`
	Markdown   string         `json:"markdown"`
	Images     []OCRImage     `json:"images,omitempty"`
	Dimensions *OCRDimensions `json:"dimensions,omitempty"`
}

// OCRUsage represents OCR processing usage info.
type OCRUsage struct {
	PagesProcessed int `json:"pages_processed"`
	DocSizeBytes   int `json:"doc_size_bytes"`
}

// OCRResponse represents an OCR API response.
type OCRResponse struct {
	Object    string    `json:"object"`
	Model     string    `json:"model"`
	Pages     []OCRPage `json:"pages"`
	UsageInfo *OCRUsage `json:"usage_info,omitempty"`
}
