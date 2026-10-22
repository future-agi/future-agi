package models

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"regexp"
	"strconv"
	"strings"
)

// OCRPagesMetadataKey is the request metadata key the cost plugin records an
// OCR request's processed pages under: the usage unit OCR is metered in, BYOK
// included (free but metered). The handler clears any value a caller sent.
const OCRPagesMetadataKey = "ocr_pages"

// OCRDocument is the document an OCR request reads: a document URL, an image
// URL, or a file already uploaded to the provider (Mistral's "file" chunk).
type OCRDocument struct {
	Type         string `json:"type"`
	DocumentURL  string `json:"document_url,omitempty"`
	DocumentName string `json:"document_name,omitempty"`
	ImageURL     string `json:"image_url,omitempty"`
	FileID       string `json:"file_id,omitempty"`
}

// UnmarshalJSON accepts image_url as a string or as Mistral's {"url": ...}
// object form.
func (d *OCRDocument) UnmarshalJSON(b []byte) error {
	type plain OCRDocument
	var aux struct {
		plain
		ImageURL json.RawMessage `json:"image_url,omitempty"`
	}
	if err := json.Unmarshal(b, &aux); err != nil {
		return err
	}
	*d = OCRDocument(aux.plain)
	d.ImageURL = ""
	if len(aux.ImageURL) == 0 || bytes.Equal(aux.ImageURL, []byte("null")) {
		return nil
	}
	if aux.ImageURL[0] == '{' {
		var obj struct {
			URL string `json:"url"`
		}
		if err := json.Unmarshal(aux.ImageURL, &obj); err != nil {
			return err
		}
		d.ImageURL = obj.URL
		return nil
	}
	return json.Unmarshal(aux.ImageURL, &d.ImageURL)
}

// Reference is what identifies the document: its URL, or the provider's file
// id for a "file" chunk.
func (d OCRDocument) Reference() string {
	switch d.Type {
	case "image_url":
		return d.ImageURL
	case "file":
		return d.FileID
	default:
		return d.DocumentURL
	}
}

// OCRRequest is an OCR API request (Mistral's /v1/ocr shape). The gateway
// reads only the fields below; every other option (include_blocks,
// confidence_scores_granularity, bbox_annotation_format,
// document_annotation_format, document_annotation_prompt, table_format,
// extract_header, extract_footer, ...) is accepted and reaches the provider
// unchanged, because a decoded request is forwarded as the body it came from.
type OCRRequest struct {
	Model              string      `json:"model"`
	Document           OCRDocument `json:"document"`
	IncludeImageBase64 bool        `json:"include_image_base64"`
	// Pages is the page selection exactly as sent: a list of page indexes
	// ([0,1,2]), a range string ("0-5", "0-2,7") or one page index (3, which
	// the gateway accepts too). Validate checks it and it is forwarded
	// verbatim.
	Pages      json.RawMessage `json:"pages,omitempty"`
	ImageLimit *int            `json:"image_limit,omitempty"`

	// KeepText is set by the gateway, never by the body: the request's
	// content mode is full and its org opted in to storing extracted text,
	// so the recorded payloads may carry the document's markdown.
	KeepText bool `json:"-"`

	raw      json.RawMessage // the body as received
	rawModel string          // the model the body named
}

// UnmarshalJSON decodes the fields the gateway reads and keeps the body, so
// MarshalJSON can forward it unchanged. A body that spells a field the
// gateway reads twice, or in another case, is refused (checkOCRBodyFields).
func (r *OCRRequest) UnmarshalJSON(b []byte) error {
	type plain OCRRequest
	var p plain
	if err := json.Unmarshal(b, &p); err != nil {
		return err
	}
	if err := checkOCRBodyFields(b); err != nil {
		return err
	}
	*r = OCRRequest(p)
	r.raw = append(json.RawMessage(nil), b...)
	r.rawModel = r.Model
	return nil
}

// The fields the gateway reads from an OCR request body: at the top level,
// in document, and in document.image_url's {"url": ...} form.
var (
	ocrRequestFields  = []string{"model", "document", "pages", "image_limit", "include_image_base64"}
	ocrDocumentFields = []string{"type", "document_url", "document_name", "image_url", "file_id"}
	ocrImageURLFields = []string{"url"}
)

// checkOCRBodyFields refuses a body in which a field the gateway reads
// appears more than once, or spelled in another case. The body is forwarded
// as sent and the upstream reads it by its exact keys, so the model, document
// and pages the gateway authorizes, limits, prices, logs and hashes are exactly
// the ones the upstream reads.
func checkOCRBodyFields(body []byte) error {
	top, err := checkOCRFields("", body, ocrRequestFields)
	if err != nil {
		return err
	}
	doc, err := checkOCRFields("document.", top["document"], ocrDocumentFields)
	if err != nil {
		return err
	}
	_, err = checkOCRFields("document.image_url.", doc["image_url"], ocrImageURLFields)
	return err
}

// checkOCRFields checks the keys of the JSON object obj against the fields
// read, which it returns by name. A value that is not an object (absent,
// null, a string) has no keys to check. Keys are compared after unescaping,
// as every JSON reader sees them, and folded the way encoding/json folds
// them (Unicode simple folding: "pageſ" matches "pages").
func checkOCRFields(path string, obj json.RawMessage, read []string) (map[string]json.RawMessage, error) {
	dec := json.NewDecoder(bytes.NewReader(obj))
	if tok, err := dec.Token(); err != nil || tok != json.Delim('{') {
		return nil, nil
	}
	values := make(map[string]json.RawMessage, len(read))
	for dec.More() {
		tok, err := dec.Token()
		if err != nil {
			return nil, err
		}
		key, _ := tok.(string)
		var value json.RawMessage
		if err := dec.Decode(&value); err != nil {
			return nil, err
		}
		for _, field := range read {
			if !strings.EqualFold(key, field) {
				continue
			}
			if key != field {
				return nil, duplicateOCRField(path+key, fmt.Sprintf("field %q must be spelled exactly %q", path+key, path+field))
			}
			if _, seen := values[field]; seen {
				return nil, duplicateOCRField(path+key, fmt.Sprintf("field %q appears more than once; send it once", path+field))
			}
			values[field] = value
		}
	}
	return values, nil
}

// duplicateOCRField is the 400 for a field sent twice or in another case.
func duplicateOCRField(param, message string) *APIError {
	err := ErrBadRequest("duplicate_field", message)
	err.Param = &param
	return err
}

// MarshalJSON is the body forwarded upstream: the one the client sent, with
// its model replaced only when the gateway changed it. A request built in
// code is encoded from its fields.
func (r *OCRRequest) MarshalJSON() ([]byte, error) {
	if len(r.raw) == 0 {
		type plain OCRRequest
		return json.Marshal((*plain)(r))
	}
	if r.Model == r.rawModel {
		return r.raw, nil
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(r.raw, &fields); err != nil {
		return nil, err
	}
	model, err := json.Marshal(r.Model)
	if err != nil {
		return nil, err
	}
	fields["model"] = model
	return json.Marshal(fields)
}

// Validate checks that the OCR request is valid.
func (r *OCRRequest) Validate() *APIError {
	if r.Model == "" {
		return ErrBadRequest("missing_model", "model is required")
	}
	switch r.Document.Type {
	case "document_url":
		if r.Document.DocumentURL == "" {
			return ErrBadRequest("missing_document_url", "document.document_url is required when type is 'document_url'")
		}
	case "image_url":
		if r.Document.ImageURL == "" {
			return ErrBadRequest("missing_image_url", "document.image_url is required when type is 'image_url'")
		}
	case "file":
		if r.Document.FileID == "" {
			return ErrBadRequest("missing_file_id", "document.file_id is required when type is 'file'")
		}
	default:
		return &APIError{Status: http.StatusBadRequest, Type: ErrTypeInvalidRequest, Code: "invalid_document_type", Message: "document.type must be 'document_url', 'image_url' or 'file'"}
	}
	if !validOCRPages(r.Pages) {
		return ErrBadRequest("invalid_pages", `pages must be a list of page indexes ([0,1,2]), a range string ("0-5", "0-2,7") or one page index, starting from 0`)
	}
	if r.ImageLimit != nil && *r.ImageLimit <= 0 {
		return ErrBadRequest("invalid_image_limit", "image_limit must be greater than 0")
	}
	return nil
}

// ocrPageRanges is a range string: comma-separated page indexes or a-b
// ranges, spaces allowed around each part.
var ocrPageRanges = regexp.MustCompile(`^\s*\d+(\s*-\s*\d+)?(\s*,\s*\d+(\s*-\s*\d+)?)*\s*$`)

// validOCRPages reports whether pages is absent, one non-negative page
// index, a non-empty list of them, or a range string whose ranges do not run
// backwards.
func validOCRPages(pages json.RawMessage) bool {
	pages = bytes.TrimSpace(pages)
	if len(pages) == 0 || bytes.Equal(pages, []byte("null")) {
		return true
	}
	switch pages[0] {
	case '0', '1', '2', '3', '4', '5', '6', '7', '8', '9':
		var page int64
		return json.Unmarshal(pages, &page) == nil
	case '[':
		var list []int64
		if err := json.Unmarshal(pages, &list); err != nil || len(list) == 0 {
			return false
		}
		for _, p := range list {
			if p < 0 {
				return false
			}
		}
		return true
	case '"':
		var s string
		if err := json.Unmarshal(pages, &s); err != nil || !ocrPageRanges.MatchString(s) {
			return false
		}
		for _, part := range strings.Split(s, ",") {
			from, to, isRange := strings.Cut(part, "-")
			if !isRange {
				continue
			}
			a, errA := strconv.Atoi(strings.TrimSpace(from))
			b, errB := strconv.Atoi(strings.TrimSpace(to))
			if errA != nil || errB != nil || a > b {
				return false
			}
		}
		return true
	default:
		return false
	}
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
	// ImageAnnotation is the provider's bbox annotation of the image, set
	// when the request asked for bbox_annotation_format.
	ImageAnnotation json.RawMessage `json:"image_annotation,omitempty"`
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

// OCRResponse is an OCR API response, decoded only as far as metering and
// the recorded payloads need. The client gets the provider's body (Raw), so
// fields the gateway does not model (headers, footers, blocks, confidence
// scores) are never dropped.
type OCRResponse struct {
	Object    string    `json:"object"`
	Model     string    `json:"model"`
	Pages     []OCRPage `json:"pages"`
	UsageInfo *OCRUsage `json:"usage_info,omitempty"`
	// DocumentAnnotation is the provider's document annotation, set when the
	// request asked for document_annotation_format.
	DocumentAnnotation json.RawMessage `json:"document_annotation,omitempty"`

	raw json.RawMessage // the provider's body as received
}

// UnmarshalJSON decodes the response and keeps the provider's body.
func (r *OCRResponse) UnmarshalJSON(b []byte) error {
	type plain OCRResponse
	var p plain
	if err := json.Unmarshal(b, &p); err != nil {
		return err
	}
	*r = OCRResponse(p)
	r.raw = append(json.RawMessage(nil), b...)
	return nil
}

// Raw is the provider's response body as received, or nil for a response
// built in code.
func (r *OCRResponse) Raw() json.RawMessage { return r.raw }

// PagesProcessed is the pages the provider reports processing
// (usage_info.pages_processed). reported is false when it reported none, and
// the count is then the pages it returned.
func (r *OCRResponse) PagesProcessed() (pages int, reported bool) {
	if r.UsageInfo != nil && r.UsageInfo.PagesProcessed > 0 {
		return r.UsageInfo.PagesProcessed, true
	}
	return len(r.Pages), false
}

// AnnotatedPages is how many processed pages carry an annotation: all of
// them when the response has a document annotation, else the pages with at
// least one annotated image (bbox annotation).
func (r *OCRResponse) AnnotatedPages() int {
	if ocrValueSet(r.DocumentAnnotation) {
		n, _ := r.PagesProcessed()
		return n
	}
	n := 0
	for _, p := range r.Pages {
		for _, img := range p.Images {
			if ocrValueSet(img.ImageAnnotation) {
				n++
				break
			}
		}
	}
	return n
}

// ocrValueSet reports whether a raw JSON value carries something: not absent,
// null or an empty string.
func ocrValueSet(v json.RawMessage) bool {
	v = bytes.TrimSpace(v)
	return len(v) > 0 && !bytes.Equal(v, []byte("null")) && !bytes.Equal(v, []byte(`""`))
}
