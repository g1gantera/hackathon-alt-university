import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync,readdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join,relative} from 'node:path';

const root=fileURLToPath(new URL('../../',import.meta.url));
// Era baseline d0a56951f87e96b82d4e60721b9f3be2e5707048.
// The 3D integration is a presentation-only adapter; these Git blob identities
// deliberately pin the existing dispatcher, graph and 2D map byte for byte.
const protectedBlobs={
 'backend/__init__.py':'e69de29bb2d1d6434b8b29ae775ad8c2e48c5391',
 'backend/app.py':'a8d86e6f533402070cba1cb2694cc4d951e089cd',
 'backend/ato.py':'3bf9ffc6ca714fe96558435a9de76de8ab7c92c5',
 'backend/blocks.py':'ad7d53d98d648c48d286706938250a434969ae84',
 'backend/dispatch.py':'06fad0d8a497db3d758956ec53ef472734154aa9',
 'backend/engine.py':'ab91b24d77363b71e6695e741efac7c8f5c3738b',
 'backend/history.py':'266c41a51ec1e0a1697e0e1e5fa44096e5fe72dc',
 'backend/meets.py':'0c5e44a72acc0f58655130457c240d6e2282b582',
 'backend/models.py':'fc6459cac28634e5bfcbe992f478bf216b794ed1',
 'backend/network.py':'c8ad58121f8b4f701a3675f11a284b1107c6b5ce',
 'backend/quality.py':'0090e1eedf7cf52e73229b1e919f3f63bd5e5d95',
 'network.json':'2569b4048f9324c03eb41f79e7bc2ad92d885c69',
 'map_data.js':'4f21e16bbfa73a834aec924a906b5c8776efd547',
 'map.html':'1d6d1809ba28442d8da6c79e16cf080714210763',
 'build_network.py':'215cd143743a7314a19ade4858168e0b31e16c7a',
 'static/app.js':'208af7ae56500b3def0aacd60152f96b3fa1bb0b',
 'static/map-overlay.js':'4b2fd9dfd556c1944f5c248267bdf4047a02e010',
};

test('era dispatcher, graph, source map and existing dashboard logic remain byte-identical',()=>{
 for(const [path,expected] of Object.entries(protectedBlobs)){
  const bytes=readFileSync(join(root,path));
  const actual=createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex');
  assert.equal(actual,expected,`${path} changed from the protected era baseline`);
 }
});

test('the visual transfer introduces no second backend or simulation Python modules',()=>{
 const sources=[];
 function walk(directory){
  for(const entry of readdirSync(directory,{withFileTypes:true})){
   const path=join(directory,entry.name);
   if(entry.isDirectory()&&entry.name!=='__pycache__')walk(path);
   else if(entry.isFile()&&entry.name.endsWith('.py'))sources.push(relative(root,path).replaceAll('\\','/'));
  }
 }
 walk(join(root,'backend'));
 assert.deepEqual(sources.sort(),Object.keys(protectedBlobs).filter(path=>path.startsWith('backend/')).sort());
});
