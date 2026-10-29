package main

import (
	"flag"
	"log"
	"net/http"
	"strings"
	"time"
)

func main() {
	addr := flag.String("addr", ":8080", "listen address")
	baseURL := flag.String("base-url", "https://hive.ethpandaops.io", "base URL for hive results")
	groups := flag.String("groups", "", "comma-separated list of result groups (auto-discovered if empty)")
	refresh := flag.Duration("refresh", 5*time.Minute, "refresh interval")
	flag.Parse()

	var groupList []string
	if *groups != "" {
		groupList = strings.Split(*groups, ",")
		for i := range groupList {
			groupList[i] = strings.TrimSpace(groupList[i])
		}
	}

	log.Printf("starting hapi: base=%s groups=%v refresh=%s", *baseURL, groupList, *refresh)

	store := NewStore(*baseURL, groupList)
	store.Start(*refresh)

	api := NewAPI(store)
	mux := http.NewServeMux()
	api.RegisterRoutes(mux)

	// Health check.
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		w.Write([]byte("ok"))
	})

	handler := corsMiddleware(mux)

	log.Printf("listening on %s", *addr)
	log.Fatal(http.ListenAndServe(*addr, handler))
}

func corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == "OPTIONS" {
			w.WriteHeader(204)
			return
		}
		next.ServeHTTP(w, r)
	})
}
