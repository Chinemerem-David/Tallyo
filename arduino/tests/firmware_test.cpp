// Runs the real sketch (tallyo_reader.ino) on a normal computer against fake hardware.
// Build and run:  see tests/test_firmware.py   (or: g++ -std=c++11 -Istubs -include Arduino.h firmware_test.cpp)
#include "Arduino.h"
#include "SPI.h"
#include "MFRC522.h"
FakeSerial Serial; unsigned long fake_millis = 0; std::vector<std::string> pin_log;
FakeCard fake_card; byte fake_version = 0x92; SPIClass SPI; int MFRC522::init_calls = 0;
#include "../tallyo_reader/tallyo_reader.ino"
#include <initializer_list>
#include <iostream>

static int count(const std::string &text, const std::string &needle) {
  int n = 0; for (size_t p = text.find(needle); p != std::string::npos; p = text.find(needle, p + 1)) n++; return n;
}
static void present(std::initializer_list<byte> bytes) {
  fake_card.present = true; fake_card.size = (byte)bytes.size(); int i = 0; for (byte b : bytes) fake_card.bytes[i++] = b;
}
static void run(unsigned long ms) { unsigned long end = fake_millis + ms; while (fake_millis < end) { loop(); fake_millis += 10; } }
#define CHECK(cond) do { if (!(cond)) { std::cerr << "FAILED line " << __LINE__ << ": " #cond "\n"; return 1; } } while (0)

int main() {
  setup();
  CHECK(count(Serial.out, "# tallyo-reader 1.0 ready\n") == 1);
  CHECK(count(Serial.out, "# rc522 ok\n") == 1);
  CHECK(!pin_log.empty() && pin_log[0] == "pin7=1");                       // power-on blink
  Serial.out.clear();

  run(4100);                                                               // heartbeat every 2 seconds
  CHECK(count(Serial.out, "# hb ok\n") == 2);

  present({0xA3, 0x4F, 0x91, 0x0C}); run(100);                             // a card is tapped
  CHECK(count(Serial.out, "UID A3 4F 91 0C\n") == 1);
  present({0xA3, 0x4F, 0x91, 0x0C}); run(100);                             // the same card again at once
  CHECK(count(Serial.out, "UID A3 4F 91 0C\n") == 1);
  run(2000); present({0xA3, 0x4F, 0x91, 0x0C}); run(100);                  // and again after the pause
  CHECK(count(Serial.out, "UID A3 4F 91 0C\n") == 2);
  present({0x04, 0x0A, 0x00, 0xFF, 0x01, 0x02, 0x03}); run(100);           // a 7-byte card with zeros in it
  CHECK(count(Serial.out, "UID 04 0A 00 FF 01 02 03\n") == 1);
  present({0x11, 0x22, 0x33, 0x44}); run(100);                             // a different card straight after
  CHECK(count(Serial.out, "UID 11 22 33 44\n") == 1);

  Serial.out.clear(); fake_version = 0x00; run(2100);                      // module stops answering
  CHECK(count(Serial.out, "# hb fault\n") >= 1);
  CHECK(MFRC522::init_calls >= 2);                                         // it tried to wake it up
  Serial.out.clear(); fake_version = 0x92; run(2100);                      // and recovers
  CHECK(count(Serial.out, "# hb ok\n") >= 1 && count(Serial.out, "# hb fault") == 0);

  pin_log.clear(); Serial.in = "RESULT ok\n"; Serial.pos = 0; run(400);
  CHECK(count(pin_log[0] + pin_log[1], "pin7=1") == 1 && count(pin_log[1], "tone5") + count(pin_log[0], "tone5") == 1);
  pin_log.clear(); Serial.in = "RESULT again\r\n"; Serial.pos = 0; run(600);
  int green = 0; for (auto &p : pin_log) if (p == "pin7=1") green++;
  CHECK(green == 2);                                                        // two short blinks
  pin_log.clear(); Serial.in = "RESULT denied\n"; Serial.pos = 0; run(1000);
  int red = 0; for (auto &p : pin_log) if (p == "pin6=1") red++;
  CHECK(red == 1);
  pin_log.clear(); Serial.in = "HELLO\nRESULT\nRESULT okay\n"; Serial.pos = 0; run(400);
  CHECK(pin_log.empty());                                                   // unknown commands do nothing
  Serial.in = std::string(300, 'x') + "\nRESULT ok\n"; Serial.pos = 0; pin_log.clear(); run(400);   // a flood of junk can't break it
  CHECK(!pin_log.empty());

  // A fresh start for a clean transcript the Python side checks against the real bridge code.
  Serial.out.clear(); fake_millis = 0; lastBeat = 0; lastUid[0] = '\0'; Serial.in.clear(); Serial.pos = 0;
  setup(); run(2100); present({0xA3, 0x4F, 0x91, 0x0C}); run(100); present({0xDE, 0xAD, 0xBE, 0xEF}); run(100);
  std::cout << "TRANSCRIPT_BEGIN\n" << Serial.out << "TRANSCRIPT_END\n";
  std::cout << "firmware logic: all checks passed\n";
  return 0;
}
