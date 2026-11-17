package contentbounds

import "io"

// Each reader request is <= 256 bytes, and TOTAL bytes requested successfully
// can consume at most MaxReadBytes+1. The extra byte distinguishes exact EOF
// from an over-limit document. There is no bufio prefetch outside this budget.
// Reader errors (even alongside data) are normalized without retaining causes.
const readChunk = 256

type input struct {
	r                 io.Reader
	buf               [readChunk]byte
	pos, end          int
	read, used, limit int64
	eof               bool
}

func (in *input) peek() (byte, error) {
	if in.pos < in.end {
		return in.buf[in.pos], nil
	}
	if in.eof {
		return 0, io.EOF
	}
	// limit is validated <= maxInt-1, read <= limit on this path.
	size := int(min(int64(len(in.buf)), in.limit-in.read+1))
	for empty := 0; empty < 100; empty++ {
		n, err := in.r.Read(in.buf[:size])
		if n < 0 || n > size {
			return 0, refuse(CodeReader)
		}
		if err != nil && err != io.EOF {
			return 0, refuse(CodeReader)
		}
		in.read += int64(n)
		if in.read > in.limit {
			return 0, refuse(CodeReadBytes)
		}
		in.eof = err == io.EOF
		in.pos, in.end = 0, n
		if n > 0 {
			return in.buf[0], nil
		}
		if in.eof {
			return 0, io.EOF
		}
	}
	return 0, refuse(CodeReader)
}

func (in *input) take() (byte, error) {
	b, err := in.peek()
	if err != nil {
		return 0, err
	}
	in.pos++
	in.used++
	return b, nil
}

// required converts only ordinary end-of-input to a syntax refusal. A
// transport error or byte-limit refusal must keep its fixed error class.
func (in *input) required() (byte, error) {
	b, err := in.take()
	if err == io.EOF {
		return 0, refuse(CodeJSON)
	}
	return b, err
}

func (in *input) space() error {
	for {
		b, err := in.peek()
		if err != nil {
			return err
		}
		switch b {
		case ' ', '\t', '\n', '\r':
			in.pos++
			in.used++
		default:
			return nil
		}
	}
}
