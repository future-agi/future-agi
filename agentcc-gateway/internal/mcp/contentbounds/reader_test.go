package contentbounds

import (
	"errors"
	"io"
	"strings"
	"testing"
)

type measuredReader struct {
	r                     io.Reader
	bytes, calls, largest int
	oneByte               bool
}

func (r *measuredReader) Read(p []byte) (int, error) {
	r.calls++
	if len(p) > r.largest {
		r.largest = len(p)
	}
	if r.oneByte && len(p) > 1 {
		p = p[:1]
	}
	n, err := r.r.Read(p)
	r.bytes += n
	return n, err
}

type repeatedByte struct{ b byte }

func (r repeatedByte) Read(p []byte) (int, error) {
	for i := range p {
		p[i] = r.b
	}
	return len(p), nil
}

type failingReader struct {
	withData bool
	calls    int
}

func (r *failingReader) Read(p []byte) (int, error) {
	r.calls++
	if r.withData && r.calls == 1 {
		p[0] = '0'
		return 1, errors.New("private-reader-message")
	}
	return 0, errors.New("private-reader-message")
}

type emptyReader struct{ calls int }

func (r *emptyReader) Read([]byte) (int, error) { r.calls++; return 0, nil }

type badCountReader struct{ negative bool }

func (r badCountReader) Read(p []byte) (int, error) {
	if r.negative {
		return -1, nil
	}
	return len(p) + 1, nil
}

type dataEOFReader struct{ done bool }

func (r *dataEOFReader) Read(p []byte) (int, error) {
	if r.done {
		return 0, io.EOF
	}
	r.done = true
	p[0] = '0'
	return 1, io.EOF
}

func TestReaderFailuresAreValueFree(t *testing.T) {
	for _, r := range []io.Reader{&failingReader{}, &failingReader{withData: true}, badCountReader{}, badCountReader{negative: true}} {
		s, err := Scan(r, designLimits())
		requireCode(t, err, CodeReader)
		if errors.Unwrap(err) != nil || s != (Summary{}) {
			t.Fatal("reader refusal leaked error chain or receipt")
		}
	}
	_, err := Scan(nil, designLimits())
	requireCode(t, err, CodeReader)
	r := &emptyReader{}
	_, err = Scan(r, designLimits())
	requireCode(t, err, CodeReader)
	if r.calls > 100 {
		t.Fatal("empty reads were not bounded")
	}
	if _, err := Scan(&dataEOFReader{}, designLimits()); err != nil {
		t.Fatal("data with EOF refused")
	}
	joined := io.MultiReader(strings.NewReader(`0`), &failingReader{})
	_, err = Scan(joined, designLimits())
	requireCode(t, err, CodeReader)
}

func TestHugeStringsKeysAndNumbersRefuseBeforeReadingBody(t *testing.T) {
	for _, prefix := range []string{`"`, `{"`, `1`} {
		l := designLimits()
		l.MaxReadBytes = 65536 // Existing stricter R19g cap, supplied explicitly.
		b := byte('a')
		if prefix == "1" {
			b = '1'
		}
		r := &measuredReader{r: io.MultiReader(strings.NewReader(prefix), repeatedByte{b})}
		_, err := Scan(r, l)
		requireCode(t, err, CodeReadBytes)
		if r.bytes != 65537 || r.largest > 256 {
			t.Fatal("scanner exceeded limit-plus-one or fixed read chunk")
		}
	}
}

func TestEarlyStructuralRefusalAndNoReadOnInvalidLimits(t *testing.T) {
	l := designLimits()
	l.MaxDepth = 2
	r := &measuredReader{r: repeatedByte{'['}}
	_, err := Scan(r, l)
	requireCode(t, err, CodeDepth)
	if r.bytes > 256 || r.calls != 1 {
		t.Fatal("structural refusal read beyond one fixed chunk")
	}
	l.MaxReadBytes = 0
	r = &measuredReader{r: repeatedByte{'['}}
	_, err = Scan(r, l)
	requireCode(t, err, CodeInvalidLimits)
	if r.calls != 0 {
		t.Fatal("invalid limits touched reader")
	}
}

func TestSingleByteReadsAcrossEscapesAndUTF8(t *testing.T) {
	input := `{"é":"𝄞𝄞","x":[-1.2e+3,true,null]}`
	r := &measuredReader{r: strings.NewReader(input), oneByte: true}
	s, err := Scan(r, designLimits())
	if err != nil || s.Counts().Bytes != int64(len(input)) {
		t.Fatal("split lexical units failed")
	}
}

func TestScalarAllocationDoesNotScaleWithLength(t *testing.T) {
	l := designLimits()
	l.MaxReadBytes = 65536
	measure := func(input string) float64 {
		return testing.AllocsPerRun(2, func() {
			if _, err := Scan(strings.NewReader(input), l); err != nil {
				t.Fatal("allocation fixture refused")
			}
		})
	}
	small := measure(`"a"`)
	large := measure(`"` + strings.Repeat("a", 60000) + `"`)
	if large > small+2 {
		t.Fatal("scalar allocation grew with payload length")
	}
}

func TestKeyGrowthIsClampedBeforeAllocation(t *testing.T) {
	var key []byte
	for i := 0; i < 9; i++ {
		var err error
		key, err = appendKey(key, []byte{'a'}, 9)
		if err != nil || len(key) != i+1 || cap(key) > 9 {
			t.Fatal("key growth exceeded its explicit bound")
		}
	}
	_, err := appendKey(key, []byte{'a'}, 9)
	requireCode(t, err, CodeReadBytes)
	if len(key) != 9 || cap(key) > 9 {
		t.Fatal("failed key append changed previous buffer")
	}
}

func TestKeyAndUTF8AcrossFixedChunkBoundary(t *testing.T) {
	key := strings.Repeat("a", 253) + "𝄞"
	input := `{"` + key + `":0,"` + strings.Repeat("a", 253) + `𝄞":1}`
	_, err := Scan(strings.NewReader(input), designLimits())
	requireCode(t, err, CodeDuplicateKey)
}
