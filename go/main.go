package main

import (
	"fmt"
	"os"
	"os/signal"
	"syscall"
)

func main() {
	configPath := ""
	if len(os.Args) > 1 {
		configPath = os.Args[1]
	}
	cfg := LoadConfig(configPath)
	writer, err := NewWriter(cfg)
	if err != nil {
		fmt.Fprintln(os.Stderr, "writer:", err)
		os.Exit(1)
	}
	defer writer.Close()
	ca, err := LoadOrCreateCA(cfg.ConfDir)
	if err != nil {
		fmt.Fprintln(os.Stderr, "ca:", err)
		os.Exit(1)
	}
	fmt.Fprintf(os.Stderr, "token-logger listening on %s (jsonl=%s, fluentbit=%v, ca=%s)\n", cfg.Addr, cfg.JSONLPath, cfg.FBEnabled, cfg.ConfDir)
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigCh
		writer.Close()
		os.Exit(0)
	}()
	if err := NewProxy(cfg, ca, writer).Serve(); err != nil {
		fmt.Fprintln(os.Stderr, "serve:", err)
		os.Exit(1)
	}
}
