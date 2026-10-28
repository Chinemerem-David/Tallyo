// Turns the raw bytes of a card ID into text like "A3 4F 91 0C".
// Kept separate from the sketch so it can also be tested on a normal computer.
#ifndef UID_FORMAT_H
#define UID_FORMAT_H

const unsigned char UID_MAX_BYTES = 10;               // cards have 4, 7 or 10 bytes
const unsigned char UID_TEXT_SIZE = UID_MAX_BYTES * 3; // "AA " per byte: 2 digits + a space, the last space is the end marker

// Writes the ID into `out` (capital letters, two digits per byte). Returns the length written.
inline unsigned char formatUid(const unsigned char *bytes, unsigned char size, char *out) {
  static const char HEX_DIGITS[] = "0123456789ABCDEF";
  if (size > UID_MAX_BYTES) size = UID_MAX_BYTES;
  unsigned char pos = 0;
  for (unsigned char i = 0; i < size; i++) {
    if (i > 0) out[pos++] = ' ';
    out[pos++] = HEX_DIGITS[bytes[i] >> 4];
    out[pos++] = HEX_DIGITS[bytes[i] & 0x0F];
  }
  out[pos] = '\0';
  return pos;
}

#endif
