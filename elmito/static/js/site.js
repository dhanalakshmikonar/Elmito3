(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  /* Mobile navigation keeps desktop category and help links available on touch screens. */
  const mobileMenu = $('#mobile-nav');
  const mobileMenuToggle = $('[data-mobile-menu-toggle]');
  const setMobileMenu = (open) => {
    if (!mobileMenu || !mobileMenuToggle) return;
    mobileMenu.hidden = !open;
    mobileMenuToggle.setAttribute('aria-expanded', String(open));
    mobileMenuToggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  };
  mobileMenuToggle?.addEventListener('click', () => setMobileMenu(mobileMenu.hidden));
  mobileMenu?.querySelectorAll('a').forEach((link) => link.addEventListener('click', () => setMobileMenu(false)));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && mobileMenu && !mobileMenu.hidden) {
      setMobileMenu(false);
      mobileMenuToggle?.focus();
    }
  });

  /* Search panel (links fall back to /search/ without JS) */
  const panel = $('#search-panel');
  const toggles = $$('[data-search-toggle]');
  toggles.forEach((el) => el.addEventListener('click', (e) => {
    if (!panel) return;
    e.preventDefault();
    setMobileMenu(false);
    const open = panel.hidden;
    panel.hidden = !open;
    toggles.forEach((t) => t.setAttribute('aria-expanded', String(open)));
    if (open) $('input', panel).focus();
  }));

  /* Listing: collapsible filters, auto-submit sort */
  const filters = $('#filters-panel');
  const filterToggle = $('[data-filters-toggle]');
  if (filters && filterToggle) {
    if (filters.dataset.collapsed === 'true') filters.hidden = true;
    filterToggle.setAttribute('aria-expanded', String(!filters.hidden));
    filterToggle.addEventListener('click', () => {
      filters.hidden = !filters.hidden;
      filterToggle.setAttribute('aria-expanded', String(!filters.hidden));
    });
  }
  const sort = $('[data-sort]');
  if (sort) sort.addEventListener('change', () => sort.form.requestSubmit());

  /* Product gallery */
  const gallery = $('[data-gallery]');
  let showColor = () => {};
  if (gallery) {
    const track = $('.gallery__track', gallery);
    const slides = $$('.gallery__slide', gallery);
    const thumbs = $$('.gallery__thumbs button', gallery);
    const lightbox = $('#lightbox');

    const markCurrent = () => {
      const visible = slides.filter((s) => !s.hidden);
      const index = Math.round(track.scrollLeft / track.clientWidth);
      const current = visible[index];
      thumbs.forEach((t) => t.setAttribute('aria-current', String(!!current && t.dataset.id === current.dataset.id)));
    };
    track.addEventListener('scroll', () => requestAnimationFrame(markCurrent), { passive: true });
    thumbs.forEach((t) => t.addEventListener('click', () => {
      const slide = slides.find((s) => s.dataset.id === t.dataset.id);
      if (slide) slide.scrollIntoView({ inline: 'start', block: 'nearest', behavior: 'smooth' });
    }));

    showColor = (name) => {
      const key = (name || '').toLowerCase();
      const matches = (el) => !el.dataset.color || el.dataset.color === key;
      const any = slides.some((s) => s.dataset.color === key);
      slides.forEach((s) => { s.hidden = any && !matches(s); });
      thumbs.forEach((t) => { t.hidden = any && !matches(t); });
      track.scrollLeft = 0;
      markCurrent();
    };

    const canHoverZoom = () => matchMedia('(hover: hover) and (min-width: 900px)').matches;
    slides.forEach((slide) => {
      slide.addEventListener('mouseenter', () => canHoverZoom() && slide.classList.add('is-zoomed'));
      slide.addEventListener('mouseleave', () => slide.classList.remove('is-zoomed'));
      slide.addEventListener('mousemove', (e) => {
        if (!canHoverZoom()) return;
        const r = slide.getBoundingClientRect();
        slide.style.setProperty('--zx', ((e.clientX - r.left) / r.width) * 100 + '%');
        slide.style.setProperty('--zy', ((e.clientY - r.top) / r.height) * 100 + '%');
      });
      slide.addEventListener('click', () => {
        if (!lightbox || !lightbox.showModal) return;
        const img = $('img', lightbox);
        const src = $('img', slide);
        img.src = src.currentSrc || src.src;
        img.alt = src.alt;
        lightbox.showModal();
      });
    });
    if (lightbox) lightbox.addEventListener('click', () => lightbox.close());
    markCurrent();
  }

  /* Size guide dialog */
  $$('[data-open-dialog]').forEach((b) => b.addEventListener('click', () => $('#' + b.dataset.openDialog)?.showModal()));
  $$('[data-close-dialog]').forEach((b) => b.addEventListener('click', () => b.closest('dialog').close()));

  /* Variant selection */
  const form = $('[data-pdp]');
  if (form) {
    const matrix = JSON.parse($('#variant-data').textContent);
    const variantInput = $('[name=variant]', form);
    const stockEl = $('[data-stock]', form);
    const errorEl = $('[data-error]', form);
    const qty = $('[name=quantity]', form);
    const colorRadios = $$('input[name=color]', form);
    const sizeRadios = $$('input[name=size]', form);
    const checked = (radios) => radios.find((r) => r.checked);
    const MAX_QTY = 10;

    const clampQty = (max) => {
      const value = parseInt(qty.value, 10) || 1;
      qty.max = String(max);
      qty.value = String(Math.min(Math.max(1, value), max));
    };

    const update = () => {
      const color = checked(colorRadios)?.value;
      const entry = matrix.find((m) => m.color === color);
      sizeRadios.forEach((r) => {
        const size = entry?.sizes.find((s) => s.size === r.value);
        r.disabled = !size || !size.in_stock;
        if (r.disabled && r.checked) r.checked = false;
      });
      const size = checked(sizeRadios)?.value;
      const variant = entry?.sizes.find((s) => s.size === size);
      variantInput.value = variant ? variant.variant_id : '';
      stockEl.className = 'stock';
      if (!entry || entry.sizes.every((s) => !s.in_stock)) {
        stockEl.textContent = 'This colour is sold out.';
        stockEl.classList.add('stock--out');
      } else if (!variant) {
        stockEl.textContent = 'Select a size.';
      } else if (variant.stock <= 5) {
        stockEl.textContent = `Only ${variant.stock} left in this size.`;
        stockEl.classList.add('stock--low');
      } else {
        stockEl.textContent = 'In stock.';
      }
      clampQty(variant ? Math.min(variant.stock, MAX_QTY) : MAX_QTY);
      errorEl.textContent = '';
    };

    colorRadios.forEach((r) => r.addEventListener('change', () => { showColor(r.value); update(); }));
    sizeRadios.forEach((r) => r.addEventListener('change', update));
    $$('[data-qty-step]', form).forEach((b) => b.addEventListener('click', () => {
      qty.value = String((parseInt(qty.value, 10) || 1) + parseInt(b.dataset.qtyStep, 10));
      clampQty(parseInt(qty.max, 10) || MAX_QTY);
    }));
    qty.addEventListener('change', () => clampQty(parseInt(qty.max, 10) || MAX_QTY));

    form.addEventListener('submit', (e) => {
      if (variantInput.value) return;
      e.preventDefault();
      errorEl.textContent = 'Please select a size.';
      form.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });

    const initial = checked(colorRadios);
    if (initial) showColor(initial.value);
    update();

    /* Sticky add-to-cart on small screens */
    const anchor = $('[data-add]', form);
    const bar = $('#sticky-buy');
    if (anchor && bar && 'IntersectionObserver' in window) {
      new IntersectionObserver(([entry]) => {
        bar.classList.toggle('is-visible', !entry.isIntersecting && entry.boundingClientRect.top < 0);
      }).observe(anchor);
    }
  }
})();
