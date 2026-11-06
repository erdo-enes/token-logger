package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type Record struct {
	TS               string   `json:"ts"`
	Client           string   `json:"client"`
	Model            string   `json:"model"`
	Endpoint         string   `json:"endpoint"`
	Host             string   `json:"host"`
	Path             string   `json:"path"`
	StatusCode       int      `json:"status_code"`
	LatencyMs        float64  `json:"latency_ms"`
	PromptTokens     *int64   `json:"prompt_tokens"`
	CompletionTokens *int64   `json:"completion_tokens"`
	CacheReadTokens  *int64   `json:"cache_read_tokens"`
	CacheWriteTokens *int64   `json:"cache_write_tokens"`
	TotalTokens      *int64   `json:"total_tokens"`
	CostUSD          *float64 `json:"cost_usd"`
	RequestJSON      string   `json:"request_json"`
	ResponseJSON     string   `json:"response_json"`
	Error            string   `json:"error"`
}

type Writer struct {
	ch       chan Record
	file     *os.File
	buf      *bufio.Writer
	udp      *net.UDPConn
	cfg      *Config
	dropped  atomic.Int64
	sent     atomic.Int64
	done     chan struct{}
	closed   atomic.Bool
	once     sync.Once
}

func NewWriter(cfg *Config) (*Writer, error) {
	if err := os.MkdirAll(filepath.Dir(cfg.JSONLPath), 0o755); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(cfg.JSONLPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, err
	}
	w := &Writer{ch: make(chan Record, 1000), file: f, buf: bufio.NewWriterSize(f, 65536), cfg: cfg, done: make(chan struct{})}
	if cfg.FBEnabled {
		addr, err := net.ResolveUDPAddr("udp", fmt.Sprintf("%s:%d", cfg.FBHost, cfg.FBPort))
		if err == nil {
			if conn, err := net.DialUDP("udp", nil, addr); err == nil {
				w.udp = conn
			}
		}
	}
	go w.loop()
	return w, nil
}

func (w *Writer) Put(r Record) {
	defer func() { _ = recover() }()
	if w.closed.Load() {
		return
	}
	select {
	case w.ch <- r:
	default:
		w.dropped.Add(1)
	}
}

func (w *Writer) loop() {
	defer close(w.done)
	for r := range w.ch {
		line, err := json.Marshal(r)
		if err != nil {
			continue
		}
		w.buf.Write(line)
		w.buf.WriteByte('\n')
		w.buf.Flush()
		if w.udp != nil {
			payload := w.buildUDPPayload(r)
			if _, err := w.udp.Write(payload); err == nil {
				w.sent.Add(1)
			} else {
				w.dropped.Add(1)
			}
		}
	}
}

func (w *Writer) buildUDPPayload(r Record) []byte {
	if len(r.RequestJSON) > 8000 {
		r.RequestJSON = r.RequestJSON[:8000]
	}
	if len(r.ResponseJSON) > 8000 {
		r.ResponseJSON = r.ResponseJSON[:8000]
	}
	if strings.EqualFold(w.cfg.FBFormat, "forward") {
		raw, _ := json.Marshal(r)
		var m map[string]any
		_ = json.Unmarshal(raw, &m)
		ts, err := time.Parse(time.RFC3339Nano, r.TS)
		epoch := float64(0)
		if err == nil {
			epoch = float64(ts.UnixNano()) / 1e9
		}
		payload := msgpackEncode([]any{w.cfg.FBTag, []any{[]any{epoch, m}}})
		if len(payload) > 60000 {
			payload = payload[:60000]
		}
		return payload
	}
	payload, _ := json.Marshal(map[string]any{"tag": w.cfg.FBTag, "ts": r.TS, "record": r})
	if len(payload) > 60000 {
		payload = payload[:60000]
	}
	return payload
}

func (w *Writer) Close() {
	w.once.Do(func() {
		w.closed.Store(true)
		close(w.ch)
		<-w.done
		w.buf.Flush()
		w.file.Close()
		if w.udp != nil {
			w.udp.Close()
		}
	})
}
