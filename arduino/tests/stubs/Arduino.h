// Stand-ins for the Arduino core, so the sketch's logic can run on a normal computer.
#pragma once
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
typedef uint8_t byte;
#define F(x) (x)
#define HIGH 1
#define LOW 0
#define OUTPUT 1
struct FakeSerial {
  std::string out, in; size_t pos = 0;
  void begin(unsigned long) {}
  void print(const char *s) { out += s; }
  void println(const char *s) { out += s; out += "\n"; }
  int available() { return (int)(in.size() - pos); }
  int read() { return pos < in.size() ? (unsigned char)in[pos++] : -1; }
};
extern FakeSerial Serial;
extern unsigned long fake_millis;
extern std::vector<std::string> pin_log;
inline unsigned long millis() { return fake_millis; }
inline void delay(unsigned long ms) { fake_millis += ms; }
inline void pinMode(byte, byte) {}
inline void digitalWrite(byte pin, byte v) { pin_log.push_back("pin" + std::to_string(pin) + (v ? "=1" : "=0")); }
inline void tone(byte pin, unsigned int, unsigned long) { pin_log.push_back("tone" + std::to_string(pin)); }
