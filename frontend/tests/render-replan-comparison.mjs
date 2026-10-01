// Render the comparison with real API data, plus a legacy archive variant.
// Usage: node frontend/tests/render-replan-comparison.mjs path/to/fixture.json
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const server=await createServer({root:fileURLToPath(new URL('..',import.meta.url)),server:{middlewareMode:true},appType:'custom',optimizeDeps:{noDiscovery:true,entries:[],include:[]}});
try {
 const {ReplanComparisonView}=await server.ssrLoadModule('/src/ReplanComparisonView.tsx');
 const render=props=>renderToStaticMarkup(React.createElement(ReplanComparisonView,props));
 const props={snapshot:fixture.snapshot,topology:fixture.topology,comparison:fixture.snapshot.replan_status.comparison,mode:'applied'};
 const html=render(props);
 for(const text of ['Индекс качества','Энергия за весь сценарий','Изменение','Конечное прибытие поездов','Освобождение хвостом','Последний расчёт'])assert.ok(html.includes(text),text);
 assert.ok(html.includes('До: неприменим'));
 assert.ok(html.includes('После: проверка пройдена'));
 assert.ok(!html.includes('NaN')&&!html.includes('undefined'));
 const legacy=structuredClone(props.comparison);
 delete legacy.evaluated_at_s;delete legacy.calculation_s;delete legacy.committed_total;
 for(const m of legacy.changes){delete m.before_arrival_s;delete m.after_arrival_s;delete m.before_release_s;delete m.after_release_s;}
 const historical=render({...props,comparison:legacy,historical:true});
 assert.ok(historical.includes('Архив')&&historical.includes('момент расчёта'));
 assert.ok(!historical.includes('NaN')&&!historical.includes('undefined'));
 console.log(JSON.stringify({status:'passed',current_render:true,legacy_archive_render:true,characters:html.length}));
}finally{await server.close();}
