// A danvas panel from an Arduino with NO network hardware.
//
// The board prints newline-delimited danvas frames over USB serial; the
// shipped bridge relays them onto a canvas as a dial-in source:
//
//     pip install danvas[serial]
//     python -m danvas.serial COM3        # spawns/attaches a hub on :8000
//
// This sketch: a slider on the canvas drives the built-in LED's blink rate,
// and the board streams A0 back as the slider... no, better: two panels —
// a label showing millis-uptime and a slider whose browser value sets the
// LED blink period. The firmware's whole protocol burden is
// Serial.println(json) and readStringUntil('\n').

unsigned long blinkMs = 500;
unsigned long lastBlink = 0, lastReport = 0;
bool led = false;

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
  // Register the panels: native templates, expanded by the bridge, placed
  // by the hub's frontend (rel chains work from firmware too).
  Serial.println(F("{\"type\":\"register_template\",\"id\":\"up\",\"kind\":\"label\",\"data\":{\"text\":\"booting\"},\"x\":40,\"y\":40}"));
  Serial.println(F("{\"type\":\"register_template\",\"id\":\"blink\",\"kind\":\"slider\",\"data\":{\"min\":50,\"max\":2000,\"value\":500},\"rel\":{\"kind\":\"below\",\"anchor\":\"up\",\"gap\":16}}"));
}

void loop() {
  // Inbound: one JSON frame per line — a browser moved our slider.
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    // Tiny parse: enough for {"type":"input","id":"blink",...,"value":N}.
    if (line.indexOf("\"blink\"") >= 0) {
      int v = line.indexOf("\"value\":");
      if (v >= 0) blinkMs = line.substring(v + 8).toInt();
    }
  }

  unsigned long now = millis();
  if (now - lastBlink >= blinkMs) {
    lastBlink = now;
    led = !led;
    digitalWrite(LED_BUILTIN, led);
  }
  if (now - lastReport >= 1000) {
    lastReport = now;
    Serial.print(F("{\"type\":\"update\",\"id\":\"up\",\"payload\":{\"post\":\"uptime "));
    Serial.print(now / 1000);
    Serial.println(F(" s\"}}"));
  }
}
