/**
 * natural_key из export_service.py: «A-2» раньше «A-10». Сравнение — как у
 * списков в Python: поэлементно, строки по кодовым точкам, числа по значению.
 */
type KeyPart = string | bigint;

// Нули десятичных систем Unicode, которые может вернуть \d Python. Ячейки склада
// пишутся ASCII-цифрами; остальные системы — на случай экзотики.
const DIGIT_ZEROS = [
  0x30, 0x660, 0x6f0, 0x7c0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66, 0xbe6, 0xc66, 0xce6,
  0xd66, 0xde6, 0xe50, 0xed0, 0xf20, 0x1040, 0x1090, 0x17e0, 0x1810, 0xff10,
];

function digitsToBigInt(s: string): bigint {
  let n = 0n;
  for (const ch of s) {
    const cp = ch.codePointAt(0)!;
    const zero = DIGIT_ZEROS.find((z) => cp >= z && cp < z + 10) ?? cp;
    n = n * 10n + BigInt(cp - zero);
  }
  return n;
}

export function naturalKey(text: string): KeyPart[] {
  // split с группой, как re.split(r'(\d+)'): нечётные элементы — числа.
  return text.split(/(\p{Nd}+)/u).map((part, i) => (i % 2 === 1 ? digitsToBigInt(part) : part.toLowerCase()));
}

/** Сравнение строк как в Python — по кодовым точкам, а не UTF-16. */
export function comparePyStrings(a: string, b: string): number {
  const ai = a[Symbol.iterator]();
  const bi = b[Symbol.iterator]();
  for (;;) {
    const x = ai.next();
    const y = bi.next();
    if (x.done || y.done) return x.done && y.done ? 0 : x.done ? -1 : 1;
    const d = x.value.codePointAt(0)! - y.value.codePointAt(0)!;
    if (d !== 0) return d;
  }
}

function comparePart(a: KeyPart, b: KeyPart): number {
  if (typeof a === 'bigint' && typeof b === 'bigint') return a < b ? -1 : a > b ? 1 : 0;
  if (typeof a === 'string' && typeof b === 'string') return comparePyStrings(a, b);
  return typeof a === 'bigint' ? -1 : 1; // в Python здесь TypeError; на практике не встречается
}

export function compareKeys(a: KeyPart[], b: KeyPart[]): number {
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    const d = comparePart(a[i], b[i]);
    if (d !== 0) return d;
  }
  return a.length - b.length;
}

