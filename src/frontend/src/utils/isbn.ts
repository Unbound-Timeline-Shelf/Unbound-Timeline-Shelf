// ISBN text as a person types it, read the way the server reads it
// (features/books/isbn.py): spaces and hyphens are ignored, a lowercase x is
// an X, and the check digit must be right. Nothing is ever guessed.

function isbn10Check(firstNine: string): string {
  let total = 0;
  for (let index = 0; index < 9; index++)
    total += (10 - index) * Number(firstNine[index]);
  const check = (11 - (total % 11)) % 11;
  return check === 10 ? "X" : String(check);
}

function isbn13Check(firstTwelve: string): string {
  let total = 0;
  for (let index = 0; index < 12; index++)
    total += Number(firstTwelve[index]) * (index % 2 ? 3 : 1);
  return String((10 - (total % 10)) % 10);
}

/** The ISBN-13 a typed ISBN-10 or ISBN-13 names, or null when it is not one. */
export function normalizeIsbn13(value: string): string | null {
  const text = value.replace(/[\s-]+/g, "").toUpperCase();
  if (/^\d{9}[\dX]$/.test(text)) {
    if (isbn10Check(text.slice(0, 9)) !== text[9]) return null;
    const body = `978${text.slice(0, 9)}`;
    return body + isbn13Check(body);
  }
  if (/^97[89]\d{10}$/.test(text))
    return isbn13Check(text.slice(0, 12)) === text[12] ? text : null;
  return null;
}
