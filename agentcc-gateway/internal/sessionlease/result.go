package sessionlease

import "time"

func statusError(code any) error {
	switch code {
	case "CAPACITY":
		return ErrCapacity
	case "GONE":
		return ErrGone
	case "BINDING":
		return ErrBinding
	case "CLOCK":
		return ErrClock
	case "CORRUPT":
		return ErrCorrupt
	default:
		return ErrUnavailable
	}
}
func decodeLease(raw any, b Binding) (Lease, error) {
	fields, ok := raw.([]any)
	if !ok || len(fields) == 0 {
		return Lease{}, ErrCorrupt
	}
	if fields[0] != "OK" {
		return Lease{}, statusError(fields[0])
	}
	if len(fields) != 5 {
		return Lease{}, ErrCorrupt
	}
	var ms [4]int64
	for i := range ms {
		n, ok := fields[i+1].(int64)
		if !ok || n <= 0 || n > 9007199254740991 {
			return Lease{}, ErrCorrupt
		}
		ms[i] = n
	}
	if ms[1] <= ms[0] || ms[1] > ms[2] || ms[2] > ms[3] || ms[1]-ms[0] > 3000 {
		return Lease{}, ErrCorrupt
	}
	return Lease{Binding: b, ServerTime: time.UnixMilli(ms[0]), ExpiresAt: time.UnixMilli(ms[1]), IdleDeadline: time.UnixMilli(ms[2]), AbsoluteDeadline: time.UnixMilli(ms[3])}, nil
}
