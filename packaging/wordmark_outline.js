// Produce the wordmark outlines once, so the shipped SVG depends on no font.
const fs = require('fs');
const opentype = require('opentype.js');

// The font ships inside the Inter release; wordmark.py finds it and passes it.
const FONT = process.env.FONT || '../extras/ttf/Inter-SemiBold.ttf';
const TEXT = 'jobscout';
const EM = 100;            // work at 100px/em; the lockup scales this
const TRACKING = -0.01;    // the app's own letter-spacing, in em

const font = opentype.parse(fs.readFileSync(FONT).buffer);
if (font.unitsPerEm !== 2048) throw new Error('unexpected unitsPerEm');
const scale = EM / font.unitsPerEm;

// --- lay the glyphs out ourselves: exact tracking, no kerning surprises
let pen = 0;
const glyphs = [];
for (const ch of TEXT) {
  const g = font.charToGlyph(ch);
  const p = g.getPath(0, 0, EM);   // already scaled to EM
  glyphs.push({ ch, d: p.toPathData(3), dx: pen, bb: p.getBoundingBox() });
  pen += g.advanceWidth * scale + TRACKING * EM;
}
const advance = pen - TRACKING * EM;

// --- ink box over the whole word. opentype.js hands back SVG-oriented
// coordinates already (y down, baseline at 0), so there is nothing to flip:
// just normalise the ink box to start at (0, 0).
const x1 = Math.min(...glyphs.map(g => g.bb.x1 + g.dx));
const x2 = Math.max(...glyphs.map(g => g.bb.x2 + g.dx));
const y1 = Math.min(...glyphs.map(g => g.bb.y1));   // topmost ink (ascender)
const y2 = Math.max(...glyphs.map(g => g.bb.y2));   // bottom ink (descender)

const W = x2 - x1, H = y2 - y1;
const X = v => +(v - x1).toFixed(3);
const Y = v => +(v - y1).toFixed(3);

const ARGS = { M: 2, L: 2, Q: 4, C: 6, Z: 0 };
function rewrite(d, dx) {
  const toks = d.match(/[A-Za-z]|-?\d*\.?\d+(?:e[-+]?\d+)?/g) || [];
  let i = 0, cmd = null, out = [];
  const num = () => Number(toks[i++]);
  while (i < toks.length) {
    const t = toks[i];
    if (/[A-Za-z]/.test(t)) {
      if (/[a-z]/.test(t)) throw new Error('relative/unsupported command: ' + t);
      cmd = t; i++;
      if (cmd === 'Z') { out.push('Z'); cmd = null; continue; }
      if (!(cmd in ARGS)) throw new Error('unhandled command: ' + cmd);
    }
    if (cmd === null) throw new Error('number with no command at token ' + i);
    switch (cmd) {           // one argument set; repeats carry the same command
      case 'M': case 'L':
        out.push(cmd, X(num() + dx), Y(num())); break;
      case 'Q':
        out.push('Q', X(num() + dx), Y(num()), X(num() + dx), Y(num())); break;
      case 'C':
        out.push('C', X(num() + dx), Y(num()), X(num() + dx), Y(num()),
                      X(num() + dx), Y(num())); break;
      default: throw new Error('unhandled: ' + cmd);
    }
    if (i < toks.length && /[A-Za-z]/.test(toks[i]) && toks[i] !== 'Z') {
      // implicit repetition ends at the next explicit command
      continue;
    }
  }
  return out.join(' ');
}

const body = glyphs.map(g =>
  `  <!-- ${g.ch} -->\n  <path d="${rewrite(g.d, g.dx)}"/>`).join('\n');

const xHeight = font.tables.os2.sxHeight * scale;
const metrics = {
  family: 'Inter', weight: 600, version: '4.1', license: 'SIL OFL 1.1',
  em: EM, trackingEm: TRACKING, text: TEXT,
  advance: +advance.toFixed(3),
  ink: { width: +W.toFixed(3), height: +H.toFixed(3),
         baselineFromTop: +Y(0).toFixed(3),
         xHeight: +xHeight.toFixed(3),
         xBandTop: +Y(-xHeight).toFixed(3),
         xBandCentre: +(Y(0) - xHeight / 2).toFixed(3) },
};
fs.writeFileSync('wordmark-metrics.json', JSON.stringify(metrics, null, 2));
fs.writeFileSync('wordmark-body.svg', body + '\n');
console.log(JSON.stringify(metrics, null, 2));
console.log('baseline from ink top:', metrics.ink.baseline,
            ' x-height:', metrics.ink.xHeight,
            ' -> x-band centres at', (metrics.ink.baseline - metrics.ink.xHeight / 2).toFixed(3));
