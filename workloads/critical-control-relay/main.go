// critical-control-relay (CCR): subscribes cmd/#, "actuates", publishes heartbeat.
// SLO: p95 actuation < 100ms. Exposes /metrics histogram + /healthz (2s probe budget).
package main

import (
	"fmt"
	"net/http"
	"os"
	"sync/atomic"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var lastBeat atomic.Int64

func env(k, d string) string { if v := os.Getenv(k); v != "" { return v }; return d }

func main() {
	hist := prometheus.NewHistogram(prometheus.HistogramOpts{
		Name: "ccr_actuation_seconds", Help: "actuation latency",
		Buckets: []float64{.01, .025, .05, .1, .2, .5, 1, 2},
	})
	prometheus.MustRegister(hist)

	broker := env("MQTT_URL", "tcp://mqtt-broker.factory-core.svc:1883")
	opts := mqtt.NewClientOptions().AddBroker(broker).SetClientID("ccr").SetAutoReconnect(true)
	c := mqtt.NewClient(opts)
	for t := c.Connect(); t.Wait() && t.Error() != nil; t = c.Connect() {
		fmt.Println("mqtt retry:", t.Error()); time.Sleep(2 * time.Second)
	}
	c.Subscribe("cmd/#", 1, func(_ mqtt.Client, m mqtt.Message) {
		t0 := time.Now()
		// "actuation": a small amount of real work; slows under CPU/IO pressure - that is the point.
		buf := make([]byte, 1<<16)
		for i := range buf { buf[i] = byte(i) }
		_ = os.WriteFile("/tmp/actuation.state", buf[:1024], 0o644)
		hist.Observe(time.Since(t0).Seconds())
	})
	go func() { // heartbeat at 1 Hz; safety-interlock watches this
		for range time.Tick(time.Second) {
			c.Publish("heartbeat/ccr", 1, false, fmt.Sprintf("%d", time.Now().UnixMilli()))
			lastBeat.Store(time.Now().UnixMilli())
		}
	}()
	http.Handle("/metrics", promhttp.Handler())
	http.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK) // liveness has 2s budget; degrade, never crash-loop
	})
	_ = http.ListenAndServe(":8080", nil)
}
