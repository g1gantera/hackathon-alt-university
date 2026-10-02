import test,{afterEach} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';
import {catalog} from '../src/i18n/catalog.ts';
import {translate,displayText,setLanguage,getLanguage,storedLanguage,subscribeLanguage,intlLocale} from '../src/i18n/core.ts';
import {qualityNames} from '../src/quality.ts';

afterEach(()=>setLanguage('ru'));
test('saved language validates input and handles inaccessible storage',()=>{
 for(const value of ['kk','ru','en'])assert.equal(storedLanguage({getItem:()=>value}),value);
 for(const value of ['xx','',null])assert.equal(storedLanguage({getItem:()=>value}),'ru');
 assert.equal(storedLanguage({getItem:()=>{throw Error('denied')}}),'ru');
});
test('every translation preserves placeholders and has English and Kazakh text',()=>{
 const placeholders=s=>[...s.matchAll(/\{\d+\}/g)].map(m=>m[0]).sort();
 for(const [key,entries] of Object.entries(catalog)){
  assert.ok(entries[0]?.trim(),key+' English');assert.ok(entries[1]?.trim(),key+' Kazakh');
  for(const entry of entries.filter(Boolean))assert.deepEqual(placeholders(entry),placeholders(key),key);
 }
});
test('all literal UI translation calls exist in the catalog',()=>{
 const root=new URL('../src/',import.meta.url),missing=[];
 function inspect(directory){for(const entry of fs.readdirSync(directory,{withFileTypes:true})){
  const filename=path.join(directory,entry.name);
  if(entry.isDirectory()){inspect(filename);continue;}
  if(!/\.tsx?$/.test(filename))continue;
  const source=ts.createSourceFile(filename,fs.readFileSync(filename,'utf8'),ts.ScriptTarget.Latest,true);
  function visit(node){
   if(ts.isCallExpression(node)&&node.expression.getText(source)==='translate'&&node.arguments[0]&&ts.isStringLiteral(node.arguments[0])){
    const key=node.arguments[0].text.trim();if(!Object.hasOwn(catalog,key))missing.push(`${entry.name}: ${key}`);
   }
   ts.forEachChild(node,visit);
  }visit(source);
 }}inspect(fileURLToPath(root));
 assert.deepEqual(missing,[]);
});
test('station names, routes, units and dynamic backend messages follow selected language',()=>{
 setLanguage('en');
 assert.equal(translate('Язык'),'Language');
 assert.equal(displayText('Астана → Кокшетау'),'Astana → Kokshetau');
 assert.equal(translate(' км'),' km');
 const message=translate('Станция {0}','Кокшетау');
 assert.equal(message,'Station Kokshetau');
 setLanguage('kk');
 assert.equal(translate('Язык'),'Тіл');
 assert.equal(displayText('Astana → Kokshetau'),'Астана → Көкшетау');
 assert.equal(displayText(message),translate('Станция {0}','Кокшетау'));
 assert.equal(intlLocale(),'kk-KZ');
 setLanguage('ru');assert.equal(displayText(message),'Станция Кокшетау');
});
test('language changes notify subscribers without changing data or protocol identifiers',()=>{
 let updates=0;const unsubscribe=subscribeLanguage(()=>updates++);
 setLanguage('en');setLanguage('en');assert.equal(updates,1);assert.equal(getLanguage(),'en');
 const snapshot={train_id:'T001',status:'moving',speed_mps:22};
 assert.equal(displayText(snapshot),snapshot);assert.equal(displayText('T001'),'T001');assert.equal(displayText(22),22);
 unsubscribe();setLanguage('kk');assert.equal(updates,1);
});
test('module-level metric labels update without reloading modules',()=>{
 setLanguage('en');assert.equal(qualityNames.energy,'Energy efficiency');
 setLanguage('ru');assert.equal(qualityNames.energy,'Энергоэффективность');
 setLanguage('kk');assert.equal(qualityNames.energy,translate('Энергоэффективность'));
});
