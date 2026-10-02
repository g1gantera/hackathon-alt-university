/* Shared presentation-only localization. API values and simulation state stay unchanged. */
(() => {
  'use strict';
  const storageKey = 'railflow.language';
  const supported = ['kk', 'ru', 'en'];
  const catalog = new Map();
  let templates = [];
  let language = 'en';
  try {
    const saved = localStorage.getItem(storageKey);
    if (supported.includes(saved)) language = saved;
  } catch { /* Language switching also works when browser storage is disabled. */ }

  function t(source, params = {}) {
    source = String(source ?? '');
    const entry = catalog.get(source);
    const translated = language === 'en' ? source : entry?.[language === 'kk' ? 0 : 1] || source;
    return translated.replace(/\{(\w+)\}/g, (token, key) =>
      Object.hasOwn(params, key) ? String(params[key]) : token);
  }

  function add(entries) {
    for (const [source, translations] of Object.entries(entries)) catalog.set(source, translations);
    templates = [...catalog.keys()].filter(source => /\{\w+\}/.test(source)).map(source => {
      const keys = [];
      let expression = '', last = 0;
      for (const match of source.matchAll(/\{(\w+)\}/g)) {
        const numeric = ['value', 'count', 'seconds', 'minutes', 'distance', 'length', 'usable', 'version', 'ui', 'score', 'vertex', 'stay', 'go'].includes(match[1]);
        const capture = numeric ? '([+−-]?[\\d][\\d.,\\s]*|—)' : match[1] === 'asset' ? '([^;\\n]+?)' : '([\\s\\S]*?)';
        expression += source.slice(last, match.index).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + capture;
        keys.push(match[1]);
        last = match.index + match[0].length;
      }
      expression += source.slice(last).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      return {source, keys, pattern: new RegExp('^' + expression + '$'), specificity: source.replace(/\{\w+\}/g, '').length};
    }).sort((a, b) => b.specificity - a.specificity);
  }

  // Backend journal entries remain original on the wire; only their display is localized.
  function message(source, depth = 0) {
    source = String(source ?? '');
    if (language === 'en' || depth > 5) return source;
    if (catalog.has(source)) return t(source);
    for (const template of templates) {
      const match = template.pattern.exec(source);
      if (!match) continue;
      const params = Object.fromEntries(template.keys.map((key, index) => [key,
        ['reason', 'target', 'action', 'kind', 'status'].includes(key) ? message(match[index + 1], depth + 1) : match[index + 1]]));
      return t(template.source, params);
    }
    if (source.includes('; ')) return source.split('; ').map(part => message(part, depth + 1)).join('; ');
    return source;
  }

  function translate(root = document) {
    const selector = '[data-i18n],[data-i18n-html],[data-i18n-placeholder],[data-i18n-title],[data-i18n-aria-label]';
    const nodes = [...root.querySelectorAll(selector)];
    if (root.matches?.(selector)) nodes.unshift(root);
    for (const node of nodes) {
      if (node.hasAttribute('data-i18n')) node.textContent = t(node.getAttribute('data-i18n'));
      // Only application-owned, static catalog markup uses this attribute.
      if (node.hasAttribute('data-i18n-html')) node.innerHTML = t(node.getAttribute('data-i18n-html'));
      for (const attribute of ['placeholder', 'title', 'aria-label']) {
        if (node.hasAttribute('data-i18n-' + attribute)) node.setAttribute(attribute, t(node.getAttribute('data-i18n-' + attribute)));
      }
    }
    if (document.documentElement.lang !== language) {
      // Firefox reparses localized decimal input text when its inherited lang
      // changes. Preserve the canonical values before switching that language.
      const numbers = [...document.querySelectorAll('input[type="number"]')].map(input => [input, input.value]);
      document.documentElement.lang = language;
      for (const [input, value] of numbers) input.value = value;
    }
    document.querySelectorAll('[data-language-select]').forEach(select => { select.value = language; });
  }

  function sendToMap() {
    document.getElementById('rail-map')?.contentWindow?.postMessage({type: 'language', language}, location.origin);
  }

  function setLanguage(next, persist = true) {
    if (!supported.includes(next)) return false;
    const changed = next !== language;
    language = next;
    if (persist) {
      try { localStorage.setItem(storageKey, language); } catch { /* Optional persistence. */ }
    }
    if (changed) {
      translate();
      window.dispatchEvent(new CustomEvent('railflow:languagechange', {detail: {language}}));
    }
    sendToMap();
    return true;
  }

  window.RailI18n = {t, add, message, translate, setLanguage, get language() { return language; }};
  document.addEventListener('change', event => {
    if (event.target.matches?.('[data-language-select]')) setLanguage(event.target.value);
  });
  window.addEventListener('storage', event => {
    if (event.key === storageKey) setLanguage(event.newValue || 'en', false);
  });
  window.addEventListener('message', event => {
    if (window.parent !== window && event.source === window.parent && event.origin === location.origin && event.data?.type === 'language') {
      setLanguage(event.data.language, false);
    }
  });
  function ready() {
    translate();
    document.getElementById('rail-map')?.addEventListener('load', sendToMap);
    sendToMap();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready, {once: true});
  else ready();
})();
