// Compose the lockup: the badge mark + "jobscout" set in Inter SemiBold.
// The glyphs are outlines, so the file stands alone anywhere.
const fs = require('fs');
const m = JSON.parse(fs.readFileSync('wordmark-metrics.json', 'utf8'));
const body = fs.readFileSync('wordmark-body.svg', 'utf8').trim();

const MARK = 64;                    // the mark's canvas
const GAP = 16;                     // between mark and wordmark
const XH = 32;                      // wordmark x-height = half the mark
const s = XH / m.ink.xHeight;       // scale for the em-100 outlines
const w = m.ink.width * s;
const h = m.ink.height * s;
const top = MARK / 2 - m.ink.xBandCentre * s;   // x-band centred on the mark
const width = MARK + GAP + w;

const badge = `<g>
    <rect x="2" y="2" width="60" height="60" rx="15" fill="url(#field)"/>
    <rect x="2.75" y="2.75" width="58.5" height="58.5" rx="14.25" fill="none"
          stroke="#ffffff" stroke-opacity="0.22" stroke-width="1.5"/>
    <circle cx="32" cy="32" r="14.8" fill="#ffffff" fill-opacity="0.08"/>
    <circle cx="32" cy="32" r="14.8" fill="none" stroke="#ffffff"
            stroke-opacity="0.95" stroke-width="2.4"/>
    <circle cx="32" cy="32" r="6.6" fill="url(#lit)"/>
  </g>`;

const defs = `  <defs>
    <linearGradient id="field" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#4f7bf5"/>
      <stop offset="1" stop-color="#1b3fa8"/>
    </linearGradient>
    <radialGradient id="lit" cx="0.5" cy="0.4" r="0.8">
      <stop offset="0" stop-color="#ffdf85"/>
      <stop offset="1" stop-color="#e3b341"/>
    </radialGradient>
  </defs>`;

function svg(ink, label) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width.toFixed(1)} ${MARK}" role="img" aria-label="${label}">
  <title>${label}</title>
  <!-- The jobscout lockup. The wordmark is Inter SemiBold (SIL OFL 1.1,
       https://rsms.me/inter/) at -0.01em tracking, the same setting the app's
       header uses, converted to outlines so this file needs no font installed.
       Regenerate with packaging/wordmark.py rather than editing the paths,
       and keep the two variants in step with the ink they sit on. -->
${defs}
  ${badge.replace(/\n/g, '\n  ')}
  <g transform="translate(${(MARK + GAP).toFixed(3)} ${top.toFixed(3)}) scale(${s.toFixed(6)})"
     fill="${ink}">
${body.replace(/^/gm, '    ')}
  </g>
</svg>
`;
}

fs.writeFileSync('logo-wordmark.svg', svg('#1d1d1f', 'jobscout'));
fs.writeFileSync('logo-wordmark-dark.svg', svg('#ececf0', 'jobscout'));
console.log('lockup:', width.toFixed(1), 'x', MARK,
            '| text ink', w.toFixed(1), 'x', h.toFixed(1),
            '| scale', s.toFixed(4), '| text top', top.toFixed(2));
