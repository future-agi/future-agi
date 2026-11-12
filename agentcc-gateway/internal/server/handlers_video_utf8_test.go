package server

import (
	"bytes"
	"context"
	"image"
	"image/png"
	"mime/multipart"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/video"
)

func TestVideoMultipartFilenameUTF8RefusedBeforeAccept(t *testing.T) {
	f := newVideoHTTP(t)
	form := func(prompt string, reference []byte) (string, string) {
		t.Helper()
		var body bytes.Buffer
		w := multipart.NewWriter(&body)
		if err := w.WriteField("model", videoTestModel); err != nil {
			t.Fatal(err)
		}
		p, err := w.CreateFormFile("prompt", "prompt.txt")
		if err != nil {
			t.Fatal(err)
		}
		if _, err := p.Write([]byte(prompt)); err != nil {
			t.Fatal(err)
		}
		if reference != nil {
			p, err = w.CreateFormFile("input_reference", "frame.png")
			if err != nil {
				t.Fatal(err)
			}
			if _, err := p.Write(reference); err != nil {
				t.Fatal(err)
			}
		}
		if err := w.Close(); err != nil {
			t.Fatal(err)
		}
		return body.String(), w.FormDataContentType()
	}
	body, ct := form("synthetic \xff boat", nil)
	w := f.form(f.keyA, video.NewID(), body, ct)
	if w.Code != 400 || !strings.Contains(w.Body.String(), `"code":"invalid_parameter"`) || !strings.Contains(w.Body.String(), `"param":"prompt"`) || !strings.Contains(w.Body.String(), "UTF-8 text required") {
		t.Error("invalid filename-bearing prompt was not refused", w.Code, w.Body.String())
	}
	jobs, err := f.store.ListAccounting(context.Background())
	if err != nil || len(jobs) != 0 {
		t.Fatal("invalid prompt reached acceptance", len(jobs), err)
	}

	// Only actual input_reference uploads are binary. A valid filename-bearing
	// Unicode prompt and a non-UTF-8 PNG must still pass decoding and ingress.
	var frame bytes.Buffer
	if err := png.Encode(&frame, image.NewRGBA(image.Rect(0, 0, 300, 300))); err != nil {
		t.Fatal(err)
	}
	if utf8.Valid(frame.Bytes()) {
		t.Fatal("image control must contain non-UTF-8 bytes")
	}
	body, ct = form("synthetic paper boat 舟", frame.Bytes())
	w = f.form(f.keyA, video.NewID(), body, ct)
	if w.Code != 202 {
		t.Fatal("valid text/binary upload rejected", w.Code, w.Body.String())
	}
	jobs, err = f.store.ListAccounting(context.Background())
	if err != nil || len(jobs) != 1 {
		t.Fatal("control not accepted exactly once", len(jobs), err)
	}
	if len(jobs[0].Ingress) != 1 || jobs[0].Ingress[0].Digest == "" {
		t.Fatal("binary reference did not reach verified ingress")
	}
}
