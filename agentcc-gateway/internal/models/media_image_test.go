package models

import (
	"bytes"
	"encoding/base64"
	"image"
	"image/png"
	"testing"
)

func TestMeterImageHeaderRejectsAnimatedAndTruncatedPNG(t *testing.T) {
	var b bytes.Buffer
	if err := png.Encode(&b, image.NewRGBA(image.Rect(0, 0, 2, 3))); err != nil {
		t.Fatal(err)
	}
	raw := b.Bytes()
	if w, h, err := MeterImageHeader("image/png", base64.StdEncoding.EncodeToString(raw)); w != 2 || h != 3 || err != nil {
		t.Fatalf("header=%dx%d error=%v", w, h, err)
	}
	// Insert an APNG frame-control chunk after IHDR. DecodeConfig alone would
	// accept the first header, but it is not a qualified single-image envelope.
	animated := append([]byte(nil), raw[:33]...)
	animated = append(animated, []byte{0, 0, 0, 0, 'a', 'c', 'T', 'L', 0, 0, 0, 0}...)
	animated = append(animated, raw[33:]...)
	for _, data := range [][]byte{raw[:33], animated, append(append([]byte(nil), raw...), 0)} {
		if _, _, err := MeterImageHeader("image/png", base64.StdEncoding.EncodeToString(data)); err == nil {
			t.Fatal("unqualified image envelope accepted")
		}
	}
}
