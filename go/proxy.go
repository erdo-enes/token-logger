package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httputil"
	"os"
	"strings"
	"time"
)

func debugf(format string, args ...any) {
	if os.Getenv("TOKEN_LOGGER_DEBUG") != "" {
		fmt.Fprintf(os.Stderr, "proxy: "+format+"\n", args...)
	}
}

type bufferedConn struct {
	net.Conn
	r *bufio.Reader
}

func (c *bufferedConn) Read(b []byte) (int, error) { return c.r.Read(b) }

type notifyConn struct {
	net.Conn
	onClose func()
	once    bool
}

func (c *notifyConn) Close() error {
	if !c.once {
		c.once = true
		c.onClose()
	}
	return c.Conn.Close()
}

type singleConnListener struct {
	conn net.Conn
	once bool
	done chan struct{}
}

func (l *singleConnListener) Accept() (net.Conn, error) {
	if !l.once {
		l.once = true
		l.done = make(chan struct{})
		return &notifyConn{Conn: l.conn, onClose: func() { close(l.done) }}, nil
	}
	<-l.done
	return nil, errors.New("single connection closed")
}
func (l *singleConnListener) Close() error  { return nil }
func (l *singleConnListener) Addr() net.Addr { return l.conn.LocalAddr() }

type cappedBuffer struct {
	buf bytes.Buffer
	max int
}

func (c *cappedBuffer) Write(p []byte) (int, error) {
	if c.buf.Len() < c.max {
		n := c.max - c.buf.Len()
		if len(p) < n {
			n = len(p)
		}
		c.buf.Write(p[:n])
	}
	return len(p), nil
}

type captureReadCloser struct {
	io.ReadCloser
	buf *cappedBuffer
}

func (c *captureReadCloser) Read(p []byte) (int, error) {
	n, err := c.ReadCloser.Read(p)
	if n > 0 {
		c.buf.Write(p[:n])
	}
	return n, err
}

type state struct {
	start         time.Time
	reqBuf        *cappedBuffer
	reqCT         string
	reqCE         string
	userAgent     string
	respBuf       *cappedBuffer
	respCT        string
	respCE        string
	status        int
	transportErr  string
	contentLength int64
}

func decodeBody(data []byte, contentEncoding string) []byte {
	if strings.EqualFold(strings.TrimSpace(contentEncoding), "gzip") && len(data) > 0 {
		if gr, err := gzip.NewReader(bytes.NewReader(data)); err == nil {
			defer gr.Close()
			if decoded, err := io.ReadAll(gr); err == nil {
				return decoded
			}
		}
	}
	return data
}

type ctxKey struct{}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(code int) {
	w.status = code
	w.ResponseWriter.WriteHeader(code)
}
func (w *statusWriter) Flush() {
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

type Proxy struct {
	cfg       *Config
	ca        *CA
	writer    *Writer
	transport *http.Transport
}

func NewProxy(cfg *Config, ca *CA, w *Writer) *Proxy {
	tr := &http.Transport{
		Proxy:              nil,
		DisableCompression: true,
		ForceAttemptHTTP2:  true,
		MaxIdleConns:       128,
		MaxIdleConnsPerHost: 32,
		IdleConnTimeout:    90 * time.Second,
		TLSHandshakeTimeout: 15 * time.Second,
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: !cfg.UpstreamVerify,
			MinVersion:         tls.VersionTLS12,
			NextProtos:         []string{"h2", "http/1.1"},
		},
	}
	return &Proxy{cfg: cfg, ca: ca, writer: w, transport: tr}
}

func (p *Proxy) shouldInterceptHost(host string) bool {
	h := strings.ToLower(host)
	if sh, _, err := net.SplitHostPort(h); err == nil {
		h = sh
	}
	if h == "127.0.0.1" || h == "localhost" || h == "::1" {
		return true
	}
	for _, allowed := range p.cfg.Hosts {
		a := strings.ToLower(allowed)
		if h == a || strings.HasSuffix(h, "."+a) {
			return true
		}
	}
	return false
}

func tunnelBlindTCP(client net.Conn, target string) {
	defer client.Close()
	if !strings.Contains(target, ":") {
		target += ":443"
	}
	up, err := net.DialTimeout("tcp", target, 15*time.Second)
	if err != nil {
		return
	}
	defer up.Close()
	_ = client.SetDeadline(time.Time{})
	done := make(chan struct{}, 2)
	go func() {
		_, _ = io.Copy(up, client)
		done <- struct{}{}
	}()
	go func() {
		_, _ = io.Copy(client, up)
		done <- struct{}{}
	}()
	<-done
}

func (p *Proxy) Serve() error {
	ln, err := net.Listen("tcp", p.cfg.Addr)
	if err != nil {
		return err
	}
	return p.ServeListener(ln)
}

func (p *Proxy) ServeListener(ln net.Listener) error {
	debugf("listening on %s", p.cfg.Addr)
	for {
		conn, err := ln.Accept()
		if err != nil {
			debugf("accept: %v", err)
			return err
		}
		debugf("accepted %s", conn.RemoteAddr())
		go p.handle(conn)
	}
}

func (p *Proxy) handle(conn net.Conn) {
	debugf("handle start")
	_ = conn.SetDeadline(time.Now().Add(30 * time.Second))
	br := bufio.NewReader(conn)
	req, err := http.ReadRequest(br)
	if err != nil {
		debugf("readrequest: %v", err)
		conn.Close()
		return
	}
	if strings.EqualFold(req.Method, "CONNECT") {
		target := req.URL.Host
		host, _, _ := net.SplitHostPort(target)
		if host == "" {
			host = target
		}
		if _, err := conn.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n")); err != nil {
			conn.Close()
			return
		}
		if !p.shouldInterceptHost(host) {
			tunnelBlindTCP(&bufferedConn{Conn: conn, r: br}, target)
			return
		}
		tlsCfg, err := p.ca.ServerTLSConfig(host)
		if err != nil {
			debugf("tlsconfig: %v", err)
			conn.Close()
			return
		}
		tlsConn := tls.Server(&bufferedConn{Conn: conn, r: br}, tlsCfg)
		if err := tlsConn.Handshake(); err != nil {
			debugf("handshake: %v", err)
			conn.Close()
			return
		}
		_ = tlsConn.SetDeadline(time.Time{})
		p.serveConn(tlsConn, "https", target)
		tlsConn.Close()
		return
	}
	// Plain HTTP proxy / direct request
	host := req.URL.Host
	scheme := req.URL.Scheme
	if scheme == "" {
		scheme = "http"
	}
	if host == "" {
		host = req.Host
	}
	p.serveConn(&bufferedConn{Conn: conn, r: br}, scheme, host)
	conn.Close()
}

func (p *Proxy) serveConn(conn net.Conn, scheme, target string) {
	rp := &httputil.ReverseProxy{
		FlushInterval: -1,
		Transport:     p.transport,
		Rewrite: func(pr *httputil.ProxyRequest) {
			st, _ := pr.In.Context().Value(ctxKey{}).(*state)
			rc := &captureReadCloser{ReadCloser: pr.In.Body, buf: &cappedBuffer{max: p.cfg.MaxBody}}
			st.reqBuf = rc.buf
			st.reqCT = pr.In.Header.Get("Content-Type")
			st.reqCE = pr.In.Header.Get("Content-Encoding")
			st.userAgent = pr.In.Header.Get("User-Agent")
			pr.Out.Body = rc
			pr.Out.ContentLength = pr.In.ContentLength
			if pr.Out.URL.Host != "" {
				if pr.Out.URL.Scheme == "" {
					pr.Out.URL.Scheme = scheme
				}
			} else {
				pr.Out.URL.Scheme = scheme
				pr.Out.URL.Host = target
			}
			pr.Out.Host = pr.Out.URL.Host
			pr.Out.RequestURI = ""
			// Ensure no proxy headers (X-Forwarded-For, X-Forwarded-Host, X-Forwarded-Proto, Via)
			// are ever injected unless the client explicitly sent them.
			for _, h := range []string{"X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "Via"} {
				if _, ok := pr.In.Header[h]; !ok {
					delete(pr.Out.Header, h)
				}
			}
		},
		ModifyResponse: func(resp *http.Response) error {
			st, _ := resp.Request.Context().Value(ctxKey{}).(*state)
			if st != nil {
				st.status = resp.StatusCode
				st.respCT = resp.Header.Get("Content-Type")
				st.respCE = resp.Header.Get("Content-Encoding")
				st.contentLength = resp.ContentLength
				resp.Body = &captureReadCloser{ReadCloser: resp.Body, buf: st.respBuf}
			}
			return nil
		},
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			debugf("reverseproxy error: %v", err)
			if st, _ := r.Context().Value(ctxKey{}).(*state); st != nil {
				st.transportErr = err.Error()
			}
			w.WriteHeader(http.StatusBadGateway)
		},
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		debugf("serveConn request %s %s", r.Method, r.URL.Path)
		st := &state{start: time.Now(), respBuf: &cappedBuffer{max: p.cfg.MaxBody}}
		r = r.WithContext(context.WithValue(r.Context(), ctxKey{}, st))
		sw := &statusWriter{ResponseWriter: w}
		rp.ServeHTTP(sw, r)
		debugf("serveConn done status=%d err=%s", st.status, st.transportErr)
		if st.status == 0 {
			st.status = sw.status
		}
		if st.status == 0 {
			st.status = 200
		}
		host := target
		if host == "" {
			host = r.Host
		}
		if h, _, err := net.SplitHostPort(host); err == nil {
			host = h
		}
		if !IsLLM(host, r.URL.Path, p.cfg.Hosts) {
			return
		}
		var rawReq []byte
		if st.reqBuf != nil {
			rawReq = st.reqBuf.buf.Bytes()
		}
		reqBytes := decodeBody(rawReq, st.reqCE)
		respBytes := decodeBody(st.respBuf.buf.Bytes(), st.respCE)
		reqParsed := ParseBody(reqBytes, st.reqCT)
		respParsed := ParseBody(respBytes, st.respCT)
		model := respParsed.Model
		if model == "" {
			model = reqParsed.Model
		}
		rec := Record{
			TS:               time.Now().UTC().Format(time.RFC3339Nano),
			Client:           DetectClient(st.userAgent, p.cfg.Client),
			Model:            model,
			Endpoint:         scheme + "://" + host + r.URL.Path,
			Host:             host,
			Path:             r.URL.Path,
			StatusCode:       st.status,
			LatencyMs:        float64(time.Since(st.start).Microseconds()) / 1000.0,
			PromptTokens:     respParsed.Usage.Prompt,
			CompletionTokens: respParsed.Usage.Completion,
			CacheReadTokens:  respParsed.Usage.CacheRead,
			CacheWriteTokens: respParsed.Usage.CacheWrite,
			TotalTokens:      respParsed.Usage.Total,
			CostUSD:          EstimateCost(model, respParsed.Usage),
			RequestJSON:      Redact(string(reqBytes)),
			ResponseJSON:     Redact(string(respBytes)),
			Error:            st.transportErr,
		}
		p.writer.Put(rec)
	})
	srv := &http.Server{
		Handler:     handler,
		IdleTimeout: 90 * time.Second,
	}
	_ = srv.Serve(&singleConnListener{conn: conn})
}
