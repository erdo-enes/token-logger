package main

import (
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestProxyEndToEnd(t *testing.T) {
	dir := t.TempDir()
	ca, err := LoadOrCreateCA(filepath.Join(dir, "ca"))
	if err != nil {
		t.Fatal(err)
	}
	upstreamTLS, err := ca.ServerTLSConfig("127.0.0.1")
	if err != nil {
		t.Fatal(err)
	}
	upstreamLn, err := tls.Listen("tcp", "127.0.0.1:0", upstreamTLS)
	if err != nil {
		t.Fatal(err)
	}
	defer upstreamLn.Close()
	go http.Serve(upstreamLn, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"model":"claude-sonnet-4-5","usage":{"input_tokens":10,"output_tokens":5}}`)
	}))
	upstreamAddr := upstreamLn.Addr().String()

	cfg := DefaultConfig()
	cfg.ConfDir = filepath.Join(dir, "ca")
	cfg.JSONLPath = filepath.Join(dir, "calls.jsonl")
	cfg.UpstreamVerify = false
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	proxyAddr := ln.Addr().String()
	cfg.Addr = proxyAddr
	writer, err := NewWriter(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	proxy := NewProxy(cfg, ca, writer)
	go proxy.ServeListener(ln)

	pool := x509.NewCertPool()
	caPEM, err := os.ReadFile(filepath.Join(dir, "ca", "ca.pem"))
	if err != nil {
		t.Fatal(err)
	}
	if !pool.AppendCertsFromPEM(caPEM) {
		t.Fatal("append ca failed")
	}
	proxyURL, _ := url.Parse("http://" + proxyAddr)
	client := &http.Client{
		Timeout: 10 * time.Second,
		Transport: &http.Transport{
			Proxy:           http.ProxyURL(proxyURL),
			TLSClientConfig: &tls.Config{RootCAs: pool},
		},
	}
	resp, err := client.Post("https://"+upstreamAddr+"/v1/messages", "application/json",
		strings.NewReader(`{"model":"claude-sonnet-4-5","messages":[]}`))
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 200 || !strings.Contains(string(body), "claude-sonnet") {
		t.Fatalf("bad resp %d %s", resp.StatusCode, body)
	}
	// writer is async; wait for the record to land, then close to flush
	var data []byte
	for deadline := time.Now().Add(3 * time.Second); time.Now().Before(deadline); {
		data, _ = os.ReadFile(cfg.JSONLPath)
		if len(data) > 0 {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	writer.Close()
	data, err = os.ReadFile(cfg.JSONLPath)
	if err != nil {
		t.Fatal(err)
	}
	var rec Record
	if err := json.Unmarshal(data, &rec); err != nil {
		t.Fatalf("jsonl parse: %v\n%s", err, data)
	}
	if rec.Model != "claude-sonnet-4-5" || rec.TotalTokens == nil || *rec.TotalTokens != 15 {
		t.Fatalf("bad record: %+v", rec)
	}
}

func TestFluentBitUDP(t *testing.T) {
	addr, err := net.ResolveUDPAddr("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	listener, err := net.ListenUDP("udp", addr)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	port := listener.LocalAddr().(*net.UDPAddr).Port

	cfg := DefaultConfig()
	cfg.JSONLPath = filepath.Join(t.TempDir(), "calls.jsonl")
	cfg.FBEnabled = true
	cfg.FBHost = "127.0.0.1"
	cfg.FBPort = port
	cfg.FBFormat = "json"
	writer, err := NewWriter(cfg)
	if err != nil {
		t.Fatal(err)
	}
	writer.Put(Record{TS: time.Now().UTC().Format(time.RFC3339Nano), Model: "gpt-4o"})
	writer.Close()
	_ = listener.SetReadDeadline(time.Now().Add(2 * time.Second))
	buf := make([]byte, 65536)
	n, _, err := listener.ReadFromUDP(buf)
	if err != nil {
		t.Fatal(err)
	}
	var payload map[string]any
	if err := json.Unmarshal(buf[:n], &payload); err != nil {
		t.Fatal(err)
	}
	if payload["tag"] != "token_logger.calls" {
		t.Fatalf("bad tag: %s", buf[:n])
	}
}
