/*
  Tallyo card reader  (Arduino Uno + RC522)

  Reads the ID of a card held against the RC522 and sends it to the computer over USB.
  The Python program reader_bridge.py on the computer does the rest.

  What this sketch sends (one line each, 115200 baud):
    UID A3 4F 91 0C     a card was tapped
    # hb ok             every 2 seconds: the RC522 module is answering
    # hb fault          every 2 seconds: the RC522 module is NOT answering (check wiring)
    # ...               other information
  What it understands back:
    RESULT ok | again | denied     lights the LEDs and beeps (optional hardware)

  Wiring and setup: see arduino/README.md.   Library needed: "MFRC522" by GithubCommunity.
*/
#include <SPI.h>
#include <MFRC522.h>
#include "uid_format.h"

// ---- Pins (Arduino Uno) ----------------------------------------------------
const byte PIN_SS      = 10;   // RC522 pin SDA (also called SS)
const byte PIN_RST     = 9;    // RC522 pin RST
const byte PIN_LED_OK  = 7;    // optional green LED (with a 220 ohm resistor)
const byte PIN_LED_BAD = 6;    // optional red LED   (with a 220 ohm resistor)
const byte PIN_BUZZER  = 5;    // optional piezo buzzer
// SCK = 13, MOSI = 11, MISO = 12 are fixed by the Uno's SPI hardware.

// ---- Timing ----------------------------------------------------------------
const unsigned long BAUD          = 115200;
const unsigned long HEARTBEAT_MS  = 2000;   // how often we say "I'm alive"
const unsigned long SAME_CARD_MS  = 1500;   // ignore the same card seen again this soon

MFRC522 rfid(PIN_SS, PIN_RST);
unsigned long lastBeat = 0;
unsigned long lastTap = 0;
char lastUid[UID_TEXT_SIZE] = "";
char command[24];
byte commandLength = 0;

// A working RC522 reports version 0x91 or 0x92. 0x00 or 0xFF means it isn't answering.
bool readerAnswers() {
  byte version = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  return version != 0x00 && version != 0xFF;
}

void flashAndBeep(byte ledPin, byte times, unsigned int ms) {
  for (byte i = 0; i < times; i++) {
    digitalWrite(ledPin, HIGH);
    tone(PIN_BUZZER, ledPin == PIN_LED_BAD ? 400 : 2000, ms);
    delay(ms);
    digitalWrite(ledPin, LOW);
    if (i + 1 < times) delay(ms);
  }
}

void handleCommand(const char *line) {
  if (strcmp(line, "RESULT ok") == 0)          flashAndBeep(PIN_LED_OK, 1, 120);
  else if (strcmp(line, "RESULT again") == 0)  flashAndBeep(PIN_LED_OK, 2, 70);
  else if (strcmp(line, "RESULT denied") == 0) flashAndBeep(PIN_LED_BAD, 1, 600);
}

// Collects characters until a line ends. Over-long lines are cut so they can never overflow.
void readCommands() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (commandLength > 0) {
        command[commandLength] = '\0';
        handleCommand(command);
        commandLength = 0;
      }
    } else if (commandLength < sizeof(command) - 1) {
      command[commandLength++] = c;
    }
  }
}

void heartbeat() {
  if (millis() - lastBeat < HEARTBEAT_MS) return;
  lastBeat = millis();
  if (readerAnswers()) {
    Serial.println(F("# hb ok"));
  } else {
    Serial.println(F("# hb fault"));
    rfid.PCD_Init();                       // try to wake the module up again
  }
}

void readCard() {
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;

  char text[UID_TEXT_SIZE];
  formatUid(rfid.uid.uidByte, rfid.uid.size, text);

  unsigned long now = millis();
  bool sameCardAgain = strcmp(text, lastUid) == 0 && (now - lastTap) < SAME_CARD_MS;
  if (!sameCardAgain) {
    Serial.print(F("UID "));
    Serial.println(text);
    strcpy(lastUid, text);
    lastTap = now;
  }
  rfid.PICC_HaltA();                       // stop talking to this card until it is taken away and tapped again
  rfid.PCD_StopCrypto1();
}

void setup() {
  Serial.begin(BAUD);
  pinMode(PIN_LED_OK, OUTPUT);
  pinMode(PIN_LED_BAD, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  SPI.begin();
  rfid.PCD_Init();
  delay(50);
  Serial.println(F("# tallyo-reader 1.0 ready"));
  if (readerAnswers()) {
    Serial.println(F("# rc522 ok"));
  } else {
    Serial.println(F("# rc522 ERROR: no answer. Check the wiring and use 3.3V, not 5V."));
  }
  flashAndBeep(PIN_LED_OK, 1, 150);        // a blink at power-on shows the board is alive
}

void loop() {
  heartbeat();
  readCommands();
  readCard();
}
