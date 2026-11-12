package videotest

import (
	"bytes"
	"encoding/binary"
	"image"
	"image/color"
	"image/png"
)

// PNG creates synthetic media; it contains no customer data.
func PNG() []byte {
	im := image.NewRGBA(image.Rect(0, 0, 64, 64))
	im.Set(0, 0, color.White)
	var b bytes.Buffer
	_ = png.Encode(&b, im)
	return b.Bytes()
}

// MP4 is a container-only fixture, not a playable encoded video.
func MP4() []byte {
	var b bytes.Buffer
	for _, atom := range []struct {
		name string
		data []byte
	}{{"ftyp", []byte("isom\x00\x00\x00\x00isom")}, {"moov", nil}} {
		_ = binary.Write(&b, binary.BigEndian, uint32(8+len(atom.data)))
		b.WriteString(atom.name)
		b.Write(atom.data)
	}
	return b.Bytes()
}
