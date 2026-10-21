import { cp, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
for (const folder of ['cmaps', 'standard_fonts', 'wasm', 'iccs']) {
  const target = resolve('public/vendor/pdfjs', folder);
  await mkdir(target, { recursive: true });
  await cp(resolve('node_modules/pdfjs-dist', folder), target, { recursive: true });
}
