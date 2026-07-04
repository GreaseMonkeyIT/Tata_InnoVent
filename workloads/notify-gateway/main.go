// notify-gateway: terminal HTTP notifier; S4 injects latency in front of it.
package main

import (
	"fmt"
	"net/http"
	"time"
)

func main() {
	http.HandleFunc("/alert", func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(20 * time.Millisecond) // nominal egress cost
		w.WriteHeader(http.StatusOK)
		fmt.Fprint(w, `{"sent":true}`)
	})
	http.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200) })
	_ = http.ListenAndServe(":8080", nil)
}
