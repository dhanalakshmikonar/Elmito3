/* EL MITO editorial theme: progressive enhancement only. Every page still works without this file. */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const root = document.documentElement;
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- Header: hide on scroll down, show on scroll up ---------- */
  const header = $('[data-header]');
  if (header) {
    let last = window.scrollY, ticking = false;
    addEventListener('scroll', () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        const y = window.scrollY;
        header.classList.toggle('is-hidden', y > last + 6 && y > 240);
        if (y < last - 6 || y < 120) header.classList.remove('is-hidden');
        last = y; ticking = false;
      });
    }, { passive: true });
  }

  /* ---------- Overlays: menu, search, saved drawer ---------- */
  const scrim = $('[data-scrim]');
  let openPanel = null, returnFocus = null;
  const panels = { menu: $('[data-menu]'), search: $('[data-search]'), saved: $('[data-saved]') };
  const triggers = { menu: '[data-menu-open]', search: '[data-search-open]', saved: '[data-saved-open]' };

  const closePanel = () => {
    if (!openPanel) return;
    const el = openPanel; openPanel = null;
    el.classList.remove('is-open');
    scrim && scrim.classList.remove('is-open');
    document.body.classList.remove('is-locked');
    $$('[aria-expanded="true"]').forEach((t) => t.setAttribute('aria-expanded', 'false'));
    setTimeout(() => { el.hidden = true; if (scrim) scrim.hidden = true; }, reduceMotion ? 0 : 520);
    if (returnFocus && returnFocus.focus) returnFocus.focus();
  };
  const open = (name, trigger) => {
    const el = panels[name];
    if (!el) return false;
    if (openPanel) closePanel();
    returnFocus = trigger || document.activeElement;
    el.hidden = false;
    if (name === 'saved' && scrim) { scrim.hidden = false; requestAnimationFrame(() => scrim.classList.add('is-open')); }
    requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('is-open')));
    document.body.classList.add('is-locked');
    $$(triggers[name]).forEach((t) => t.setAttribute('aria-expanded', 'true'));
    openPanel = el;
    const focusTarget = name === 'search' ? $('input[type=search]', el) : $('[data-close]', el);
    setTimeout(() => focusTarget && focusTarget.focus(), 60);
    return true;
  };
  Object.keys(triggers).forEach((name) => $$(triggers[name]).forEach((t) => t.addEventListener('click', (e) => {
    if (open(name, t)) e.preventDefault();
  })));
  $$('[data-close]').forEach((b) => b.addEventListener('click', closePanel));
  scrim && scrim.addEventListener('click', () => { closePanel(); closeFilters(); });
  addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closePanel(); closeLightbox(); closeFilters(); }
    if (e.key === 'Tab' && openPanel) {
      const f = $$('a[href], button:not([disabled]), input, select', openPanel).filter((x) => x.offsetParent !== null);
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
  const menuLinks = $('[data-menu]');
  menuLinks && menuLinks.addEventListener('click', (e) => { if (e.target.closest('a')) closePanel(); });

  /* ---------- Toasts ---------- */
  $$('.toast').forEach((t, i) => {
    const dismiss = () => { t.classList.add('is-leaving'); setTimeout(() => t.remove(), 360); };
    $('[data-toast-close]', t)?.addEventListener('click', dismiss);
    setTimeout(dismiss, 6500 + i * 800);
  });

  /* ---------- Saved items (stored in this browser only; no account sync) ---------- */
  const KEY = 'elmito:saved:v1';
  const safeUrl = (u) => typeof u === 'string' && (u.startsWith('/') || u.startsWith('https://')) && !u.startsWith('//');
  const load = () => { try { const v = JSON.parse(localStorage.getItem(KEY) || '[]'); return Array.isArray(v) ? v.filter((x) => x && x.id && safeUrl(x.url)) : []; } catch (e) { return []; } };
  const store = (items) => { try { localStorage.setItem(KEY, JSON.stringify(items.slice(0, 60))); } catch (e) { /* storage unavailable: feature degrades silently */ } };
  const list = $('[data-saved-list]'), emptyState = $('[data-saved-empty]');
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  const renderSaved = () => {
    const items = load();
    $$('[data-save]').forEach((b) => {
      const on = items.some((x) => x.id === b.dataset.id);
      b.setAttribute('aria-pressed', String(on));
      b.setAttribute('aria-label', (on ? 'Remove from saved: ' : 'Save for later: ') + (b.dataset.name || ''));
    });
    $$('[data-saved-count]').forEach((c) => { c.textContent = String(items.length); c.hidden = !items.length; });
    $$('[data-saved-dot]').forEach((d) => { d.hidden = !items.length; });
    if (!list) return;
    list.replaceChildren();
    if (emptyState) emptyState.hidden = items.length > 0;
    items.forEach((item) => {
      const li = el('li', 'saved-item');
      if (safeUrl(item.img)) { const img = el('img'); img.src = item.img; img.alt = ''; img.width = 76; img.height = 95; img.loading = 'lazy'; li.append(img); } else li.append(el('span'));
      const info = el('div'); const a = el('a', '', item.name || 'Product'); a.href = item.url;
      info.append(a, el('p', '', item.price || ''));
      const rm = el('button', '', 'Remove'); rm.type = 'button';
      rm.addEventListener('click', () => { store(load().filter((x) => x.id !== item.id)); renderSaved(); });
      li.append(info, rm); list.append(li);
    });
  };
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-save]');
    if (!b) return;
    e.preventDefault(); e.stopPropagation();
    const items = load(), i = items.findIndex((x) => x.id === b.dataset.id);
    if (i >= 0) items.splice(i, 1);
    else items.unshift({ id: b.dataset.id, name: b.dataset.name, url: b.dataset.url, img: b.dataset.img, price: b.dataset.price });
    store(items); renderSaved();
  });
  addEventListener('storage', (e) => { if (e.key === KEY) renderSaved(); });
  renderSaved();

  /* ---------- Scroll reveal ---------- */
  const reveals = $$('.reveal');
  if ('IntersectionObserver' in window && !reduceMotion) {
    const io = new IntersectionObserver((entries) => entries.forEach((en) => { if (en.isIntersecting) { en.target.classList.add('is-in'); io.unobserve(en.target); } }), { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    reveals.forEach((r) => io.observe(r));
  } else reveals.forEach((r) => r.classList.add('is-in'));

  /* ---------- Images: fade in when loaded, graceful fallback if one is missing ---------- */
  const trackImage = (img) => {
    const ok = () => { img.classList.add('is-loaded'); img.parentElement && img.parentElement.classList.add('has-img'); };
    const fail = () => { img.hidden = true; img.parentElement && img.parentElement.classList.add('img-failed'); };
    if (img.complete) { img.naturalWidth ? ok() : fail(); } else { img.addEventListener('load', ok, { once: true }); img.addEventListener('error', fail, { once: true }); }
  };
  $$('img[data-img]').forEach(trackImage);

  /* ---------- Home: collection index preview ---------- */
  const index = $('[data-index]');
  if (index) {
    const preview = $('[data-index-img]', index);
    const rows = $$('[data-preview]', index);
    const show = (row) => {
      if (!preview || !row.dataset.preview) return;
      rows.forEach((r) => r.classList.toggle('is-active', r === row));
      if (preview.getAttribute('src') !== row.dataset.preview) {
        preview.classList.add('is-swapping');
        setTimeout(() => { preview.src = row.dataset.preview; preview.classList.remove('is-swapping'); }, reduceMotion ? 0 : 220);
      }
    };
    rows.forEach((r) => { r.addEventListener('mouseenter', () => show(r)); r.addEventListener('focus', () => show(r)); });
    rows[0] && rows[0].classList.add('is-active');
  }

  /* ---------- Home: oversized rail ---------- */
  $$('[data-rail]').forEach((rail) => {
    const track = $('[data-rail-track]', rail), bar = $('[data-rail-bar]', rail.parentElement);
    const prev = $('[data-rail-prev]', rail.parentElement), next = $('[data-rail-next]', rail.parentElement);
    if (!track) return;
    const update = () => {
      const max = track.scrollWidth - track.clientWidth;
      const ratio = max > 0 ? track.scrollLeft / max : 0;
      if (bar) { const w = Math.max(0.12, track.clientWidth / track.scrollWidth); bar.style.width = (w * 100) + '%'; bar.style.transform = `translateX(${ratio * (1 / w - 1) * 100}%)`; }
      if (prev) prev.disabled = track.scrollLeft < 4;
      if (next) next.disabled = track.scrollLeft > max - 4;
    };
    const step = () => (track.querySelector('li')?.getBoundingClientRect().width || 320) + 24;
    prev && prev.addEventListener('click', () => track.scrollBy({ left: -step(), behavior: 'smooth' }));
    next && next.addEventListener('click', () => track.scrollBy({ left: step(), behavior: 'smooth' }));
    track.addEventListener('scroll', () => requestAnimationFrame(update), { passive: true });
    addEventListener('resize', update);
    // mouse drag to scroll (touch already scrolls natively)
    let down = false, startX = 0, startLeft = 0, moved = false;
    track.addEventListener('pointerdown', (e) => { if (e.pointerType !== 'mouse') return; down = true; moved = false; startX = e.clientX; startLeft = track.scrollLeft; });
    addEventListener('pointermove', (e) => { if (!down) return; const dx = e.clientX - startX; if (Math.abs(dx) > 4) { moved = true; track.classList.add('is-dragging'); } track.scrollLeft = startLeft - dx; });
    addEventListener('pointerup', () => { down = false; setTimeout(() => track.classList.remove('is-dragging'), 0); });
    track.addEventListener('click', (e) => { if (moved) { e.preventDefault(); moved = false; } }, true);
    update();
  });

  /* ---------- Shop: filter sheet + sort ---------- */
  const filters = $('[data-filters]');
  const closeFilters = () => {
    if (!filters || !filters.classList.contains('is-open')) return;
    filters.classList.remove('is-open'); document.body.classList.remove('is-locked');
    if (scrim) { scrim.classList.remove('is-open'); setTimeout(() => { if (!openPanel) scrim.hidden = true; }, reduceMotion ? 0 : 420); }
    $$('[data-filters-open]').forEach((b) => b.setAttribute('aria-expanded', 'false'));
  };
  if (filters) {
    $$('[data-filters-open]').forEach((b) => b.addEventListener('click', () => {
      filters.classList.add('is-open'); document.body.classList.add('is-locked'); b.setAttribute('aria-expanded', 'true');
      if (scrim) { scrim.hidden = false; requestAnimationFrame(() => scrim.classList.add('is-open')); }
      $('[data-filters-close]', filters)?.focus();
    }));
    $$('[data-filters-close]').forEach((b) => b.addEventListener('click', closeFilters));
  }
  const sort = $('[data-sort]');
  sort && sort.addEventListener('change', () => sort.form.requestSubmit());

  /* ---------- Product page ---------- */
  const lightbox = $('#lightbox');
  let lbIndex = 0, lbFigs = [];
  const lbShow = (i) => {
    if (!lbFigs.length) return;
    lbIndex = (i + lbFigs.length) % lbFigs.length;
    const src = $('img', lbFigs[lbIndex]);
    const target = $('img', lightbox);
    target.src = src.currentSrc || src.src; target.alt = src.alt;
  };
  const closeLightbox = () => { if (lightbox && lightbox.open) lightbox.close(); };
  const gallery = $('[data-gallery]');
  let showColor = () => {};
  if (gallery) {
    const track = $('[data-track]', gallery), counter = $('[data-counter]', gallery);
    const figs = $$('.pdp__fig', gallery);
    const visible = () => figs.filter((f) => !f.hidden);
    const pad = (n) => String(n).padStart(2, '0');
    const mark = () => {
      if (!counter || !track) return;
      const v = visible();
      const horizontal = track.scrollWidth > track.clientWidth + 4;
      const i = horizontal ? Math.round(track.scrollLeft / track.clientWidth) : 0;
      counter.textContent = `${pad(Math.min(i + 1, v.length))} / ${pad(v.length)}`;
    };
    track && track.addEventListener('scroll', () => requestAnimationFrame(mark), { passive: true });
    showColor = (name) => {
      const key = (name || '').toLowerCase();
      const any = figs.some((f) => f.dataset.color === key);
      figs.forEach((f) => { f.hidden = any && f.dataset.color && f.dataset.color !== key; });
      if (track) track.scrollLeft = 0;
      mark();
    };
    figs.forEach((f) => $('[data-zoom]', f)?.addEventListener('click', () => {
      if (!lightbox || !lightbox.showModal) return;
      lbFigs = visible(); lbShow(lbFigs.indexOf(f)); lightbox.showModal();
    }));
    $('[data-lb-prev]')?.addEventListener('click', (e) => { e.stopPropagation(); lbShow(lbIndex - 1); });
    $('[data-lb-next]')?.addEventListener('click', (e) => { e.stopPropagation(); lbShow(lbIndex + 1); });
    lightbox && lightbox.addEventListener('click', closeLightbox);
    addEventListener('keydown', (e) => { if (lightbox && lightbox.open) { if (e.key === 'ArrowLeft') lbShow(lbIndex - 1); if (e.key === 'ArrowRight') lbShow(lbIndex + 1); } });
    mark();
  }
  $$('[data-open-dialog]').forEach((b) => b.addEventListener('click', () => $('#' + b.dataset.openDialog)?.showModal()));
  $$('[data-close-dialog]').forEach((b) => b.addEventListener('click', () => b.closest('dialog').close()));

  const form = $('[data-pdp]');
  if (form) {
    const matrix = JSON.parse($('#variant-data').textContent);
    const variantInput = $('[name=variant]', form), stockEl = $('[data-stock]', form), errorEl = $('[data-error]', form), qty = $('[name=quantity]', form);
    const colorRadios = $$('input[name=color]', form), sizeRadios = $$('input[name=size]', form);
    const checked = (rs) => rs.find((r) => r.checked);
    const colorLabel = $('[data-color-name]', form);
    const MAX_QTY = 10;
    const clampQty = (max) => { if (!qty) return; const v = parseInt(qty.value, 10) || 1; qty.max = String(max); qty.value = String(Math.min(Math.max(1, v), max)); };

    const update = () => {
      const color = checked(colorRadios)?.value;
      const entry = matrix.find((m) => m.color === color);
      if (colorLabel) colorLabel.textContent = color || '';
      sizeRadios.forEach((r) => {
        const s = entry?.sizes.find((x) => x.size === r.value);
        r.disabled = !s || !s.in_stock;
        if (r.disabled && r.checked) r.checked = false;
      });
      const size = checked(sizeRadios)?.value;
      const variant = entry?.sizes.find((x) => x.size === size);
      variantInput.value = variant ? variant.variant_id : '';
      stockEl.className = 'stock';
      if (!entry || entry.sizes.every((s) => !s.in_stock)) { stockEl.textContent = 'This colour is sold out.'; stockEl.classList.add('stock--out'); }
      else if (!variant) stockEl.textContent = 'Select a size.';
      else if (variant.stock <= 5) { stockEl.textContent = `Only ${variant.stock} left in this size.`; stockEl.classList.add('stock--low'); }
      else stockEl.textContent = 'In stock.';
      clampQty(variant ? Math.min(variant.stock, MAX_QTY) : MAX_QTY);
      errorEl.textContent = '';
    };
    colorRadios.forEach((r) => r.addEventListener('change', () => { showColor(r.value); update(); }));
    sizeRadios.forEach((r) => r.addEventListener('change', update));
    $$('[data-qty-step]', form).forEach((b) => b.addEventListener('click', () => { qty.value = String((parseInt(qty.value, 10) || 1) + parseInt(b.dataset.qtyStep, 10)); clampQty(parseInt(qty.max, 10) || MAX_QTY); }));
    qty && qty.addEventListener('change', () => clampQty(parseInt(qty.max, 10) || MAX_QTY));
    form.addEventListener('submit', (e) => {
      if (variantInput.value) return;
      e.preventDefault(); e.stopImmediatePropagation();
      errorEl.textContent = 'Please select a size.';
      form.scrollIntoView({ behavior: 'smooth', block: 'center' });
      $$('.btn.is-loading').forEach((b) => b.classList.remove('is-loading'));
    }, true);
    const initial = checked(colorRadios);
    if (initial) showColor(initial.value);
    update();
    const anchor = $('[data-add]', form), bar = $('#sticky-buy');
    if (anchor && bar && 'IntersectionObserver' in window) new IntersectionObserver(([en]) => bar.classList.toggle('is-visible', !en.isIntersecting && en.boundingClientRect.top < 0)).observe(anchor);
  }

  /* ---------- Loading state on form submit (stops double submits) ---------- */
  document.addEventListener('submit', (e) => {
    const f = e.target;
    setTimeout(() => {
      if (e.defaultPrevented || f.hasAttribute('data-no-loading') || f.method.toLowerCase() !== 'post') return;
      const btn = e.submitter || $('button[type=submit], .btn[type=submit]', f);
      if (btn && btn.classList.contains('btn')) { btn.classList.add('is-loading'); btn.setAttribute('aria-busy', 'true'); }
      $$('button[type=submit]', f).forEach((b) => { if (b !== btn) b.disabled = true; });
    }, 0);
  });
  addEventListener('pageshow', (e) => { if (e.persisted) $$('.is-loading').forEach((b) => { b.classList.remove('is-loading'); b.removeAttribute('aria-busy'); b.disabled = false; }); });
  root.setAttribute('data-ready', ''); // last: if anything above throws, CSS fallbacks keep content visible
})();
