/* M_part presentation adapter. Existing app.js retains state, commands and rendering.
   Move existing nodes and forward UI shortcuts; never calculate simulation values. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const id = value => document.getElementById(value);
  const i18n = window.RailI18n;
  i18n.add({
    'Map': ['Карта', 'Карта'],
    'Dispatcher': ['Диспетчер', 'Диспетчер'],
    'Analytics': ['Талдау', 'Аналитика'],
    'History': ['Тарих', 'История'],
    'Quality index': ['Сапа индексі', 'Индекс качества'],
    'Trains': ['Пойыздар', 'Поезда'],
    '2D map': ['2D карта', 'Карта 2D'],
    '3D map': ['3D карта', 'Карта 3D'],
    'Light theme': ['Ашық тақырып', 'Светлая тема'],
    'Dark theme': ['Қараңғы тақырып', 'Тёмная тема'],
    'DEMO': ['ДЕМО', 'ДЕМО'],
  });
  const paths = {
    train: '<rect x="5" y="3" width="14" height="15" rx="4"/><path d="M5 10h14M12 3v7M8 21l2-3m6 3-2-3"/><circle cx="9" cy="14" r="1"/><circle cx="15" cy="14" r="1"/>',
    map: '<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3V6Zm6-3v15m6-12v15"/>',
    dispatch: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="15" y="15" width="6" height="6" rx="1"/><path d="M6 9v9h9M9 6h9v9"/>',
    chart: '<path d="M4 3v17h17M7 15l4-5 4 2 5-7"/>',
    history: '<path d="M3 11a9 9 0 1 1 3 7M3 4v7h7M12 7v5l3 2"/>',
    moon: '<path d="M20 14A9 9 0 0 1 10 3a9 9 0 1 0 10 11Z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/>',
    alert: '<path d="m12 3 10 18H2L12 3Zm0 6v5m0 3v1"/>',
    arrow: '<path d="m9 5 7 7-7 7"/>',
  };
  const svg = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
  function text(source, tag = 'span') {
    const node = document.createElement(tag); node.dataset.i18n = source; node.textContent = i18n.t(source); return node;
  }
  function label(node, source) { node.dataset.i18n = source; node.textContent = i18n.t(source); }
  function shortcut(source, action, className = '') {
    const node = document.createElement('button'); node.type = 'button'; node.className = className;
    node.append(text(source)); node.addEventListener('click', action); return node;
  }

  // Header follows M_part: brand, route, sections, language, model clock, live state.
  const header = $('.topbar'), nav = $('.topbar nav'), right = $('.topbar-right');
  $('.brand-symbol').innerHTML = svg('train');
  $('.wordmark>span:last-child').textContent = 'RailFlow';
  const demo = text('DEMO'); demo.className = 'reference-demo'; $('.wordmark').append(demo);
  const route = document.createElement('div'); route.className = 'reference-route';
  route.append(text('KAZAKHSTAN NETWORK', 'strong'), id('map-region'));
  nav.before(route);
  const sections = [['overview', 'Map', 'map'], ['incidents', 'Dispatcher', 'dispatch'], ['timetable', 'Analytics', 'chart'], ['history', 'History', 'history']];
  for (const [tab, source, icon] of sections) {
    const button = $(`[data-tab="${tab}"]`);
    label(button.querySelector('.i18n-text'), source);
    button.dataset.i18nAriaLabel = source;
    button.querySelector('span:first-child').innerHTML = svg(icon);
    nav.append(button);
  }
  right.insertBefore($('.sim-time'), id('workspace-menu-button'));
  right.insertBefore(id('connection'), id('workspace-menu-button'));
  $('.header-status').remove();

  // Theme is a local visual preference; no map/simulation command is issued.
  const themeButton = document.createElement('button'); themeButton.type = 'button';
  themeButton.className = 'round-button reference-theme';
  right.insertBefore(themeButton, id('workspace-menu-button'));
  let theme;
  try { theme = localStorage.getItem('railflow-era-theme'); } catch { /* optional preference */ }
  const media = matchMedia('(prefers-color-scheme:dark)');
  function applyTheme() {
    const dark = theme === 'dark' || (theme !== 'light' && media.matches);
    document.documentElement.dataset.referenceTheme = dark ? 'dark' : 'light';
    const title = dark ? 'Light theme' : 'Dark theme';
    themeButton.innerHTML = svg(dark ? 'sun' : 'moon');
    themeButton.dataset.i18nTitle = title; themeButton.dataset.i18nAriaLabel = title;
    themeButton.title = i18n.t(title); themeButton.setAttribute('aria-label', i18n.t(title));
  }
  themeButton.addEventListener('click', () => {
    theme = document.documentElement.dataset.referenceTheme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('railflow-era-theme', theme); } catch { /* optional preference */ }
    applyTheme();
  });
  media.addEventListener('change', applyTheme); window.addEventListener('railflow:languagechange', applyTheme); applyTheme();

  // Quality, not train count, is the prominent card. All figures remain app-owned.
  const widget = $('.network-widget'), metrics = $('.metrics-grid');
  const quality = id('metric-quality').closest('.metric'); quality.classList.add('reference-quality');
  const qualityCaption = quality.querySelector('.i18n-text'); label(qualityCaption, 'Quality index');
  const qualityHead = quality.querySelector(':scope>span'); qualityHead.append(id('metric-quality-sub'));
  const scale = document.createElement('div'); scale.className = 'reference-quality-scale'; scale.setAttribute('aria-hidden', 'true');
  scale.append(document.createElement('i')); quality.append(scale);
  const qualityLink = id('quality-open'); qualityLink.classList.add('reference-quality-link');
  qualityLink.append(text('Quality details')); quality.append(qualityLink);
  $('.widget-heading').remove(); $('.widget-region').remove();
  metrics.before(quality);
  const incidents = id('metric-incidents').closest('.metric'); incidents.classList.add('reference-operations', 'glass');
  const operations = shortcut('Dispatcher', () => $('[data-tab="incidents"]').click(), 'reference-operations-link');
  operations.insertAdjacentHTML('afterbegin', svg('dispatch')); operations.insertAdjacentHTML('beforeend', svg('arrow'));
  incidents.prepend(operations); widget.after(incidents);
  const disclaimer = $('.demo-banner'); incidents.after(disclaimer);
  function qualityAppearance() {
    const gauge = id('quality-gauge');
    scale.style.setProperty('--quality-width', `${gauge.style.getPropertyValue('--score') || 0}%`);
    scale.style.setProperty('--quality-color', gauge.style.getPropertyValue('--gauge') || 'var(--accent)');
    quality.style.setProperty('--quality-color', gauge.style.getPropertyValue('--gauge') || 'var(--accent)');
  }
  new MutationObserver(qualityAppearance).observe(id('quality-gauge'), {attributes:true,attributeFilter:['style']}); qualityAppearance();

  // Filter pills forward to the original select and its original change handler.
  const fleet = $('.train-panel'); label(fleet.querySelector('.panel-heading h2 [data-i18n]') || fleet.querySelector('.panel-heading h2'), 'Trains');
  const filter = id('train-filter'), chips = document.createElement('div'); chips.className = 'reference-filters';
  chips.setAttribute('role', 'group'); chips.dataset.i18nAriaLabel = 'Filter trains';
  filter.classList.add('reference-native-filter');
  for (const option of filter.options) {
    const button = shortcut(option.dataset.i18n || option.textContent, () => {
      filter.value = option.value; filter.dispatchEvent(new Event('change', {bubbles:true})); syncFilters();
    });
    button.dataset.value = option.value; chips.append(button);
  }
  fleet.querySelector('.table-tools').after(chips);
  function syncFilters() { for (const button of chips.children) button.setAttribute('aria-pressed', String(button.dataset.value === filter.value)); }
  filter.addEventListener('change', syncFilters); syncFilters();

  // Restore readable transport labels and M_part's time presets / incident shortcut.
  for (const name of ['start','pause']) { id(name).classList.remove('transport-icon'); delete id(name).dataset.symbol; }
  const controls = $('.control-bar'), speed = id('speed');
  const presets = document.createElement('div'); presets.className = 'reference-speed-presets';
  for (const value of ['1','10','30','60']) {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = value + '×'; button.dataset.value = value;
    button.addEventListener('click', () => { speed.value = value; speed.dispatchEvent(new Event('change', {bubbles:true})); }); presets.append(button);
  }
  speed.parentElement.before(presets);
  function syncSpeed() { for (const button of presets.children) { button.disabled = speed.disabled; button.setAttribute('aria-pressed', String(button.dataset.value === speed.value)); } }
  new MutationObserver(syncSpeed).observe(id('sim-clock'), {childList:true,subtree:true,characterData:true});
  new MutationObserver(syncSpeed).observe(speed, {attributes:true,attributeFilter:['disabled']}); speed.addEventListener('change',syncSpeed); syncSpeed();
  const addIncident = shortcut('Create incident', () => { $('[data-tab="incidents"]').click(); id('incident-kind').focus(); }, 'reference-incident');
  addIncident.insertAdjacentHTML('afterbegin', svg('alert')); controls.append(addIncident);
  $('.map-tools').classList.add('reference-mapdock');
  for (const [i,button] of [...$('.scene3d-view-switch').children].entries()) label(button, i ? '3D map' : '2D map');
  id('fit-trains').append(text('⌖ Focus trains')); id('map-controls').append(text('Layers'));

  // Existing advisory panel opens beside the compact fleet on desktop.
  id('advisory-open').addEventListener('click', () => { if (matchMedia('(min-width:1100px)').matches) fleet.hidden = false; });
  id('train-rows').addEventListener('click', event => { if (event.target.closest('[data-train]')) id('advisory-open').click(); });
  i18n.translate();
})();
