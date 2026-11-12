package contentbounds

import "io"

type phase uint8

const (
	objectFirst phase = iota
	objectKey
	objectColon
	objectValue
	objectAfter
	arrayFirst
	arrayValue
	arrayAfter
)

type frame struct {
	state phase
	keys  map[string]struct{}
}

type scanner struct {
	in     input
	limits Limits
	counts Counts
	kind   Kind
	stack  []frame
}

// Scan accepts exactly one strict JSON value and optional JSON whitespace.
// Invalid UTF-8, invalid/unpaired UTF-16 escapes, duplicate decoded keys,
// malformed/truncated input, extra documents and reader failures all refuse.
// On any refusal the returned Summary is zero, never a partial receipt.
//
// Reads are fixed-size and bounded by MaxReadBytes+1 including lookahead. The
// iterative parser retains only its structural stack and live decoded key
// sets. Scalar strings/numbers are not retained. Stack/key growth is checked
// before allocation; total retained key bytes are bounded by wire bytes and
// key counts by value-node bounds. This is NOT a full-document decoder.
//
// MaxItemBytes is an accumulator ceiling, not a scalar or inferred-envelope
// ceiling. For stricter item scanning, callers must reduce MaxReadBytes too.
// A blocking reader is a caller transport/deadline responsibility. Scan does
// not close the reader, recover reader panics, or start goroutines.
func Scan(reader io.Reader, limits Limits) (Summary, error) {
	if !limits.valid() {
		return Summary{}, refuse(CodeInvalidLimits)
	}
	if reader == nil {
		return Summary{}, refuse(CodeReader)
	}
	s := scanner{in: input{r: reader, limit: limits.MaxReadBytes}, limits: limits}
	started := false
	for {
		err := s.in.space()
		if err == io.EOF {
			if !started || len(s.stack) != 0 {
				return Summary{}, refuse(CodeJSON)
			}
			s.counts.Bytes = s.in.used
			return Summary{counts: s.counts, kind: s.kind, validated: true}, nil
		}
		if err != nil {
			return Summary{}, err
		}
		b, _ := s.in.peek() // space stopped at this buffered non-space byte.
		if !started {
			started = true
			err = s.value(b)
		} else if len(s.stack) == 0 {
			err = refuse(CodeJSON)
		} else {
			err = s.step(b)
		}
		if err != nil {
			return Summary{}, err
		}
	}
}

func (s *scanner) push(state phase) error {
	depth := int64(len(s.stack)) + 1
	if depth > s.limits.MaxDepth {
		return refuse(CodeDepth)
	}
	if len(s.stack) == cap(s.stack) {
		limit := int(min(s.limits.MaxDepth, s.limits.MaxNodes, s.limits.MaxReadBytes))
		capacity := cap(s.stack)
		if capacity == 0 {
			capacity = min(8, limit)
		} else if capacity <= limit-capacity {
			capacity *= 2
		} else {
			capacity = limit
		}
		next := make([]frame, len(s.stack), capacity)
		copy(next, s.stack)
		s.stack = next
	}
	s.stack = append(s.stack, frame{state: state})
	s.counts.Depth = max(s.counts.Depth, depth)
	return nil
}

func (s *scanner) pop() {
	last := len(s.stack) - 1
	s.stack[last] = frame{} // Release completed key sets, including slice slack.
	s.stack = s.stack[:last]
	_, _ = s.in.take()
}

func (s *scanner) value(b byte) error {
	if s.counts.Nodes >= s.limits.MaxNodes {
		return refuse(CodeNodes)
	}
	s.counts.Nodes++
	var kind Kind
	var err error
	switch {
	case b == '{':
		kind = KindObject
		err = s.push(objectFirst)
		if err == nil {
			_, _ = s.in.take()
		}
	case b == '[':
		kind = KindArray
		err = s.push(arrayFirst)
		if err == nil {
			_, _ = s.in.take()
		}
	case b == '"':
		kind = KindString
		_, err = s.in.stringToken(false)
	case b == 't':
		kind = KindBoolean
		err = s.in.literal("true")
	case b == 'f':
		kind = KindBoolean
		err = s.in.literal("false")
	case b == 'n':
		kind = KindNull
		err = s.in.literal("null")
	case b == '-' || (b >= '0' && b <= '9'):
		kind = KindNumber
		err = s.in.number()
	default:
		err = refuse(CodeJSON)
	}
	if s.counts.Nodes == 1 {
		s.kind = kind
	}
	return err
}

func (s *scanner) step(b byte) error {
	f := &s.stack[len(s.stack)-1]
	switch f.state {
	case objectFirst, objectKey:
		if f.state == objectFirst && b == '}' {
			s.pop()
			return nil
		}
		if b != '"' {
			return refuse(CodeJSON)
		}
		// Every key needs a value node. Refuse BEFORE buffering a new key
		// when no value node is available, not after key/map allocation.
		if s.counts.Nodes >= s.limits.MaxNodes {
			return refuse(CodeNodes)
		}
		key, err := s.in.stringToken(true)
		if err != nil {
			return err
		}
		if _, exists := f.keys[key]; exists {
			return refuse(CodeDuplicateKey)
		}
		if f.keys == nil {
			f.keys = make(map[string]struct{})
		}
		f.keys[key] = struct{}{}
		f.state = objectColon
	case objectColon:
		if b != ':' {
			return refuse(CodeJSON)
		}
		_, _ = s.in.take()
		f.state = objectValue
	case objectValue:
		f.state = objectAfter
		return s.value(b)
	case objectAfter:
		switch b {
		case '}':
			s.pop()
		case ',':
			_, _ = s.in.take()
			f.state = objectKey
		default:
			return refuse(CodeJSON)
		}
	case arrayFirst, arrayValue:
		if f.state == arrayFirst && b == ']' {
			s.pop()
			return nil
		}
		if len(s.stack) == 1 {
			if s.counts.RootArrayItems >= s.limits.MaxItems {
				return refuse(CodeItems)
			}
			s.counts.RootArrayItems++
		}
		f.state = arrayAfter
		return s.value(b)
	case arrayAfter:
		switch b {
		case ']':
			s.pop()
		case ',':
			_, _ = s.in.take()
			f.state = arrayValue
		default:
			return refuse(CodeJSON)
		}
	default:
		return refuse(CodeJSON)
	}
	return nil
}
