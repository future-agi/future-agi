package media

import (
	"bytes"
	"context"
	"encoding/binary"
	"fmt"
	"image"
	"image/color"
	"image/gif"
	"image/jpeg"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
)

func TestVerify_Headers(t *testing.T) {
	var jpg, g bytes.Buffer
	jpeg.Encode(&jpg, image.NewRGBA(image.Rect(0, 0, 32, 16)), nil)
	gif.Encode(&g, image.NewRGBA(image.Rect(0, 0, 32, 16)), nil)
	webp := append([]byte("RIFF\x16\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00"), []byte{0, 0, 0, 0, 31, 0, 0, 15, 0, 0}...)
	wav := []byte("RIFF\x28\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x40\x1f\x00\x00\x80\x3e\x00\x00\x02\x00\x10\x00data\x04\x00\x00\x00\x00\x00\x00\x00")
	for _, tc := range []struct {
		name, mime string
		data       []byte
		w, h       int
	}{{"png", "image/png", videotest.PNG(), 64, 64}, {"jpeg", "image/jpeg", jpg.Bytes(), 32, 16}, {"gif", "image/gif", g.Bytes(), 32, 16}, {"webp", "image/webp", webp, 32, 16}, {"mp4", "video/mp4", videotest.MP4(), 0, 0}, {"wav", "audio/wav", wav, 0, 0}, {"mp3", "audio/mpeg", []byte{0xff, 0xfb, 0x90, 0x00, 0, 0, 0, 0}, 0, 0}} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := NewFetcher(nil).Verify(context.Background(), bytes.NewReader(tc.data), tc.mime, "", limits())
			if err != nil || got.Width != tc.w || got.Height != tc.h {
				t.Fatal(got, err)
			}
		})
	}
}
func TestVerify_InvalidAndDimensionLimits(t *testing.T) {
	for _, tc := range []struct {
		name, mime string
		data       []byte
	}{{"truncated", "image/png", videotest.PNG()[:15]}, {"empty", "video/mp4", nil}, {"no_moov", "video/mp4", videotest.MP4()[:20]}, {"bogus", "audio/mpeg", []byte("ID3garbage")}, {"ftyp_in_text", "video/mp4", []byte("this is ftyp isom moov but is not media")}} {
		t.Run(tc.name, func(t *testing.T) {
			_, err := NewFetcher(nil).Verify(context.Background(), bytes.NewReader(tc.data), tc.mime, "", limits())
			if err == nil {
				t.Fatal("accepted invalid media")
			}
		})
	}
	l := limits()
	l.MaxPixels = 100
	_, err := NewFetcher(nil).Verify(context.Background(), bytes.NewReader(videotest.PNG()), "image/png", "", l)
	requireCode(t, err, 400, "media_dimensions_invalid")
	// A gigantic box must not trigger allocation or acceptance of truncated input.
	box := make([]byte, 16)
	binary.BigEndian.PutUint32(box, 1)
	copy(box[4:], "ftyp")
	binary.BigEndian.PutUint64(box[8:], 1<<62)
	_, err = NewFetcher(nil).Verify(context.Background(), bytes.NewReader(box), "video/mp4", "", limits())
	if err == nil {
		t.Fatal("truncated box accepted")
	}
}

func TestVerify_GIFFrameCap(t *testing.T) {
	var b bytes.Buffer
	im := image.NewPaletted(image.Rect(0, 0, 2, 2), []color.Color{color.Black, color.White})
	if err := gif.EncodeAll(&b, &gif.GIF{Image: []*image.Paletted{im, im}, Delay: []int{1, 1}}); err != nil {
		t.Fatal(err)
	}
	_, err := NewFetcher(nil).Verify(context.Background(), bytes.NewReader(b.Bytes()), "image/gif", "", limits())
	requireCode(t, err, 400, "media_invalid")
}
func TestVerify_TruncatedMoov(t *testing.T) {
	b := videotest.MP4()
	binary.BigEndian.PutUint32(b[len(b)-8:], 100)
	_, err := NewFetcher(nil).Verify(context.Background(), bytes.NewReader(b), "video/mp4", "", limits())
	if err == nil {
		t.Fatal("truncated moov accepted")
	}
}
func TestVerify_WebPLosslessShortHeader(t *testing.T) {
	b := append([]byte("RIFF\x12\x00\x00\x00WEBPVP8L\x05\x00\x00\x00"), 0x2f, 0x1f, 0xc0, 3, 0, 0)
	got, err := NewFetcher(nil).Verify(context.Background(), bytes.NewReader(b), "image/webp", "", limits())
	if err != nil || got.Width != 32 || got.Height != 16 {
		t.Fatal(got, err)
	}
}

func TestVerify_MovieDurationAndWebPZeroDimensions(t *testing.T) {
	for _, version := range []byte{0, 1} {
		t.Run(fmt.Sprint(version), func(t *testing.T) {
			body := make([]byte, 20)
			body[0] = version
			if version == 0 {
				binary.BigEndian.PutUint32(body[12:], 1000)
				binary.BigEndian.PutUint32(body[16:], 5000)
			} else {
				body = make([]byte, 32)
				body[0] = 1
				binary.BigEndian.PutUint32(body[20:], 1000)
				binary.BigEndian.PutUint64(body[24:], 5000)
			}
			atom := func(kind string, p []byte) []byte {
				h := make([]byte, 8)
				binary.BigEndian.PutUint32(h, uint32(len(p)+8))
				copy(h[4:], kind)
				return append(h, p...)
			}
			data := append(atom("ftyp", []byte("qt  \x00\x00\x00\x00")), atom("moov", atom("mvhd", body))...)
			got, err := NewFetcher(nil).Verify(context.Background(), bytes.NewReader(data), "video/quicktime", "", limits())
			if err != nil || got.ContentType != "video/quicktime" || got.DurationSeconds != 5 {
				t.Fatal(got, err)
			}
		})
	}
	data := append([]byte("RIFF\x16\x00\x00\x00WEBPVP8 \x0a\x00\x00\x00"), []byte{0, 0, 0, 0x9d, 1, 0x2a, 0, 0, 0, 0}...)
	_, err := NewFetcher(nil).Verify(context.Background(), bytes.NewReader(data), "image/webp", "", limits())
	if err == nil {
		t.Fatal("zero dimensions accepted")
	}
}
