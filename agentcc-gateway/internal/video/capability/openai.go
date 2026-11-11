package capability

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"mime"
	"mime/multipart"
	"net/http"
	"strconv"
	"strings"
	"unicode/utf8"

	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
)

// WireOpenAI marks jobs created through the OpenAI Videos API shape. Their
// responses use OpenAI's status enum; native jobs keep the gateway contract.
const WireOpenAI = "openai"

// OpenAICreate is the OpenAI Videos API create request (POST /v1/videos), as
// sent by the official SDKs as multipart/form-data or by JSON clients. The
// gateway extensions audio, resolution and aspect_ratio let callers reach
// provider settings that seconds/size cannot express.
type OpenAICreate struct {
	secondsPresent               bool
	Model, Prompt, Seconds, Size string
	Resolution, AspectRatio      string
	Audio                        *bool
	InputReference               *video.Input
}

var openAIFields = map[string]bool{"model": true, "prompt": true, "seconds": true, "size": true, "input_reference": true, "audio": true, "resolution": true, "aspect_ratio": true}

// IsOpenAIJSON reports whether a JSON body uses an OpenAI-only field. Native
// requests reject these fields as unknown, so routing them here cannot change
// the meaning of any request the native contract accepted.
func IsOpenAIJSON(body []byte) bool {
	var fields map[string]json.RawMessage
	if json.Unmarshal(body, &fields) != nil {
		return false
	}
	for _, k := range []string{"seconds", "size", "input_reference"} {
		if _, ok := fields[k]; ok {
			return true
		}
	}
	return false
}

// DecodeOpenAIJSON accepts the OpenAI JSON create shape. input_reference may be
// {"image_url": "<https URL or data:image/...;base64 URL>"}; file ids are not
// supported because the gateway has no OpenAI Files namespace for videos.
func DecodeOpenAIJSON(body []byte) (OpenAICreate, error) {
	var raw map[string]json.RawMessage
	if len(bytes.TrimSpace(body)) == 0 || bytes.TrimSpace(body)[0] != '{' || json.Unmarshal(body, &raw) != nil {
		return OpenAICreate{}, invalid("invalid_request", "body", "object required")
	}
	// json.Unmarshal replaces invalid UTF-8 with U+FFFD instead of failing.
	if !utf8.Valid(body) {
		return OpenAICreate{}, invalid("invalid_request", "body", "UTF-8 JSON required")
	}
	if k, dup := duplicateKey(body); dup {
		return OpenAICreate{}, invalid("invalid_parameter", k, "field repeated")
	}
	var c OpenAICreate
	for k, v := range raw {
		if !openAIFields[k] {
			return c, invalid("unknown_field", k, "unknown field")
		}
		// Unmarshalling null into a bool or string is a silent no-op, so an
		// explicit null would read as false or "" (audio off, default size).
		// Omit a field to get its default; null is never a value here.
		if bytes.Equal(bytes.TrimSpace(v), []byte("null")) {
			return c, invalid("invalid_parameter", k, "null is not allowed; omit the field to use the default")
		}
		if k == "input_reference" {
			// encoding/json matches struct field names case-insensitively. Check
			// exact keys first so aliases cannot override a validated image URL.
			var fields map[string]json.RawMessage
			if json.Unmarshal(v, &fields) != nil {
				return c, invalid("invalid_parameter", "input_reference", "image_url object required")
			}
			for name := range fields {
				if name != "image_url" && name != "file_id" {
					return c, invalid("invalid_parameter", "input_reference", "image_url object required")
				}
			}
			var ref struct {
				ImageURL string `json:"image_url"`
				FileID   string `json:"file_id"`
			}
			d := json.NewDecoder(bytes.NewReader(v))
			d.DisallowUnknownFields()
			if d.Decode(&ref) != nil || ref.FileID != "" || ref.ImageURL == "" {
				return c, invalid("invalid_parameter", "input_reference", "image_url object required")
			}
			in, err := referenceURL(ref.ImageURL)
			if err != nil {
				return c, err
			}
			c.InputReference = &in
			continue
		}
		if k == "audio" {
			var b bool
			if json.Unmarshal(v, &b) != nil {
				return c, invalid("invalid_parameter", "audio", "boolean required")
			}
			c.Audio = &b
			continue
		}
		s, err := scalar(v)
		if err != nil {
			return c, invalid("invalid_parameter", k, "string required")
		}
		c.set(k, s)
	}
	return c, nil
}

// duplicateKey reports the path of a repeated key in any object, including
// objects in arrays. Callers first validate the JSON syntax. Walking tokens
// preserves every occurrence instead of collapsing repeated containers to maps.
func duplicateKey(body []byte) (string, bool) {
	d := json.NewDecoder(bytes.NewReader(body))
	d.UseNumber()
	return duplicateValue(d)
}

func duplicateValue(d *json.Decoder) (string, bool) {
	t, err := d.Token()
	if err != nil {
		return "", false
	}
	switch t {
	case json.Delim('{'):
		seen := map[string]bool{}
		for d.More() {
			t, err := d.Token()
			k, ok := t.(string)
			if err != nil || !ok {
				return "", false
			}
			if seen[k] {
				return k, true
			}
			seen[k] = true
			if nested, dup := duplicateValue(d); dup {
				return k + "." + nested, true
			}
		}
		_, _ = d.Token() // closing brace; syntax was already validated
	case json.Delim('['):
		for i := 0; d.More(); i++ {
			if nested, dup := duplicateValue(d); dup {
				return strconv.Itoa(i) + "." + nested, true
			}
		}
		_, _ = d.Token() // closing bracket
	}
	return "", false
}

// DecodeOpenAIMultipart parses the multipart body the OpenAI SDKs send. Each
// field may appear once; unknown fields are rejected like native JSON.
func DecodeOpenAIMultipart(body []byte, contentType string) (OpenAICreate, error) {
	var c OpenAICreate
	mt, params, err := mime.ParseMediaType(contentType)
	if err != nil || mt != "multipart/form-data" || params["boundary"] == "" {
		return c, invalid("invalid_request", "body", "multipart boundary required")
	}
	r := multipart.NewReader(bytes.NewReader(body), params["boundary"])
	seen := map[string]bool{}
	for {
		p, err := r.NextPart()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return c, invalid("invalid_request", "body", "invalid multipart body")
		}
		name := p.FormName()
		if !openAIFields[name] {
			return c, invalid("unknown_field", name, "unknown field")
		}
		if seen[name] {
			return c, invalid("invalid_parameter", name, "field repeated")
		}
		seen[name] = true
		value, err := io.ReadAll(p)
		if err != nil {
			return c, invalid("invalid_request", "body", "invalid multipart body")
		}
		// Text fields must be UTF-8; only an uploaded input_reference file is binary.
		if (name != "input_reference" || p.FileName() == "") && !utf8.Valid(value) {
			return c, invalid("invalid_parameter", name, "UTF-8 text required")
		}
		switch name {
		case "input_reference":
			if p.FileName() == "" {
				in, err := referenceURL(string(value))
				if err != nil {
					return c, err
				}
				c.InputReference = &in
				continue
			}
			// SDKs label uploads from the filename or as octet-stream, so the
			// bytes decide the media type; ingress verifies them again.
			mediaType := sniffImage(value)
			if mediaType == "" {
				return c, invalid("unsupported_combination", "input_reference", "image file required")
			}
			c.InputReference = &video.Input{Role: video.FirstFrame, MediaType: mediaType, Source: video.Source{Data: base64.StdEncoding.EncodeToString(value)}}
		case "audio":
			b, err := strconv.ParseBool(strings.TrimSpace(string(value)))
			if err != nil {
				return c, invalid("invalid_parameter", "audio", "boolean required")
			}
			c.Audio = &b
		default:
			c.set(name, string(value))
		}
	}
	return c, nil
}
func (c *OpenAICreate) set(k, v string) {
	switch k {
	case "model":
		c.Model = v
	case "prompt":
		c.Prompt = v
	case "seconds":
		c.secondsPresent = true
		c.Seconds = v
	case "size":
		c.Size = v
	case "resolution":
		c.Resolution = v
	case "aspect_ratio":
		c.AspectRatio = v
	}
}
func scalar(v json.RawMessage) (string, error) {
	var s string
	if json.Unmarshal(v, &s) == nil {
		return s, nil
	}
	var n json.Number
	d := json.NewDecoder(bytes.NewReader(v))
	d.UseNumber()
	if d.Decode(&n) == nil {
		return n.String(), nil
	}
	return "", fmt.Errorf("not a scalar")
}
func referenceURL(raw string) (video.Input, error) {
	raw = strings.TrimSpace(raw)
	if rest, ok := strings.CutPrefix(raw, "data:"); ok {
		meta, data, ok := strings.Cut(rest, ",")
		mediaType, isBase64 := strings.CutSuffix(meta, ";base64")
		if !ok || !isBase64 || !strings.HasPrefix(mediaType, "image/") {
			return video.Input{}, invalid("invalid_parameter", "input_reference", "base64 image data URL required")
		}
		return video.Input{Role: video.FirstFrame, MediaType: mediaType, Source: video.Source{Data: data}}, nil
	}
	if !strings.HasPrefix(raw, "https://") {
		return video.Input{}, invalid("invalid_parameter", "input_reference", "https URL or data URL required")
	}
	// The media type is confirmed by ingress sniffing before acceptance.
	return video.Input{Role: video.FirstFrame, MediaType: "image/" + imageFormatHint(raw), Source: video.Source{URL: raw}}, nil
}
func sniffImage(b []byte) string {
	if len(b) >= 12 && string(b[:4]) == "RIFF" && string(b[8:12]) == "WEBP" {
		return "image/webp"
	}
	switch t := http.DetectContentType(b); t {
	case "image/png", "image/jpeg", "image/gif":
		return t
	}
	return ""
}
func imageFormatHint(u string) string {
	path := strings.ToLower(strings.SplitN(strings.SplitN(u, "?", 2)[0], "#", 2)[0])
	switch {
	case strings.HasSuffix(path, ".png"):
		return "png"
	case strings.HasSuffix(path, ".webp"):
		return "webp"
	}
	return "jpeg"
}

var ratios = []struct {
	name string
	w, h float64
}{{"16:9", 16, 9}, {"9:16", 9, 16}, {"1:1", 1, 1}, {"4:3", 4, 3}, {"3:4", 3, 4}, {"21:9", 21, 9}, {"9:21", 9, 21}, {"3:2", 3, 2}, {"2:3", 2, 3}}
var resolutions = []struct {
	name  string
	short int
}{{"480p", 480}, {"720p", 720}, {"768p", 768}, {"1080p", 1080}, {"4k", 2160}}

// ParseSize maps an exact canonical WIDTHxHEIGHT (as SizeFor renders it, for
// example 854x480 or 1280x720) onto a resolution tier and aspect ratio. Any
// other size is rejected rather than silently rounded to a different output.
func ParseSize(size string) (resolution, aspect string, err error) {
	a, b, ok := strings.Cut(strings.ToLower(strings.TrimSpace(size)), "x")
	w, e1 := strconv.Atoi(a)
	h, e2 := strconv.Atoi(b)
	if !ok || e1 != nil || e2 != nil || w <= 0 || h <= 0 {
		return "", "", invalid("invalid_parameter", "size", "WIDTHxHEIGHT required")
	}
	want := fmt.Sprintf("%dx%d", w, h)
	for _, r := range resolutions {
		for _, q := range ratios {
			if SizeFor(r.name, q.name) == want {
				return r.name, q.name, nil
			}
		}
	}
	return "", "", invalid("unsupported_combination", "size", "size is not a supported resolution and aspect ratio")
}

// SizeFor renders the canonical WIDTHxHEIGHT for a resolution tier and aspect
// ratio, rounding the long side to an even pixel count.
func SizeFor(resolution, aspect string) string {
	short := 0
	for _, r := range resolutions {
		if r.name == resolution {
			short = r.short
		}
	}
	for _, r := range ratios {
		if r.name == aspect && short > 0 {
			long := int(math.Round(float64(short)*math.Max(r.w, r.h)/math.Min(r.w, r.h)/2) * 2)
			if r.w >= r.h {
				return fmt.Sprintf("%dx%d", long, short)
			}
			return fmt.Sprintf("%dx%d", short, long)
		}
	}
	return ""
}

// Request maps the OpenAI shape onto the gateway request: seconds ->
// duration_seconds, size -> resolution + aspect_ratio, input_reference ->
// a first_frame input. Conflicting size and explicit tier/ratio fail closed.
func (c OpenAICreate) Request() (Request, error) {
	req := Request{Model: c.Model, Prompt: c.Prompt, Resolution: c.Resolution, AspectRatio: c.AspectRatio, Audio: c.Audio}
	if c.secondsPresent || c.Seconds != "" {
		n, err := strconv.Atoi(strings.TrimSpace(c.Seconds))
		if err != nil || n <= 0 {
			return Request{}, invalid("invalid_parameter", "seconds", "positive integer seconds required")
		}
		req.DurationSeconds = float64(n)
	}
	if c.Size != "" {
		resolution, aspect, err := ParseSize(c.Size)
		if err != nil {
			return Request{}, err
		}
		if c.Resolution != "" && c.Resolution != resolution || c.AspectRatio != "" && c.AspectRatio != aspect {
			return Request{}, invalid("unsupported_combination", "size", "size conflicts with resolution or aspect_ratio")
		}
		req.Resolution, req.AspectRatio = resolution, aspect
	}
	if c.InputReference != nil {
		req.Inputs = []Input{*c.InputReference}
	}
	return req, nil
}
