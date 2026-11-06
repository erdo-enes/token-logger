package main

import (
	"encoding/binary"
	"math"
)

func msgpackEncode(v any) []byte {
	var out []byte
	switch x := v.(type) {
	case nil:
		return []byte{0xc0}
	case bool:
		if x {
			return []byte{0xc3}
		}
		return []byte{0xc2}
	case int64:
		return msgpackInt(x)
	case int:
		return msgpackInt(int64(x))
	case float64:
		b := make([]byte, 9)
		b[0] = 0xcb
		binary.BigEndian.PutUint64(b[1:], math.Float64bits(x))
		return b
	case string:
		raw := []byte(x)
		n := len(raw)
		switch {
		case n < 32:
			return append([]byte{0xa0 | byte(n)}, raw...)
		case n < 256:
			return append([]byte{0xd9, byte(n)}, raw...)
		case n < 65536:
			b := make([]byte, 3)
			b[0] = 0xda
			binary.BigEndian.PutUint16(b[1:], uint16(n))
			return append(b, raw...)
		default:
			b := make([]byte, 5)
			b[0] = 0xdb
			binary.BigEndian.PutUint32(b[1:], uint32(n))
			return append(b, raw...)
		}
	case []any:
		n := len(x)
		var head []byte
		if n < 16 {
			head = []byte{0x90 | byte(n)}
		} else {
			head = make([]byte, 3)
			head[0] = 0xdc
			binary.BigEndian.PutUint16(head[1:], uint16(n))
		}
		out = append(out, head...)
		for _, e := range x {
			out = append(out, msgpackEncode(e)...)
		}
		return out
	case map[string]any:
		n := len(x)
		var head []byte
		if n < 16 {
			head = []byte{0x80 | byte(n)}
		} else {
			head = make([]byte, 3)
			head[0] = 0xde
			binary.BigEndian.PutUint16(head[1:], uint16(n))
		}
		out = append(out, head...)
		for k, val := range x {
			out = append(out, msgpackEncode(k)...)
			out = append(out, msgpackEncode(val)...)
		}
		return out
	default:
		return msgpackEncode("")
	}
}

func msgpackInt(v int64) []byte {
	switch {
	case v >= 0 && v < 128:
		return []byte{byte(v)}
	case v >= 0 && v < 256:
		return []byte{0xcc, byte(v)}
	case v >= 0 && v < 65536:
		b := make([]byte, 3)
		b[0] = 0xcd
		binary.BigEndian.PutUint16(b[1:], uint16(v))
		return b
	case v >= 0 && v < 4294967296:
		b := make([]byte, 5)
		b[0] = 0xce
		binary.BigEndian.PutUint32(b[1:], uint32(v))
		return b
	default:
		b := make([]byte, 9)
		b[0] = 0xcf
		binary.BigEndian.PutUint64(b[1:], uint64(v))
		return b
	}
}
