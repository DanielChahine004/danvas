// For examples/static_serial: prints A0 and A1 ten times a second, and echoes
// any line it is sent (which shows in the app's log).
void setup() {
  Serial.begin(115200);
}

void loop() {
  Serial.print(analogRead(A0));
  Serial.print(',');
  Serial.println(analogRead(A1));
  while (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    Serial.print("got: ");
    Serial.println(line);
  }
  delay(100);
}
