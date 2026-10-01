import {build} from 'esbuild';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {gzipSync} from 'node:zlib';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const output = path.join(root, 'static/scene3d');
await mkdir(output, {recursive:true});
await build({entryPoints:[path.join(root,'scripts/scene3d-src/main.ts')],
  outfile:path.join(output,'scene.js'), bundle:true, minify:true,
  format:'esm', target:'es2022', legalComments:'eof'});
// A read-only, byte-exact copy: the renderer shares era's surveyed graph and
// chainage precision. No backend endpoint or original map asset is modified.
const raw = await readFile(path.join(root, 'network.json'));
await writeFile(path.join(output, 'network.json.gz'), gzipSync(raw, {level:9}));
await writeFile(path.join(output, 'network-source.json'), JSON.stringify({
  source:'network.json', sha256:createHash('sha256').update(raw).digest('hex'),
  bytes:raw.length, modelsBranch:'M_part', modelsCommit:'5522f5cf3b631c41a6f886686e44928c65e70f3b',
  modelsBlob:'7da14bf4b9b660ed61cf713ecdf6ee9cff15cfbe',
}, null, 2)+'\n');
await writeFile(path.join(output, 'THREE-LICENSE.txt'), await readFile(new URL('./node_modules/three/LICENSE',import.meta.url)));
console.log('Built era 3D view and exact read-only network copy.');
