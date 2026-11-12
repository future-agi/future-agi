package contentbounds

import (
	"io"
	"unicode/utf8"
)

func syntaxEnd(err error) error {
	if err == io.EOF {
		return refuse(CodeJSON)
	}
	return err
}

// stringToken validates UTF-8 and escapes incrementally. Only object keys are
// retained, canonically decoded for semantic equality. Scalar strings allocate
// no buffer. No Unicode normalization/case folding is applied: JSON member
// names compare by decoded code points, not by visual similarity.
func (in *input) stringToken(key bool) (string, error) {
	b, err := in.required()
	if err != nil {
		return "", err
	}
	if b != '"' {
		return "", refuse(CodeJSON)
	}
	var decoded []byte
	for {
		b, err = in.required()
		if err != nil {
			return "", err
		}
		if b == '"' {
			return string(decoded), nil
		}
		if b < 0x20 {
			return "", refuse(CodeJSON)
		}
		var buf [utf8.UTFMax]byte
		buf[0] = b
		n := 1
		if b == '\\' {
			r, err := in.escape()
			if err != nil {
				return "", err
			}
			n = utf8.EncodeRune(buf[:], r)
		} else if b >= utf8.RuneSelf {
			switch {
			case b >= 0xc2 && b <= 0xdf:
				n = 2
			case b >= 0xe0 && b <= 0xef:
				n = 3
			case b >= 0xf0 && b <= 0xf4:
				n = 4
			default:
				return "", refuse(CodeJSON)
			}
			for i := 1; i < n; i++ {
				buf[i], err = in.required()
				if err != nil {
					return "", err
				}
			}
			_, size := utf8.DecodeRune(buf[:n])
			if size != n {
				return "", refuse(CodeJSON)
			}
		}
		if key {
			decoded, err = appendKey(decoded, buf[:n], int(in.limit))
			if err != nil {
				return "", err
			}
		}
	}
}

// Clamp capacity growth BEFORE allocation. Decoded key bytes cannot exceed
// wire bytes; live key sets and their metadata are bounded by read/node caps.
// Slice/map runtime overhead is not promised to equal the wire byte limit.
func appendKey(dst, piece []byte, limit int) ([]byte, error) {
	if len(piece) > limit-len(dst) {
		return nil, refuse(CodeReadBytes)
	}
	need := len(dst) + len(piece)
	if need > cap(dst) {
		capacity := cap(dst)
		if capacity == 0 {
			capacity = min(32, limit)
		} else if capacity <= limit-capacity {
			capacity *= 2
		} else {
			capacity = limit
		}
		capacity = max(capacity, need)
		next := make([]byte, len(dst), capacity)
		copy(next, dst)
		dst = next
	}
	return append(dst, piece...), nil
}

func (in *input) escape() (rune, error) {
	b, err := in.required()
	if err != nil {
		return 0, err
	}
	switch b {
	case '"', '\\', '/':
		return rune(b), nil
	case 'b':
		return '\b', nil
	case 'f':
		return '\f', nil
	case 'n':
		return '\n', nil
	case 'r':
		return '\r', nil
	case 't':
		return '\t', nil
	case 'u':
		u, err := in.hexRune()
		if err != nil {
			return 0, err
		}
		if u >= 0xdc00 && u <= 0xdfff {
			return 0, refuse(CodeJSON)
		}
		if u < 0xd800 || u > 0xdbff {
			return u, nil
		}
		for _, want := range [...]byte{'\\', 'u'} {
			b, err = in.required()
			if err != nil {
				return 0, err
			}
			if b != want {
				return 0, refuse(CodeJSON)
			}
		}
		low, err := in.hexRune()
		if err != nil {
			return 0, err
		}
		if low < 0xdc00 || low > 0xdfff {
			return 0, refuse(CodeJSON)
		}
		return 0x10000 + ((u - 0xd800) << 10) + low - 0xdc00, nil
	default:
		return 0, refuse(CodeJSON)
	}
}

func (in *input) hexRune() (rune, error) {
	var value rune
	for i := 0; i < 4; i++ {
		b, err := in.required()
		if err != nil {
			return 0, err
		}
		var digit byte
		switch {
		case b >= '0' && b <= '9':
			digit = b - '0'
		case b >= 'a' && b <= 'f':
			digit = b - 'a' + 10
		case b >= 'A' && b <= 'F':
			digit = b - 'A' + 10
		default:
			return 0, refuse(CodeJSON)
		}
		value = value*16 + rune(digit)
	}
	return value, nil
}

func (in *input) literal(want string) error {
	for i := range want {
		b, err := in.required()
		if err != nil {
			return err
		}
		if b != want[i] {
			return refuse(CodeJSON)
		}
	}
	return nil
}

func (in *input) digits() error {
	seen := false
	for {
		b, err := in.peek()
		if err != nil && err != io.EOF {
			return err
		}
		if err == io.EOF || b < '0' || b > '9' {
			if !seen {
				return refuse(CodeJSON)
			}
			return nil
		}
		_, _ = in.take() // peek succeeded; no new reader call.
		seen = true
	}
}

// Numbers are lexical only: no strconv conversion, arbitrary-precision
// allocation, floating-point rounding, or numeric overflow is involved.
func (in *input) number() error {
	b, err := in.peek()
	if err != nil {
		return syntaxEnd(err)
	}
	if b == '-' {
		_, _ = in.take()
		b, err = in.peek()
		if err != nil {
			return syntaxEnd(err)
		}
	}
	if b == '0' {
		_, _ = in.take()
	} else if b >= '1' && b <= '9' {
		if err := in.digits(); err != nil {
			return err
		}
	} else {
		return refuse(CodeJSON)
	}
	b, err = in.peek()
	if err == io.EOF {
		return nil
	}
	if err != nil {
		return err
	}
	if b == '.' {
		_, _ = in.take()
		if err := in.digits(); err != nil {
			return err
		}
		b, err = in.peek()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
	}
	if b == 'e' || b == 'E' {
		_, _ = in.take()
		b, err = in.peek()
		if err != nil {
			return syntaxEnd(err)
		}
		if b == '+' || b == '-' {
			_, _ = in.take()
		}
		return in.digits()
	}
	return nil
}
