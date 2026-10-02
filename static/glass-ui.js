/* Presentation shell only. Existing app.js owns data, dispatch and all commands. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const byId = id => document.getElementById(id);
  const i18n = window.RailI18n;
  i18n.add({
    'Network overview': ['Желіге шолу', 'Обзор сети'],
    'Workspace menu': ['Жұмыс кеңістігі мәзірі', 'Меню рабочего пространства'],
    'Simulation': ['Симуляция', 'Симуляция'],
    'Choose scenario': ['Сценарийді таңдау', 'Выбрать сценарий'],
    'View driving advisory': ['Жүргізу кеңесін көру', 'Рекомендации по движению'],
    'Back to trains': ['Пойыздарға оралу', 'К списку поездов'],
    'Close panel': ['Панельді жабу', 'Закрыть панель'],
    'Expand train list': ['Пойыздар тізімін жаю', 'Развернуть список поездов'],
    'Collapse train list': ['Пойыздар тізімін жию', 'Свернуть список поездов'],
    'Network': ['Желі', 'Сеть'],
    'Timetable': ['Кесте', 'Расписание'],
    'Quality details': ['Сапа мәліметтері', 'Показатели качества'],
    'Scenario resets the current run.': ['Сценарий ағымдағы іске қосуды қалпына келтіреді.', 'Сценарий сбрасывает текущий запуск.'],
  });
  const icons = {
    logo: '<path d="M8 3h8M7 8h10M6 14h12M8 3 4 21M16 3l4 18M5 19h14"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    focus: '<circle cx="12" cy="12" r="5"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
    layers: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
    arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    back: '<path d="M19 12H5m5-5-5 5 5 5"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    chart: '<path d="M4 4v16h16M8 15l4-5 4 2 4-7"/>',
    sliders: '<path d="M4 7h5m4 0h7M4 17h9m4 0h3"/><circle cx="11" cy="7" r="2"/><circle cx="15" cy="17" r="2"/>',
  };
  const svg = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;
  function text(source, className) {
    const node = document.createElement('span');
    node.dataset.i18n = source;
    node.textContent = i18n.t(source);
    if (className) node.className = className;
    return node;
  }
  function button(id, source, icon, className = '') {
    const node = document.createElement('button');
    node.type = 'button'; node.id = id; node.className = className;
    node.dataset.i18nAriaLabel = source; node.dataset.i18nTitle = source;
    node.setAttribute('aria-label', i18n.t(source)); node.title = i18n.t(source);
    if (icon) node.innerHTML = svg(icon);
    return node;
  }
  function iconify(node, source, icon) {
    node.removeAttribute('data-i18n');
    node.dataset.i18nAriaLabel = source; node.dataset.i18nTitle = source;
    node.setAttribute('aria-label', i18n.t(source)); node.title = i18n.t(source);
    node.innerHTML = svg(icon);
  }
  $('.login-card .brandmark').innerHTML = svg('logo');
  const main = $('main'), topbar = $('.topbar'), mapPanel = $('.map-panel');
  for (const [id, label] of [['log-search','Search event text or asset IDs…'], ['log-kind','All events'], ['replay-length','Replay']]) byId(id).dataset.i18nAriaLabel = label;
  // Move the untouched iframe before its first navigation. It never leaves this shell.
  main.prepend(mapPanel);
  const brand = $('.wordmark');
  brand.innerHTML = `<span class="brand-symbol">${svg('logo').replace('stroke="currentColor"', 'stroke="url(#brand-gradient)"').replace('</svg>', '<defs><linearGradient id="brand-gradient" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#0a84ff"/><stop offset="1" stop-color="#30d5c8"/></linearGradient></defs></svg>')}</span><span>railflow<span class="brand-dot">.</span></span>`;
  const nav = $('.sidebar nav');
  const headerRight = $('.topbar-right');
  const headerStatus = document.createElement('div'); headerStatus.className = 'header-status';
  headerStatus.append(byId('connection'), $('.sim-time'));
  headerStatus.querySelector('.sim-time .live-dot')?.remove();
  topbar.prepend(brand, headerStatus, nav);
  $('.breadcrumb').classList.add('sr-only');
  const navLabels = {overview: 'Network', timetable: 'Timetable', incidents: 'Incidents'};
  for (const [name, source] of Object.entries(navLabels)) {
    const node = $(`[data-tab="${name}"]`);
    node.querySelector('.i18n-text').dataset.i18n = source;
    node.querySelector('.i18n-text').textContent = i18n.t(source);
    node.querySelector('span:first-child').setAttribute('aria-hidden', 'true');
    node.dataset.i18nAriaLabel = source;
  }
  const menuButton = button('workspace-menu-button', 'Workspace menu', 'more', 'round-button');
  menuButton.setAttribute('aria-expanded', 'false'); menuButton.setAttribute('aria-controls', 'workspace-menu');
  const menu = document.createElement('div'); menu.id = 'workspace-menu'; menu.className = 'glass workspace-menu'; menu.hidden = true;
  menu.append($('[data-tab="history"]'), $('[data-tab="settings"]'));
  const menuLinks = document.createElement('div'); menuLinks.className = 'menu-links';
  menuLinks.append(byId('help-open'), byId('assumptions-open'), headerRight.querySelector('a'), $('footer a'));
  menu.append(menuLinks, $('.sidebar-bottom'));
  const notes = document.createElement('div'); notes.className = 'menu-notes';
  notes.append($('.network-note'), $('footer'));
  menu.append(notes);
  headerRight.append(menuButton); topbar.append(menu);
  $('.sidebar').remove();
  function closeMenu(focus = false) {
    menu.hidden = true; menuButton.setAttribute('aria-expanded', 'false');
    if (focus) menuButton.focus();
  }
  menuButton.addEventListener('click', () => {
    menu.hidden = !menu.hidden;
    menuButton.setAttribute('aria-expanded', String(!menu.hidden));
    if (!menu.hidden) menu.querySelector('button').focus();
  });
  document.addEventListener('pointerdown', e => {
    if (!menu.hidden && !menu.contains(e.target) && !menuButton.contains(e.target)) closeMenu();
  });
  menu.addEventListener('click', e => { if (e.target.closest('button,a')) { if (e.target.closest('[data-tab]')) queueMicrotask(() => sheetClose.focus()); closeMenu(); } });
  byId('logout').dataset.i18nAriaLabel = 'Sign out';

  const overview = byId('tab-overview');
  const widget = document.createElement('section'); widget.className = 'network-widget glass';
  const widgetHeading = document.createElement('div'); widgetHeading.className = 'widget-heading';
  const heading = document.createElement('div');
  heading.append(text('KAZAKHSTAN NETWORK', 'eyebrow'));
  const title = document.createElement('h2'); title.append(text('Network overview')); heading.append(title);
  const qualityOpen = button('quality-open', 'Quality details', 'chart', 'round-button');
  qualityOpen.setAttribute('aria-expanded', 'false'); qualityOpen.setAttribute('aria-controls', 'quality-details');
  widgetHeading.append(heading, qualityOpen);
  widget.append(widgetHeading, $('.metrics-grid'));
  const region = document.createElement('div'); region.className = 'widget-region'; region.append(byId('map-region'));
  widget.append(region); overview.prepend(widget);
  const quality = $('.quality-panel'); quality.id = 'quality-details'; quality.hidden = true;
  const qualityClose = button('quality-close', 'Close panel', 'close', 'round-button');
  quality.querySelector('.small-icon').replaceWith(qualityClose);
  function showQuality(show) {
    quality.hidden = !show; widget.classList.toggle('quality-expanded', show);
    qualityOpen.setAttribute('aria-expanded', String(show));
    if (show) qualityClose.focus(); else qualityOpen.focus();
  }
  qualityOpen.addEventListener('click', () => showQuality(quality.hidden));
  qualityClose.addEventListener('click', () => showQuality(false));

  const trainPanel = $('.train-panel');
  const trainHeading = trainPanel.querySelector('.panel-heading');
  const trainTools = trainPanel.querySelector('.table-tools');
  trainHeading.after(trainTools);
  const addTrain = byId('add-train-open'); iconify(addTrain, '+ Add train', 'plus');
  trainHeading.append(addTrain);
  const mobileToggle = button('mobile-sheet-toggle', 'Collapse train list', 'chevron', 'round-button sheet-toggle');
  mobileToggle.setAttribute('aria-expanded', 'true'); mobileToggle.setAttribute('aria-controls', 'train-list-body');
  trainHeading.append(mobileToggle);
  trainPanel.querySelector('.table-scroll').id = 'train-list-body';
  const trainFooter = document.createElement('div'); trainFooter.className = 'train-footer';
  const advisoryOpen = button('advisory-open', 'View driving advisory', null);
  advisoryOpen.append(text('View driving advisory')); advisoryOpen.insertAdjacentHTML('beforeend', svg('arrow'));
  trainFooter.append(advisoryOpen); trainPanel.append(trainFooter);
  const details = $('.bottom-grid'); details.id = 'train-details'; details.hidden = true;
  const advisoryClose = button('advisory-close', 'Back to trains', 'back', 'advisory-back');
  advisoryClose.append(text('Back to trains')); details.prepend(advisoryClose);
  function showAdvisory(show) {
    details.hidden = !show; trainPanel.hidden = show;
    advisoryOpen.setAttribute('aria-expanded', String(show));
    if (show) { advisoryClose.focus(); window.dispatchEvent(new Event('resize')); }
    else advisoryOpen.focus();
  }
  advisoryOpen.setAttribute('aria-controls', 'train-details'); advisoryOpen.setAttribute('aria-expanded', 'false');
  advisoryOpen.addEventListener('click', () => showAdvisory(true));
  advisoryClose.addEventListener('click', () => showAdvisory(false));
  mobileToggle.addEventListener('click', () => {
    const collapsed = trainPanel.classList.toggle('sheet-collapsed');
    mobileToggle.setAttribute('aria-expanded', String(!collapsed));
    mobileToggle.dataset.i18nAriaLabel = collapsed ? 'Expand train list' : 'Collapse train list';
    mobileToggle.dataset.i18nTitle = mobileToggle.dataset.i18nAriaLabel;
    i18n.translate(mobileToggle);
  });
  // Space selects rows just like Enter. Expose selection without changing row rendering.
  byId('train-rows').addEventListener('keydown', e => {
    if (e.code === 'Space' && e.target.matches('.train-row')) { e.preventDefault(); e.target.click(); }
  });
  let focusedTrain = null;
  document.addEventListener('focusin', e => { focusedTrain = e.target.closest?.('#train-rows [data-train]')?.dataset.train || null; });
  document.addEventListener('pointerdown', e => { if (!e.target.closest('#train-rows')) focusedTrain = null; });
  function accessibleRows() {
    byId('train-rows').querySelectorAll('.train-row').forEach(row => {
      row.setAttribute('aria-selected', String(row.classList.contains('selected')));
    });
    advisoryOpen.disabled = !byId('train-rows').querySelector('.train-row.selected');
    if (focusedTrain && document.activeElement === document.body) {
      const replacement = byId('train-rows').querySelector(`[data-train="${CSS.escape(focusedTrain)}"]`);
      if (replacement) replacement.focus({preventScroll: true});
      else focusedTrain = null;
    }
  }
  new MutationObserver(accessibleRows).observe(byId('train-rows'), {childList: true});

  const scenarioDialog = document.createElement('dialog'); scenarioDialog.id = 'scenario-dialog';
  scenarioDialog.setAttribute('aria-labelledby', 'scenario-title');
  const scenarioHeading = document.createElement('div'); scenarioHeading.className = 'dialog-heading';
  const scenarioTitle = document.createElement('h2'); scenarioTitle.id = 'scenario-title'; scenarioTitle.append(text('Choose scenario'));
  const scenarioClose = button('scenario-close', 'Close dialog', 'close', 'round-button');
  scenarioHeading.append(scenarioTitle, scenarioClose);
  const scenarioBody = document.createElement('div'); scenarioBody.className = 'form-body';
  const scenarioNote = document.createElement('p'); scenarioNote.className = 'fineprint'; scenarioNote.append(text('Scenario resets the current run.'));
  scenarioBody.append($('.demo-actions'), scenarioNote);
  scenarioDialog.append(scenarioHeading, scenarioBody); document.body.append(scenarioDialog);
  scenarioClose.addEventListener('click', () => scenarioDialog.close());
  byId('load-demo').addEventListener('click', () => scenarioDialog.close());
  byId('load-demo').classList.add('primary');
  const scenarioOpen = button('scenario-open', 'Choose scenario', 'sliders');
  scenarioOpen.append(text('Simulation'));
  $('.control-bar').prepend(scenarioOpen); $('.mode-label').remove();
  scenarioOpen.addEventListener('click', () => scenarioDialog.showModal());
  // Keep app-owned text for screen readers and its dynamic running/resume labels.
  for (const [id, symbol] of [['start','▶'], ['pause','Ⅱ'], ['reset','↺']]) {
    byId(id).classList.add('transport-icon'); byId(id).dataset.symbol = symbol;
  }
  byId('reset').dataset.i18nAriaLabel = 'Reset simulation';
  byId('start').setAttribute('aria-label', byId('start').textContent);
  byId('pause').dataset.i18nAriaLabel = 'Ⅱ Pause';
  new MutationObserver(() => byId('start').setAttribute('aria-label', byId('start').textContent)).observe(byId('start'), {childList: true, characterData: true, subtree: true});
  $('.speed-label').querySelector('span').classList.add('sr-only');
  byId('speed').dataset.i18nAriaLabel = 'Speed';
  iconify(byId('fit-trains'), '⌖ Focus trains', 'focus');
  iconify(byId('map-controls'), 'Layers', 'layers');
  $('.map-tools').querySelector('.tag').remove();
  const mapHeading = mapPanel.querySelector('.panel-heading');
  mapHeading.querySelector('h2').classList.add('sr-only');
  const legend = $('.map-legend');
  const mapFooter = $('.map-footer');
  mapFooter.prepend(legend);
  mapFooter.querySelector(':scope > span:first-of-type').classList.add('sr-only');

  const sheet = document.createElement('section'); sheet.className = 'workspace-sheet glass'; sheet.hidden = true;
  sheet.setAttribute('aria-labelledby', 'page-title');
  const sheetClose = button('sheet-close', 'Close panel', 'close', 'round-button');
  const pageHeading = $('.page-heading'); pageHeading.append(sheetClose); sheet.append(pageHeading);
  document.querySelectorAll('.tab:not(#tab-overview)').forEach(node => sheet.append(node));
  main.append(sheet);
  sheetClose.addEventListener('click', () => $('[data-tab="overview"]').click());
  function syncTab() {
    const isOverview = byId('tab-overview').classList.contains('active');
    const focusedInMenu = menu.contains(document.activeElement);
    sheet.hidden = isOverview;
    if (!isOverview && focusedInMenu) sheetClose.focus();
    document.body.classList.toggle('sheet-open', !isOverview);
    document.querySelectorAll('.nav-button').forEach(node => {
      if (node.classList.contains('active')) node.setAttribute('aria-current', 'page');
      else node.removeAttribute('aria-current');
    });
    closeMenu();
  }
  new MutationObserver(syncTab).observe(overview, {attributes: true, attributeFilter: ['class']});
  syncTab();
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || document.querySelector('dialog[open]')) return;
    if (!menu.hidden) closeMenu(true);
    else if (!sheet.hidden) { $('[data-tab="overview"]').click(); $('[data-tab="overview"]').focus(); }
    else if (!details.hidden) showAdvisory(false);
    else if (!quality.hidden) showQuality(false);
  });
  for (const dialog of document.querySelectorAll('dialog')) {
    if (!dialog.hasAttribute('aria-labelledby')) {
      const title = dialog.querySelector('h2');
      if (title) { title.id ||= dialog.id + '-title'; dialog.setAttribute('aria-labelledby', title.id); }
    }
  }
  // Styling for the existing map's chrome only; no map API, geometry or state changes.
  const frame = byId('rail-map');
  function styleMap() {
    try {
      const doc = frame.contentDocument;
      const isMap = frame.getAttribute('src') === '/map-live';
      const isScene = frame.getAttribute('src') === '/static/scene3d/index.html';
      if (!doc || (!isMap && !isScene) || doc.getElementById('glass-map-style')) return;
      const link = doc.createElement('link'); link.id = 'glass-map-style'; link.rel = 'stylesheet'; link.href = isMap ? '/static/glass-map.css' : '/static/glass-scene.css'; doc.head.append(link);
      const layers = doc.getElementById(isMap ? 'panel' : 'layers');
      const syncLayers = () => {
        const open = isMap ? layers.style.display !== 'none' : !layers.hidden;
        document.body.classList.toggle('map-layers-open', open);
        byId('map-controls').setAttribute('aria-expanded', String(open));
      };
      new MutationObserver(syncLayers).observe(layers, {attributes: true, attributeFilter: ['style','hidden']});
      syncLayers();
    } catch { /* A future cross-origin map can retain its own chrome. */ }
  }
  frame.addEventListener('load', () => { document.body.classList.remove('map-layers-open'); styleMap(); });
  window.addEventListener('message', event => {
    if (event.origin === location.origin && event.source === frame.contentWindow && event.data?.type === 'map-ready') styleMap();
  });
  i18n.translate();
})();
