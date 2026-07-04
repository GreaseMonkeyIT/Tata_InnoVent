// safety-interlock: watches CCR heartbeat; trips to safe-mode on 3 missed beats.
package main

import (
	"fmt"
	"net/http"
	"os"
	"sync/atomic"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
)

var last atomic.Int64
var tripped atomic.Bool

func env(k, d string) string { if v := os.Getenv(k); v != "" { return v }; return d }

func main() {
	last.Store(time.Now().UnixMilli())
	opts := mqtt.NewClientOptions().AddBroker(env("MQTT_URL", "tcp://mqtt-broker.factory-core.svc:1883")).
		SetClientID("safety-interlock").SetAutoReconnect(true)
	c := mqtt.NewClient(opts)
	for t := c.Connect(); t.Wait() && t.Error() != nil; t = c.Connect() { time.Sleep(2 * time.Second) }
	c.Subscribe("heartbeat/ccr", 1, func(_ mqtt.Client, _ mqtt.Message) {
		last.Store(time.Now().UnixMilli())
		tripped.Store(false)
	})
	go func() {
		for range time.Tick(time.Second) {
			if time.Now().UnixMilli()-last.Load() > 3000 && !tripped.Load() {
				tripped.Store(true)
				fmt.Println("SAFE-MODE TRIP: ccr heartbeat lost >3s") // log event = A2 evidence
			}
		}
	}()
	http.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200) })
	http.HandleFunc("/state", func(w http.ResponseWriter, _ *http.Request) {
		if tripped.Load() { fmt.Fprint(w, "SAFE_MODE") } else { fmt.Fprint(w, "ARMED") }
	})
	_ = http.ListenAndServe(":8080", nil)
}
