package media

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"image"
	_ "image/gif"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"math"
	"net/http"
	"strings"
)

func inspect(src io.Reader, declared string, l Limits) (Result, error) {
	r := bufio.NewReaderSize(src, 1024)
	head, err := r.Peek(512)
	if err != nil && err != io.EOF {
		return Result{}, err
	}
	typ := http.DetectContentType(head)
	if len(head) >= 12 && string(head[:4]) == "RIFF" && string(head[8:12]) == "WEBP" {
		typ = "image/webp"
	}
	if len(head) >= 8 && string(head[4:8]) == "ftyp" {
		typ = "video/mp4"
		if len(head) >= 12 && string(head[8:12]) == "qt  " {
			typ = "video/quicktime"
		}
	}
	if len(head) >= 4 && validMP3(head[:4]) {
		typ = "audio/mpeg"
	}
	family := func(s string) string { a, _, _ := strings.Cut(s, "/"); return a }
	if family(typ) != family(declared) || !strings.Contains(declared, "/") {
		return Result{}, failure(400, "media_type_mismatch")
	}
	out := Result{ContentType: typ, DecodedCheck: "header_only"}
	switch typ {
	case "image/png", "image/jpeg", "image/gif":
		if typ == "image/gif" {
			head, err = r.Peek(781)
			if err != nil && err != io.EOF {
				return out, err
			}
		}
		cfg, _, e := image.DecodeConfig(bytes.NewReader(head))
		if e != nil { // JPEG metadata can put SOF beyond the sniff buffer.
			cfg, _, e = image.DecodeConfig(r)
		}
		if e != nil {
			return out, failure(400, "media_invalid")
		}
		out.Width, out.Height = cfg.Width, cfg.Height
		if typ == "image/gif" {
			if err = scanGIF(r); err != nil {
				return out, err
			}
		}
	case "image/webp":
		out.Width, out.Height, err = webpDimensions(head)
		if err != nil {
			return out, err
		}
	case "video/mp4", "video/quicktime":
		out.DecodedCheck = "container_only"
		out.DurationSeconds, err = scanMP4(r)
		if err != nil {
			return out, err
		}
	case "audio/wave", "audio/x-wav", "audio/wav":
		out.ContentType = "audio/wav"
		out.DurationSeconds, err = scanWAV(r)
		if err != nil {
			return out, err
		}
	case "audio/mpeg":
		if err = checkMP3(r); err != nil {
			return out, err
		}
	default:
		return out, failure(400, "media_unsupported")
	}
	if strings.HasPrefix(typ, "image/") && (out.Width <= 0 || out.Height <= 0) {
		return out, failure(400, "media_dimensions_invalid")
	}
	if out.Width > 0 {
		w, h := int64(out.Width), int64(out.Height)
		ratio := float64(w) / float64(h)
		if h <= 0 || w > math.MaxInt64/h || l.MaxPixels > 0 && w*h > l.MaxPixels || l.MinDimension > 0 && (w < int64(l.MinDimension) || h < int64(l.MinDimension)) || l.MaxDimension > 0 && (w > int64(l.MaxDimension) || h > int64(l.MaxDimension)) || l.MinRatio > 0 && ratio <= l.MinRatio || l.MaxRatio > 0 && ratio >= l.MaxRatio {
			return out, failure(400, "media_dimensions_invalid")
		}
	}
	_, err = io.Copy(io.Discard, r)
	return out, err
}
func webpDimensions(b []byte) (int, int, error) {
	invalid := failure(400, "media_invalid")
	if len(b) < 25 {
		return 0, 0, invalid
	}
	le24 := func(p []byte) int { return int(p[0]) | int(p[1])<<8 | int(p[2])<<16 }
	switch string(b[12:16]) {
	case "VP8X":
		if len(b) < 30 {
			return 0, 0, invalid
		}
		if binary.LittleEndian.Uint32(b[16:20]) != 10 {
			return 0, 0, invalid
		}
		return 1 + le24(b[24:27]), 1 + le24(b[27:30]), nil
	case "VP8 ":
		if len(b) < 30 {
			return 0, 0, invalid
		}
		if !bytes.Equal(b[23:26], []byte{0x9d, 1, 0x2a}) {
			return 0, 0, invalid
		}
		return int(binary.LittleEndian.Uint16(b[26:28]) & 0x3fff), int(binary.LittleEndian.Uint16(b[28:30]) & 0x3fff), nil
	case "VP8L":
		if b[20] != 0x2f {
			return 0, 0, invalid
		}
		v := binary.LittleEndian.Uint32(b[21:25])
		return int(v&0x3fff) + 1, int((v>>14)&0x3fff) + 1, nil
	}
	return 0, 0, invalid
}
func scanMP4(r io.Reader) (float64, error) {
	ftyp, moov := false, false
	var duration float64
	for {
		var h [16]byte
		_, err := io.ReadFull(r, h[:8])
		if err == io.EOF {
			break
		}
		if err != nil {
			return 0, err
		}
		size := uint64(binary.BigEndian.Uint32(h[:4]))
		header := uint64(8)
		kind := string(h[4:8])
		if size == 1 {
			if _, err = io.ReadFull(r, h[8:]); err != nil {
				return 0, err
			}
			size = binary.BigEndian.Uint64(h[8:])
			header = 16
		}
		if size == 0 {
			if kind == "mdat" && ftyp && moov {
				_, err = io.Copy(io.Discard, r)
				return duration, err
			}
			return 0, failure(400, "media_invalid")
		}
		if size < header || size-header > math.MaxInt64 {
			return 0, failure(400, "media_invalid")
		}
		n := int64(size - header)
		if kind == "ftyp" {
			if n < 8 {
				return 0, failure(400, "media_invalid")
			}
			ftyp = true
		}
		if kind == "moov" {
			moov = true
			lr := &io.LimitedReader{R: r, N: n}
			duration, err = scanMoov(lr)
			if err == nil && lr.N != 0 {
				err = io.ErrUnexpectedEOF
			}
			if err != nil {
				return 0, err
			}
		} else if _, err = io.CopyN(io.Discard, r, n); err != nil {
			return 0, err
		}
	}
	if !ftyp || !moov {
		return 0, failure(400, "media_invalid")
	}
	return duration, nil
}
func scanMoov(r io.Reader) (float64, error) {
	var duration float64
	for {
		var h [8]byte
		_, err := io.ReadFull(r, h[:])
		if err == io.EOF {
			return duration, nil
		}
		if err != nil {
			return 0, err
		}
		n := int64(binary.BigEndian.Uint32(h[:4])) - 8
		if n < 0 {
			return 0, failure(400, "media_invalid")
		}
		if string(h[4:]) == "mvhd" {
			if n < 20 {
				return 0, failure(400, "media_invalid")
			}
			var b [32]byte
			read := int64(20)
			if _, err = io.ReadFull(r, b[:20]); err != nil {
				return 0, err
			}
			scale := binary.BigEndian.Uint32(b[12:16])
			ticks := uint64(binary.BigEndian.Uint32(b[16:20]))
			if b[0] == 1 {
				if n < 32 {
					return 0, failure(400, "media_invalid")
				}
				if _, err = io.ReadFull(r, b[20:]); err != nil {
					return 0, err
				}
				read = 32
				scale = binary.BigEndian.Uint32(b[20:24])
				ticks = binary.BigEndian.Uint64(b[24:32])
			}
			if scale == 0 {
				return 0, failure(400, "media_invalid")
			}
			duration = float64(ticks) / float64(scale)
			n -= read
		}
		if _, err = io.CopyN(io.Discard, r, n); err != nil {
			return 0, err
		}
	}
}
func scanWAV(r io.Reader) (float64, error) {
	var hdr [12]byte
	if _, err := io.ReadFull(r, hdr[:]); err != nil {
		return 0, err
	}
	var rate uint32
	var data int64
	for {
		var h [8]byte
		_, err := io.ReadFull(r, h[:])
		if err == io.EOF {
			break
		}
		if err != nil {
			return 0, err
		}
		n := int64(binary.LittleEndian.Uint32(h[4:]))
		size := n
		if string(h[:4]) == "fmt " {
			if n < 16 {
				return 0, failure(400, "media_invalid")
			}
			var b [16]byte
			if _, err = io.ReadFull(r, b[:]); err != nil {
				return 0, err
			}
			rate = binary.LittleEndian.Uint32(b[8:12])
			if rate == 0 || binary.LittleEndian.Uint16(b[2:4]) == 0 {
				return 0, failure(400, "media_invalid")
			}
			n -= 16
		}
		if string(h[:4]) == "data" {
			data += n
		}
		if _, err = io.CopyN(io.Discard, r, n+size%2); err != nil {
			return 0, err
		}
	}
	if rate == 0 || data == 0 {
		return 0, failure(400, "media_invalid")
	}
	return float64(data) / float64(rate), nil
}
func validMP3(b []byte) bool {
	return len(b) >= 4 && b[0] == 255 && b[1]&0xe0 == 0xe0 && b[1]&0x18 != 8 && b[1]&6 != 0 && b[2]>>4 > 0 && b[2]>>4 < 15 && b[2]&12 != 12
}
func checkMP3(r *bufio.Reader) error {
	h, err := r.Peek(10)
	if err != nil && err != io.EOF {
		return err
	}
	if len(h) >= 10 && string(h[:3]) == "ID3" {
		size := int64(0)
		for _, b := range h[6:10] {
			if b&128 != 0 {
				return failure(400, "media_invalid")
			}
			size = size<<7 | int64(b)
		}
		if _, err = io.CopyN(io.Discard, r, size+10); err != nil {
			return err
		}
	}
	h, err = r.Peek(4)
	if err != nil {
		return err
	}
	if !validMP3(h) {
		return failure(400, "media_invalid")
	}
	return nil
}

// scanGIF counts image descriptors without allocating or decompressing pixels.
func scanGIF(r io.Reader) error {
	invalid := failure(400, "media_invalid")
	var h [13]byte
	if _, err := io.ReadFull(r, h[:]); err != nil {
		return err
	}
	skipTable := func(packed byte) error {
		if packed&128 == 0 {
			return nil
		}
		_, err := io.CopyN(io.Discard, r, int64(3*(1<<((packed&7)+1))))
		return err
	}
	if err := skipTable(h[10]); err != nil {
		return err
	}
	blocks := func() error {
		for {
			var size [1]byte
			if _, err := io.ReadFull(r, size[:]); err != nil {
				return err
			}
			if size[0] == 0 {
				return nil
			}
			if _, err := io.CopyN(io.Discard, r, int64(size[0])); err != nil {
				return err
			}
		}
	}
	frames := 0
	for {
		var tag [1]byte
		if _, err := io.ReadFull(r, tag[:]); err != nil {
			return err
		}
		switch tag[0] {
		case 0x3b:
			if frames != 1 {
				return invalid
			}
			return nil
		case 0x21:
			if _, err := io.ReadFull(r, tag[:]); err != nil {
				return err
			}
			if err := blocks(); err != nil {
				return err
			}
		case 0x2c:
			frames++
			if frames > 1 {
				return invalid
			}
			var d [9]byte
			if _, err := io.ReadFull(r, d[:]); err != nil {
				return err
			}
			if err := skipTable(d[8]); err != nil {
				return err
			}
			if _, err := io.ReadFull(r, tag[:]); err != nil {
				return err
			}
			if err := blocks(); err != nil {
				return err
			}
		default:
			return invalid
		}
	}
}
