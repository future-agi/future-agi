package capability

import (
	"bytes"
	"encoding/base64"
	"errors"
	"mime/multipart"
	"testing"
	"unicode/utf8"
)

func multipartTextPart(t *testing.T, name, value, filename string) ([]byte, string) {
	t.Helper()
	var body bytes.Buffer
	w := multipart.NewWriter(&body)
	for key, value := range map[string]string{"model": "openrouter/bytedance/seedance-1-5-pro", "prompt": "synthetic paper boat"} {
		if key != name {
			if err := w.WriteField(key, value); err != nil {
				t.Fatal(err)
			}
		}
	}
	if filename == "" {
		if err := w.WriteField(name, value); err != nil {
			t.Fatal(err)
		}
	} else {
		p, err := w.CreateFormFile(name, filename)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := p.Write([]byte(value)); err != nil {
			t.Fatal(err)
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return body.Bytes(), w.FormDataContentType()
}

// R3-F2: filenames do not make scalar fields binary. Their values still reach
// c.set/ParseBool as text, so malformed UTF-8 must fail before normalization.
func TestOpenAIMultipartTextUTF8RegardlessOfFilename(t *testing.T) {
	for _, tc := range []struct{ name, value string }{
		{"model", "openrouter/bytedance/seedance-1-5-pro"},
		{"prompt", "synthetic paper boat 舟"},
		{"seconds", "4"},
		{"size", "854x480"},
		{"audio", "false"},
		{"resolution", "480p"},
		{"aspect_ratio", "16:9"},
	} {
		for _, filename := range []string{"", "value.txt"} {
			t.Run(tc.name+"/"+filename, func(t *testing.T) {
				body, ct := multipartTextPart(t, tc.name, tc.value, filename)
				c, err := DecodeOpenAIMultipart(body, ct)
				if err != nil {
					t.Fatal("valid text rejected", err)
				}
				req, err := c.Request()
				if err != nil {
					t.Fatal(err)
				}
				if _, err := NewRegistry().Normalize(req, ""); err != nil {
					t.Fatal("valid control did not normalize", err)
				}
				body, ct = multipartTextPart(t, tc.name, tc.value+"\xff", filename)
				_, err = DecodeOpenAIMultipart(body, ct)
				var ve *ValidationError
				if !errors.As(err, &ve) || ve.Code != "invalid_parameter" || ve.Param != tc.name || ve.Reason != "UTF-8 text required" {
					t.Fatalf("want UTF-8 refusal for %s, got %v", tc.name, err)
				}
			})
		}
	}
}

func TestOpenAIMultipartReferenceTextUTF8AndBinaryFile(t *testing.T) {
	body, ct := multipartTextPart(t, "input_reference", "https://example.com/frame.png\xff", "")
	_, err := DecodeOpenAIMultipart(body, ct)
	var ve *ValidationError
	if !errors.As(err, &ve) || ve.Code != "invalid_parameter" || ve.Param != "input_reference" || ve.Reason != "UTF-8 text required" {
		t.Fatalf("invalid UTF-8 reference text accepted: %v", err)
	}
	if utf8.Valid(pngBytes) {
		t.Fatal("binary control must contain non-UTF-8 bytes")
	}
	body, ct = multipartTextPart(t, "input_reference", string(pngBytes), "frame.png")
	c, err := DecodeOpenAIMultipart(body, ct)
	if err != nil {
		t.Fatal("binary reference rejected", err)
	}
	if c.InputReference == nil || c.InputReference.MediaType != "image/png" || c.InputReference.Source.Data != base64.StdEncoding.EncodeToString(pngBytes) {
		t.Fatalf("binary reference changed: %+v", c.InputReference)
	}
}
