package models

import (
	"bytes"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"image"
	_ "image/jpeg"
	_ "image/png"
)

// MeterImageHeader reads bounded PNG/JPEG headers, not pixels. The result is
// evidence of dimensions only, NOT proof of a complete decodable image or of a
// vendor tariff. Animated/other formats and MIME mismatches fail closed.
func MeterImageHeader(mime, encoded string) (width, height int, err error) {
	if len(encoded) == 0 || len(encoded) > 8*1024*1024 {
		return 0, 0, fmt.Errorf("image exceeds metering byte bound")
	}
	raw, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return 0, 0, fmt.Errorf("invalid image encoding")
	}
	cfg, format, err := image.DecodeConfig(bytes.NewReader(raw))
	if err != nil || (format != "jpeg" && format != "png") || mime != "image/"+format || cfg.Width <= 0 || cfg.Height <= 0 || cfg.Width > 16384 || cfg.Height > 16384 || int64(cfg.Width)*int64(cfg.Height) > 64_000_000 {
		return 0, 0, fmt.Errorf("unsupported image header or dimensions")
	}
	if (format == "png" && !staticPNGEnvelope(raw)) || (format == "jpeg" && bytes.Contains(raw, []byte("MPF\x00"))) {
		return 0, 0, fmt.Errorf("multi-frame or incomplete image envelope")
	}
	return cfg.Width, cfg.Height, nil
}

// PNG's DecodeConfig stops at IHDR, before APNG's frame controls. Check chunk
// framing without expanding pixels; an animation must not be counted as one.
func staticPNGEnvelope(raw []byte) bool {
	for off := 8; off+12 <= len(raw); {
		n := int64(binary.BigEndian.Uint32(raw[off : off+4]))
		if n > int64(len(raw)-off-12) {
			return false
		}
		kind := string(raw[off+4 : off+8])
		if kind == "acTL" || kind == "fcTL" || kind == "fdAT" {
			return false
		}
		off += int(n) + 12
		if kind == "IEND" {
			return n == 0 && off == len(raw)
		}
	}
	return false
}
