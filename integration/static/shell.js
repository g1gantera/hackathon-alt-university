function show(view) {
  if (!['dispatcher', 'network'].includes(view)) return;
  document.querySelectorAll('iframe').forEach(frame => {
    frame.hidden = frame.id !== view;
    if (!frame.hidden && !frame.src) frame.src = frame.dataset.src;
  });
  document.querySelectorAll('[data-view]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.view === view));
  });
  history.replaceState(null, '', `#${view}`);
}
document.querySelectorAll('[data-view]').forEach(button => {
  button.addEventListener('click', () => show(button.dataset.view));
});
window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.source !== document.getElementById('network').contentWindow) return;
  if (event.data?.type === 'railflow.navigate') show(event.data.view);
});
window.addEventListener('hashchange', () => show(location.hash.slice(1)));
show(location.hash.slice(1) || 'dispatcher');
