import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const read = file => readFileSync(new URL('../static/' + file, import.meta.url), 'utf8');
const files = ['i18n-static.js', 'i18n-dynamic.js', 'i18n-messages.js', 'i18n-map.js'];
function harness(saved, blocked = false) {
  const handlers = {}, events = [], selectors = [{value: 'en'}, {value: 'en'}];
  const storage = new Map(saved ? [['railflow.language', saved]] : []);
  const window = {addEventListener: (name, callback) => { handlers[name] = callback; }, dispatchEvent: event => { events.push(event); }};
  window.parent = window;
  const document = {readyState: 'loading', documentElement: {lang: 'en'},
    addEventListener: (name, callback) => { handlers[name] = callback; },
    querySelectorAll: selector => selector === '[data-language-select]' ? selectors : [], getElementById: () => null};
  const context = {window, document, location: {origin: 'https://railflow.example'},
    localStorage: {getItem: key => { if (blocked) throw Error('blocked'); return storage.get(key); },
      setItem: (key, value) => { if (blocked) throw Error('blocked'); storage.set(key, value); }},
    CustomEvent: class {constructor(type, options) { this.type = type; this.detail = options.detail; }}};
  runInNewContext(read('i18n.js'), context);
  context.RailI18n = window.RailI18n;
  for (const file of files) runInNewContext(read(file), context);
  handlers.DOMContentLoaded();
  return {i18n: window.RailI18n, handlers, events, selectors, document, storage, window};
}

test('languages persist, synchronize selectors and use ISO document language codes', () => {
  const h = harness();
  assert.equal(h.i18n.language, 'en');
  h.i18n.setLanguage('kk');
  assert.equal(h.i18n.t('Username'), 'Пайдаланушы аты');
  assert.equal(h.document.documentElement.lang, 'kk');
  assert.deepEqual(h.selectors.map(s => s.value), ['kk', 'kk']);
  assert.equal(h.storage.get('railflow.language'), 'kk');
  assert.equal(h.events.at(-1).detail.language, 'kk');
  assert.equal(harness('ru').i18n.t('Username'), 'Имя пользователя');
  h.i18n.setLanguage('en');
  assert.equal(h.i18n.t('Username'), 'Username');
  const count = h.events.length;
  assert.equal(h.i18n.setLanguage('unsupported'), false);
  assert.equal(h.i18n.language, 'en');
  assert.equal(h.events.length, count);
});

test('unavailable storage and invalid saved language never prevent switching', () => {
  assert.equal(harness('broken').i18n.language, 'en');
  const h = harness(null, true);
  assert.doesNotThrow(() => h.i18n.setLanguage('ru'));
  assert.equal(h.i18n.language, 'ru');
  h.handlers.storage({key: 'railflow.language', newValue: 'kk'});
  assert.equal(h.i18n.language, 'kk');
});

test('backend templates preserve IDs, numbers and unknown content with English fallback', () => {
  const {i18n} = harness('ru');
  const source = 'Movement authorised to section boundary at 5000.5 m; dispatch score 9.25. Highest feasible preference in this decision.';
  assert.match(i18n.message(source), /граница участка на 5000\.5 м/);
  assert.match(i18n.message(source), /9\.25/);
  assert.equal(i18n.message('Train <b>custom</b> name'), 'Train <b>custom</b> name');
  assert.equal(i18n.message('Custom trains'), 'Custom trains', 'Unit templates must not translate arbitrary names ending in s');
  assert.equal(i18n.message('Unknown vertex V321'), 'Неизвестная вершина V321');
  assert.equal(i18n.message('KZ-009 cleared'), 'KZ-009 устранено');
  assert.match(i18n.message('Following signal authority; E4 occupied by KZ-009'), /KZ-009/);
  i18n.setLanguage('en');
  assert.equal(i18n.message(source), source);
});

test('static translations update text and accessibility without replacing form inputs', () => {
  const {i18n} = harness('ru');
  const input = {value: 'User draft', attributes: {'data-i18n-placeholder': 'Username', 'data-i18n-aria-label': 'Username'},
    hasAttribute(key) { return key in this.attributes; }, getAttribute(key) { return this.attributes[key]; },
    setAttribute(key, value) { this.attributes[key] = value; }};
  i18n.translate({querySelectorAll: () => [input]});
  assert.equal(input.value, 'User draft');
  assert.equal(input.attributes.placeholder, 'Имя пользователя');
  assert.equal(input.attributes['aria-label'], 'Имя пользователя');
});

test('switching document language preserves decimal drafts even if native inputs reparse', () => {
  const h = harness('ru'), input = {value: '2.3'};
  const query = h.document.querySelectorAll;
  h.document.querySelectorAll = selector => selector === 'input[type="number"]' ? [input] : query(selector);
  let lang = 'ru';
  Object.defineProperty(h.document.documentElement, 'lang', {
    get: () => lang, set: value => { lang = value; input.value = ''; }
  });
  h.i18n.setLanguage('en');
  assert.equal(input.value, '2.3');
  assert.equal(h.document.documentElement.lang, 'en');
});

test('every catalog translation retains interpolation values and every static annotation is covered', () => {
  const catalog = new Map();
  const RailI18n = {add: entries => Object.entries(entries).forEach(entry => catalog.set(...entry))};
  for (const file of files) runInNewContext(read(file), {RailI18n, window: {RailI18n}});
  const slots = source => [...source.matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort();
  for (const [source, translations] of catalog) {
    assert.equal(translations.length, 2, source);
    for (const translation of translations) {
      assert.ok(translation.trim(), source);
      assert.deepEqual(slots(translation), slots(source), source);
    }
  }
  const decode = source => source.replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#10;/g, '\n').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
  for (const html of [read('index.html'), read('scene3d/index.html')]) {
    for (const match of html.matchAll(/data-i18n(?:-html|-placeholder|-title|-aria-label)?="([^"]+)"/g)) {
      assert.ok(catalog.has(decode(match[1])), `Missing translation: ${decode(match[1])}`);
    }
  }
});
