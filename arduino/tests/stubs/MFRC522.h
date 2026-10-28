// A fake card reader: the test decides whether a card is present and what version it reports.
#pragma once
#include "Arduino.h"
struct FakeCard { bool present = false; byte size = 0; byte bytes[10] = {0}; };
extern FakeCard fake_card;
extern byte fake_version;
class MFRC522 {
public:
  enum PCD_Register : byte { VersionReg = 0x37 };
  struct Uid { byte size; byte uidByte[10]; };
  Uid uid;
  static int init_calls;
  MFRC522(byte, byte) { uid.size = 0; }
  void PCD_Init() { init_calls++; }
  byte PCD_ReadRegister(PCD_Register) { return fake_version; }
  bool PICC_IsNewCardPresent() { return fake_card.present; }
  bool PICC_ReadCardSerial() { uid.size = fake_card.size; memcpy(uid.uidByte, fake_card.bytes, 10); return true; }
  void PICC_HaltA() { fake_card.present = false; }     // like the real module: not "new" again until re-presented
  void PCD_StopCrypto1() {}
};
