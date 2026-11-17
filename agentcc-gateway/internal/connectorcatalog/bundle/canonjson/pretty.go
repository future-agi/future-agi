package canonjson

import (
	"bytes"
	"strings"
)

// printWidth is Prettier's default print width, which the repository uses
// (it has no Prettier configuration).
const printWidth = 80

// Pretty renders v, followed by a newline, exactly as Prettier 3's JSON
// printer (printWidth 80, two-space indent) prints it, so the commit hook's
// formatter leaves the bytes unchanged and a file the tool checks byte for
// byte stays fresh after it is committed. The rules, as Prettier applies
// them to its own output:
//   - a non-empty object always breaks, one member per line (Prettier keeps
//     an object expanded when a newline follows its opening brace);
//   - an array stays on one line when it holds no non-empty object, is not
//     two or more arrays (or objects) of the same kind each with more than
//     one item, and fits in 80 columns with the comma that follows it;
//   - otherwise an array breaks, one element per line, except an array of
//     two or more numbers, which fills each line;
//   - every non-ASCII character is written as a \u escape, which Prettier
//     keeps, so column widths are byte counts;
//   - numbers are written as ECMAScript does, without the "+" of a positive
//     exponent, the form Prettier normalizes numbers to.
func Pretty(v any) ([]byte, error) {
	p := &printer{}
	if err := p.value(v, 0, 0, 0); err != nil {
		return nil, err
	}
	p.buf.WriteByte('\n')
	return p.buf.Bytes(), nil
}

type printer struct{ buf bytes.Buffer }

// value prints v starting at column col; indent is the indentation of the
// line v starts on, and trailing the width of what follows v on its line.
func (p *printer) value(v any, col, indent, trailing int) error {
	v, err := normalize(v)
	if err != nil {
		return err
	}
	switch x := v.(type) {
	case Object:
		return p.object(x, indent)
	case []any:
		return p.array(x, col, indent, trailing)
	}
	s, err := atom(v)
	if err != nil {
		return err
	}
	p.buf.WriteString(s)
	return nil
}

func (p *printer) object(o Object, indent int) error {
	if len(o) == 0 {
		p.buf.WriteString("{}")
		return nil
	}
	p.buf.WriteString("{\n")
	inner := indent + 2
	for i, m := range o {
		key, err := atom(m.Key)
		if err != nil {
			return err
		}
		p.buf.WriteString(strings.Repeat(" ", inner))
		p.buf.WriteString(key)
		p.buf.WriteString(": ")
		last := i == len(o)-1
		if err := p.value(m.Value, inner+len(key)+2, inner, commaWidth(last)); err != nil {
			return err
		}
		if !last {
			p.buf.WriteByte(',')
		}
		p.buf.WriteByte('\n')
	}
	p.buf.WriteString(strings.Repeat(" ", indent))
	p.buf.WriteByte('}')
	return nil
}

func (p *printer) array(a []any, col, indent, trailing int) error {
	if len(a) == 0 {
		p.buf.WriteString("[]")
		return nil
	}
	one, mustBreak, err := flat(a)
	if err != nil {
		return err
	}
	if !mustBreak && col+len(one)+trailing <= printWidth {
		p.buf.WriteString(one)
		return nil
	}
	inner := indent + 2
	p.buf.WriteString("[\n")
	p.buf.WriteString(strings.Repeat(" ", inner))
	if allNumbers(a) {
		if err := p.fill(a, inner); err != nil {
			return err
		}
	} else {
		for i, e := range a {
			last := i == len(a)-1
			if err := p.value(e, inner, inner, commaWidth(last)); err != nil {
				return err
			}
			if !last {
				p.buf.WriteString(",\n")
				p.buf.WriteString(strings.Repeat(" ", inner))
			}
		}
	}
	p.buf.WriteByte('\n')
	p.buf.WriteString(strings.Repeat(" ", indent))
	p.buf.WriteByte(']')
	return nil
}

// fill prints numbers as Prettier's fill does: an element and its comma go
// on the current line, and a line break follows it when the next element
// and its comma would not fit beside it.
func (p *printer) fill(a []any, indent int) error {
	parts := make([]string, len(a))
	for i, e := range a {
		s, err := atom(e)
		if err != nil {
			return err
		}
		if i < len(a)-1 {
			s += ","
		}
		parts[i] = s
	}
	col := indent
	for i, s := range parts {
		p.buf.WriteString(s)
		if i == len(parts)-1 {
			break
		}
		if col+len(s)+1+len(parts[i+1]) <= printWidth {
			p.buf.WriteByte(' ')
			col += len(s) + 1
		} else {
			p.buf.WriteByte('\n')
			p.buf.WriteString(strings.Repeat(" ", indent))
			col = indent
		}
	}
	return nil
}

// flat renders v on one line, and reports whether Prettier breaks it
// whatever the width.
func flat(v any) (string, bool, error) {
	v, err := normalize(v)
	if err != nil {
		return "", false, err
	}
	switch x := v.(type) {
	case Object:
		if len(x) == 0 {
			return "{}", false, nil
		}
		return "", true, nil
	case []any:
		if len(x) == 0 {
			return "[]", false, nil
		}
		if sameKindCollections(x) {
			return "", true, nil
		}
		items := make([]string, len(x))
		for i, e := range x {
			s, brk, err := flat(e)
			if err != nil || brk {
				return "", brk, err
			}
			items[i] = s
		}
		return "[" + strings.Join(items, ", ") + "]", false, nil
	}
	s, err := atom(v)
	return s, false, err
}

// sameKindCollections is Prettier's rule that breaks an array of two or more
// arrays, or two or more objects, each with more than one item.
func sameKindCollections(a []any) bool {
	if len(a) < 2 {
		return false
	}
	for i, e := range a {
		n := 0
		switch x := e.(type) {
		case Object:
			n = len(x)
		case []any:
			n = len(x)
		default:
			return false
		}
		if n <= 1 {
			return false
		}
		if i+1 < len(a) && !sameKind(e, a[i+1]) {
			return false
		}
	}
	return true
}

func sameKind(a, b any) bool {
	_, ao := a.(Object)
	_, bo := b.(Object)
	return ao == bo
}

func allNumbers(a []any) bool {
	if len(a) < 2 {
		return false
	}
	for _, e := range a {
		switch e.(type) {
		case int, int64, float64:
		default:
			return false
		}
	}
	return true
}

func commaWidth(last bool) int {
	if last {
		return 0
	}
	return 1
}

// atom renders a string, number, bool or null.
func atom(v any) (string, error) {
	v, err := normalize(v)
	if err != nil {
		return "", err
	}
	switch x := v.(type) {
	case nil:
		return "null", nil
	case bool:
		if x {
			return "true", nil
		}
		return "false", nil
	case string:
		var buf bytes.Buffer
		if err := writeString(&buf, x, escapeASCII); err != nil {
			return "", err
		}
		return buf.String(), nil
	case int64, float64:
		s, err := NumberText(x)
		return strings.Replace(s, "e+", "e", 1), err
	}
	return "", nil // unreachable: objects and arrays are not atoms
}
