const fs = require('fs');
const h = fs.readFileSync('webui.html', 'utf8');
const blocks = h.match(/<script>([\s\S]*?)<\/script>/g) || [];
let ok = true;
blocks.forEach((s, i) => {
  let code = s.replace(/^<script>/, '').replace(/<\/script>$/, '');
  code = code.replace(/^\s*\/\/\s*<!\[CDATA\[|\]\]>\s*$/g, '');
  try {
    new Function(code);
    console.log('block' + i + ' OK len=' + code.length);
  } catch (e) {
    ok = false;
    console.log('block' + i + ' ERR: ' + e.message);
    // find approximate location
    const idx = e.stack ? -1 : -1;
  }
});
console.log(ok ? 'ALL_OK' : 'HAS_ERROR');
