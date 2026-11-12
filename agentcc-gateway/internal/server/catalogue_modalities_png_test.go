package server

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"image"
	"image/png"
	"strings"
	"testing"
)

// Encode actual pixels without compression so this remains a valid PNG whose
// base64 SSE event exceeds 1 MiB, regardless of future encoder improvements.
func cat2LargePNG(t *testing.T) []byte {
	t.Helper()
	img := image.NewRGBA(image.Rect(0, 0, 1024, 1024))
	for i := 0; i < len(img.Pix); i += 4 {
		img.Pix[i] = byte(i / 4)
		img.Pix[i+1] = byte(i / 4096)
		img.Pix[i+2] = byte(i / 16)
		img.Pix[i+3] = 255
	}
	var encoded bytes.Buffer
	encoder := png.Encoder{CompressionLevel: png.NoCompression}
	if err := encoder.Encode(&encoded, img); err != nil {
		t.Fatal(err)
	}
	decoded, err := png.Decode(bytes.NewReader(encoded.Bytes()))
	if err != nil || decoded.Bounds() != img.Bounds() {
		t.Fatalf("invalid PNG fixture: %v", err)
	}
	t.Logf("valid PNG fixture: width=1024 height=1024 bytes=%d", encoded.Len())
	return encoded.Bytes()
}

func cat2DecodedImage(t *testing.T, data []byte) []byte {
	t.Helper()
	var image struct {
		URL struct {
			Data string `json:"url"`
		} `json:"image_url"`
	}
	if err := json.Unmarshal(data, &image); err != nil {
		t.Fatal(err)
	}
	const prefix = "data:image/png;base64,"
	if !strings.HasPrefix(image.URL.Data, prefix) {
		t.Fatal("image payload has no PNG data URL")
	}
	payload, err := base64.StdEncoding.DecodeString(strings.TrimPrefix(image.URL.Data, prefix))
	if err != nil {
		t.Fatal("image payload is not base64")
	}
	return payload
}
