// No-float guard: fails when frontend/src uses parseFloat, Number( or toFixed.
// Comments and string literals are ignored; money must stay a decimal string.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const srcDir = join(root, "src");
const EXT = /\.(ts|tsx|js|jsx|mjs)$/;
const PATTERNS = [
  { name: "parseFloat", re: /\bparseFloat\b/ },
  { name: "Number(", re: /(?<![\w$.])Number\s*\(/ },
  { name: "toFixed", re: /\.\s*toFixed\b|\btoFixed\s*\(/ },
];

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (EXT.test(name)) out.push(p);
  }
  return out;
}

// Blank out comments and string/template literal contents, keeping newlines.
function stripNoise(src) {
  let out = "";
  let i = 0;
  const blank = (s) => s.replace(/[^\n]/g, " ");
  while (i < src.length) {
    const c = src[i];
    const n = src[i + 1];
    if (c === "/" && n === "/") {
      const e = src.indexOf("\n", i);
      const end = e === -1 ? src.length : e;
      out += blank(src.slice(i, end));
      i = end;
    } else if (c === "/" && n === "*") {
      const e = src.indexOf("*/", i + 2);
      const end = e === -1 ? src.length : e + 2;
      out += blank(src.slice(i, end));
      i = end;
    } else if (c === '"' || c === "'" || c === "`") {
      let j = i + 1;
      while (j < src.length && src[j] !== c) {
        if (src[j] === "\\") j++;
        j++;
      }
      out += c + blank(src.slice(i + 1, j)) + (src[j] ?? "");
      i = j + 1;
    } else {
      out += c;
      i++;
    }
  }
  return out;
}

let matches = 0;
for (const file of walk(srcDir)) {
  const lines = stripNoise(readFileSync(file, "utf8")).split("\n");
  lines.forEach((line, idx) => {
    for (const { name, re } of PATTERNS) {
      if (re.test(line)) {
        console.error(`${relative(root, file)}:${idx + 1}: forbidden ${name}`);
        matches++;
      }
    }
  });
}

if (matches > 0) {
  console.error(`check-no-float: ${matches} match(es)`);
  process.exit(1);
}
console.log("check-no-float: clean (0 matches)");
