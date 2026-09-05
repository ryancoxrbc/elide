/* Page rendering shared by the two steps that show source documents:
   the URL of a rendered page, and the full-size lightbox over it.

   Both steps draw the same pages from the same routes, and the lightbox is a
   hundred lines of keyboard and rotation handling that neither wants a second
   copy of. Templates provide the markup by including _lightbox.html. */
window.Viewer = (function () {
  'use strict';

  function docPageUrl(source, page, dpi, bust) {
    return '/doc/' + encodeURIComponent(source) + '/page/' + page + '.png?dpi=' + dpi +
      (bust ? '&t=' + Date.now() : '');
  }

  function rotate(source, page) {
    return fetch('/rotate/' + encodeURIComponent(source) + '/' + page, {method: 'POST'})
      .then(function (r) { return r.json(); });
  }

  function setIgnored(source, page) {
    return fetch('/ignore/' + encodeURIComponent(source) + '/' + page, {method: 'POST'})
      .then(function (r) { return r.json(); });
  }

  // ---- the lightbox: one overlay for the whole page ---------------------
  var lb = null;

  function mount() {
    if (lb || !document.getElementById('lightbox')) return lb;
    lb = {
      el: document.getElementById('lightbox'),
      img: document.getElementById('lbImg'),
      title: document.getElementById('lbTitle'),
      pageLabel: document.getElementById('lbPageLabel'),
      prevBtn: document.getElementById('lbPrev'),
      nextBtn: document.getElementById('lbNext'),
      rotateBtn: document.getElementById('lbRotate'),
      source: null, pages: [], at: 0, onRotate: null
    };

    lb.el.querySelector('.lightbox-backdrop').addEventListener('click', close);
    document.getElementById('lbClose').addEventListener('click', close);
    lb.prevBtn.addEventListener('click', function () { step(-1); });
    lb.nextBtn.addEventListener('click', function () { step(1); });
    lb.rotateBtn.addEventListener('click', function () {
      var page = lb.pages[lb.at];
      lb.rotateBtn.disabled = true;
      rotate(lb.source, page)
        .then(function () {
          render(true);
          if (lb.onRotate) lb.onRotate(page);
        })
        .finally(function () { lb.rotateBtn.disabled = false; });
    });
    document.addEventListener('keydown', function (e) {
      if (lb.el.hidden) return;
      if (e.key === 'Escape') close();
      else if (e.key === 'ArrowLeft') step(-1);
      else if (e.key === 'ArrowRight') step(1);
    });
    return lb;
  }

  function render(bust) {
    var page = lb.pages[lb.at];
    lb.img.src = docPageUrl(lb.source, page, 220, bust);
    lb.title.textContent = lb.source;
    lb.pageLabel.textContent = 'Page ' + page +
      (lb.pages.length > 1 ? '  ·  ' + (lb.at + 1) + ' of ' + lb.pages.length : '');
    lb.prevBtn.disabled = lb.at <= 0;
    lb.nextBtn.disabled = lb.at >= lb.pages.length - 1;
  }

  function step(delta) {
    var next = lb.at + delta;
    if (next < 0 || next >= lb.pages.length) return;
    lb.at = next;
    render();
  }

  function close() {
    lb.el.hidden = true;
    document.body.classList.remove('lb-open');
  }

  /* `pages` is the list this lightbox may page through - a whole document on
     the split step, one receipt's own pages on the amounts step. */
  function open(source, pages, page, onRotate) {
    if (!mount()) return;
    lb.source = source;
    lb.pages = pages.slice();
    lb.at = Math.max(0, lb.pages.indexOf(parseInt(page, 10)));
    lb.onRotate = onRotate || null;
    lb.el.hidden = false;
    document.body.classList.add('lb-open');
    render(true);  // always fetch fresh, in case a rotation happened since last open
  }

  return {docPageUrl: docPageUrl, rotate: rotate, setIgnored: setIgnored, open: open};
})();
