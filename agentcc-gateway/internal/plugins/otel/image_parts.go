package otel

import (
	"encoding/base64"
	"encoding/json"
	"io"
	"mime"
	"net/url"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Image metadata is deliberately limited to kind, media type and decoded byte
// count. A remote reference has unknown type and zero known bytes: never fetch
// it, infer metadata from its path, or retain a host, query, file ID or hash.
type imageInfo struct {
	mediaType string
	data      string
	base64    bool
}

func (image imageInfo) placeholder(mode models.ContentMode) string {
	if mode != models.ContentModeMetadata {
		return "[image]"
	}
	mediaType := "unknown"
	if len(image.mediaType) <= 127 {
		if parsed, _, err := mime.ParseMediaType(image.mediaType); err == nil && strings.HasPrefix(parsed, "image/") {
			mediaType = parsed
		}
	}
	var size int64
	if image.base64 {
		// Count decoded bytes without allocating another copy of the image.
		if n, err := io.Copy(io.Discard, base64.NewDecoder(base64.StdEncoding, strings.NewReader(image.data))); err == nil {
			size = n
		}
	} else if decoded, err := url.PathUnescape(image.data); err == nil {
		size = int64(len(decoded))
	}
	metadata := struct {
		Kind      string `json:"kind"`
		MediaType string `json:"media_type"`
		Bytes     int64  `json:"bytes"`
	}{"image", mediaType, size}
	b, _ := json.Marshal(metadata)
	// Use the same description convention as oversized full-content images,
	// but never describeImageURL: it intentionally returns small URLs intact.
	return "[image: " + string(b) + "]"
}

func imageURLInfo(ref string) imageInfo {
	if len(ref) < 5 || !strings.EqualFold(ref[:5], "data:") {
		return imageInfo{}
	}
	header, data, ok := strings.Cut(ref[5:], ",")
	if !ok {
		return imageInfo{}
	}
	mediaType, _, _ := strings.Cut(header, ";")
	return imageInfo{mediaType: mediaType, data: data, base64: strings.HasSuffix(strings.ToLower(header), ";base64")}
}

func imagePartInfo(part map[string]any, allowImplicit bool) (imageInfo, bool) {
	typ, _ := part["type"].(string)
	switch typ {
	case "image", "image_url", "input_image", "output_image", "image_file", "computer_screenshot":
	case "":
		if !allowImplicit {
			return imageInfo{}, false
		}
	default:
		return imageInfo{}, false
	}
	// A type label or property name alone is not an image. Require an actual
	// reference/data value, never a JSON Schema declaration of that field.
	if ref, ok := part["image_url"]; ok {
		if info, valid := imageReferenceInfo(ref); valid {
			return info, true
		}
	}
	if typ != "" {
		for _, key := range []string{"source", "image_file"} {
			if source, ok := part[key].(map[string]any); ok {
				if info, valid := imageSourceInfo(source); valid {
					return info, true
				}
			}
		}
		return imageSourceInfo(part)
	}
	// Gemini's REST and SDK spellings, including remotely referenced images.
	for _, key := range []string{"inline_data", "inlineData", "file_data", "fileData"} {
		if source, ok := part[key].(map[string]any); ok {
			mediaType := imageMediaType(source)
			if mediaType == "" || strings.HasPrefix(strings.ToLower(mediaType), "image/") {
				if info, valid := imageSourceInfo(source); valid {
					return info, true
				}
			}
		}
	}
	return imageInfo{}, false
}

func imageReferenceInfo(ref any) (imageInfo, bool) {
	if value, ok := ref.(string); ok && value != "" {
		return imageURLInfo(value), true
	}
	if source, ok := ref.(map[string]any); ok {
		return imageSourceInfo(source)
	}
	return imageInfo{}, false
}

func imageSourceInfo(source map[string]any) (imageInfo, bool) {
	for _, key := range []string{"url", "file_uri", "fileUri"} {
		if ref, ok := source[key].(string); ok && ref != "" {
			return imageURLInfo(ref), true
		}
	}
	if data, ok := source["data"].(string); ok {
		return imageInfo{mediaType: imageMediaType(source), data: data, base64: true}, true
	}
	if id, ok := source["file_id"].(string); ok && id != "" {
		return imageInfo{}, true
	}
	return imageInfo{}, false
}

func imageMediaType(source map[string]any) string {
	for _, key := range []string{"media_type", "mime_type", "mimeType"} {
		if value, ok := source[key].(string); ok {
			return value
		}
	}
	return ""
}
