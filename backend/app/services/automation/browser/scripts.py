# ruff: noqa: E501
"""The JavaScript the Playwright driver runs inside the page (V0.10).

Two entry points share one `collect()` so that finding a control again uses
exactly the enumeration that discovered it:

    SNAPSHOT_JS   read every visible control and the page's safety signals
    RESOLVE_JS    find the element for a locator (and an option, for a group)
    READ_JS       what a locator's control holds right now

Nothing here clicks, submits or navigates, and nothing returns page text beyond
labels, option texts and button captions (no form values in a snapshot).
"""

_COLLECT = r"""
const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
const trunc = (s, n) => (s.length > n ? s.slice(0, n) : s);

const isRendered = (el) => {
  if (!el || !el.isConnected) return false;
  const st = getComputedStyle(el);
  if (st.display === 'none' || st.visibility === 'hidden' || st.visibility === 'collapse') return false;
  const rects = el.getClientRects();
  if (!rects.length) return false;
  const r = rects[0];
  // Hidden "honeypot" fields are pushed far off screen.
  if (r.right < -10 || r.bottom < -10) return false;
  return true;
};

const textWithout = (node, except) => {
  const clone = node.cloneNode(true);
  clone.querySelectorAll('input, select, textarea, button, script, style').forEach((n) => n.remove());
  return norm(clone.textContent);
};

const cleanLabel = (raw) => {
  let text = norm(raw);
  text = text.replace(/\s*\((required|optional|erforderlich|optional|pflichtfeld)\)\s*$/i, '');
  text = text.replace(/\s*[*✱]+\s*$/, '');
  return norm(text);
};

const unstableId = (id) =>
  /^(:r|:R|«r|_r_|radix-|headlessui-|react-|rc[_-]|ember\d|mui-|__)/.test(id) ||
  /\d{5,}/.test(id) ||
  /[0-9a-f]{8}-[0-9a-f]{4}-/.test(id);

const labelOf = (el) => {
  const parts = [];
  if (el.labels && el.labels.length) {
    for (const l of el.labels) parts.push(textWithout(l, el));
  }
  const by = el.getAttribute('aria-labelledby');
  if (by) {
    for (const id of by.split(/\s+/)) {
      const n = document.getElementById(id);
      if (n) parts.push(norm(n.textContent));
    }
  }
  const aria = el.getAttribute('aria-label');
  if (aria) parts.push(norm(aria));
  let text = parts.filter(Boolean)[0] || '';
  if (!text) {
    const fs = el.closest('fieldset');
    const legend = fs && fs.querySelector('legend');
    if (legend) text = norm(legend.textContent);
  }
  if (!text) text = norm(el.getAttribute('title') || '');
  if (!text) text = norm(el.getAttribute('placeholder') || '');
  return text;
};

const groupLabelOf = (first) => {
  const container = first.closest('fieldset, [role=radiogroup], [role=group]');
  if (container) {
    const legend = container.tagName === 'FIELDSET' ? container.querySelector(':scope > legend') : null;
    if (legend) return norm(legend.textContent);
    const by = container.getAttribute('aria-labelledby');
    if (by) {
      const n = document.getElementById(by.split(/\s+/)[0]);
      if (n) return norm(n.textContent);
    }
    const aria = container.getAttribute('aria-label');
    if (aria) return norm(aria);
  }
  return '';
};

const formsList = Array.from(document.forms);

const formContextOf = (el) => {
  const f = el.form || el.closest('form');
  if (!f) return { form: null, index: null };
  const index = formsList.indexOf(f);
  const name = f.getAttribute('aria-label') || f.id || f.getAttribute('name') || null;
  return { form: name ? trunc(name, 80) : null, index: index >= 0 ? index : null };
};

const SKIP_TYPES = new Set(['hidden', 'submit', 'button', 'reset', 'image', 'search', 'color', 'range']);

const collect = () => {
  const out = [];
  const els = [];
  const add = (ctrl, el) => { out.push(ctrl); els.push(el); return out.length - 1; };
  const seenGroups = new Map();
  let password = false;
  const nodes = Array.from(
    document.querySelectorAll(
      'input, textarea, select, [role=combobox], [role=listbox], [role=radiogroup], [role=switch], ' +
        '[role=checkbox], [role=textbox], [contenteditable=""], [contenteditable="true"]'
    )
  );
  for (const el of nodes) {
    const tag = el.tagName.toLowerCase();
    if (el.closest('form[role=search], [role=search]')) continue;
    const ctx = formContextOf(el);
    const common = {
      dom_id: el.id || null,
      id_stable: el.id ? !unstableId(el.id) : true,
      name: el.getAttribute('name') || null,
      form: ctx.form,
      form_index: ctx.index,
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      readonly: !!el.readOnly || el.getAttribute('aria-readonly') === 'true',
    };
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'password') {
        if (isRendered(el)) password = true;
        continue;
      }
      if (SKIP_TYPES.has(type)) continue;
      if (type === 'radio') {
        if (!isRendered(el) && !isRendered(el.parentElement)) continue;
        const gkey = `${ctx.index}|${el.name}`;
        if (!el.name) {
          // A radio without a name is not a group the browser links; treat it as unsupported.
          continue;
        }
        let entry = seenGroups.get(gkey);
        if (!entry) {
          const rawG = groupLabelOf(el);
          const g = { kind: 'radio', ...common, label: cleanLabel(rawG), required: /[*✱]\s*$/.test(rawG), options: [], meta: {} };
          g.dom_id = null; // a group has no single id
          entry = { g, index: add(g, [el]) };
          seenGroups.set(gkey, entry);
        } else {
          els[entry.index].push(el);
        }
        const optLabel = el.labels && el.labels.length ? textWithout(el.labels[0], el) : norm(el.getAttribute('aria-label') || el.value);
        entry.g.options.push({ value: el.value, label: optLabel || el.value });
        if (el.required || el.getAttribute('aria-required') === 'true') entry.g.required = true;
        continue;
      }
      if (type === 'checkbox') {
        if (!isRendered(el) && !isRendered(el.parentElement)) continue;
        const sameName = el.name
          ? Array.from(document.querySelectorAll('input[type=checkbox]')).filter(
              (c) => c.name === el.name && (c.form || null) === (el.form || null)
            )
          : [];
        if (sameName.length > 1) {
          const gkey = `${ctx.index}|cb|${el.name}`;
          let entry = seenGroups.get(gkey);
          if (!entry) {
            const rawG = groupLabelOf(el);
            const g = { kind: 'checkbox_group', ...common, label: cleanLabel(rawG), required: /[*✱]\s*$/.test(rawG), options: [], meta: {} };
            g.dom_id = null;
            entry = { g, index: add(g, [el]) };
            seenGroups.set(gkey, entry);
          } else {
            els[entry.index].push(el);
          }
          const optLabel = el.labels && el.labels.length ? textWithout(el.labels[0], el) : el.value;
          entry.g.options.push({ value: el.value, label: optLabel || el.value });
          if (el.required) entry.g.required = true;
          continue;
        }
        const raw = labelOf(el);
        add({
          kind: 'checkbox', ...common, label: cleanLabel(raw),
          required: !!el.required || el.getAttribute('aria-required') === 'true' || /[*✱]\s*$/.test(raw),
          options: [{ value: el.value || 'on', label: cleanLabel(raw) }], meta: {},
        }, el);
        continue;
      }
      if (type === 'file') {
        if (!isRendered(el) && !isRendered(el.parentElement)) continue;
        const raw = labelOf(el);
        add({
          kind: 'file', ...common, label: cleanLabel(raw),
          required: !!el.required || /[*✱]\s*$/.test(raw), options: [],
          meta: { accept: el.getAttribute('accept') || null, multiple: !!el.multiple },
        }, el);
        continue;
      }
      if (!isRendered(el) || el.getAttribute('aria-hidden') === 'true') continue;
      const raw = labelOf(el);
      const ml = el.maxLength;
      add({
        kind: type, ...common, label: cleanLabel(raw),
        required: !!el.required || el.getAttribute('aria-required') === 'true' || /[*✱]\s*$/.test(raw),
        max_length: ml > 0 && ml < 1000000 ? ml : null, options: [],
        meta: {
          placeholder: trunc(el.getAttribute('placeholder') || '', 120) || null,
          autocomplete: el.getAttribute('autocomplete') || null,
          min: el.getAttribute('min'), max: el.getAttribute('max'), pattern: el.getAttribute('pattern'),
        },
      }, el);
      continue;
    }
    if (tag === 'textarea') {
      if (!isRendered(el) || el.getAttribute('aria-hidden') === 'true') continue;
      const raw = labelOf(el);
      const ml = el.maxLength;
      add({
        kind: 'textarea', ...common, label: cleanLabel(raw),
        required: !!el.required || el.getAttribute('aria-required') === 'true' || /[*✱]\s*$/.test(raw),
        max_length: ml > 0 && ml < 1000000 ? ml : null, options: [],
        meta: { placeholder: trunc(el.getAttribute('placeholder') || '', 120) || null },
      }, el);
      continue;
    }
    if (tag === 'select') {
      if (!isRendered(el) || el.getAttribute('aria-hidden') === 'true') continue;
      const raw = labelOf(el);
      const options = Array.from(el.options)
        .filter((o) => o.value !== '')
        .map((o) => ({ value: o.value, label: norm(o.label || o.textContent) }));
      add({
        kind: 'select', ...common, label: cleanLabel(raw),
        required: !!el.required || el.getAttribute('aria-required') === 'true' || /[*✱]\s*$/.test(raw),
        options, meta: { multiple: !!el.multiple },
      }, el);
      continue;
    }
    // ARIA / contenteditable widgets: found, not driven.
    if (!isRendered(el)) continue;
    if (el.closest('input, select, textarea')) continue;
    const role = el.getAttribute('role') || 'textbox';
    const raw = labelOf(el) || norm(el.getAttribute('aria-label') || '');
    add({
      kind: 'custom', ...common, label: cleanLabel(raw),
      required: el.getAttribute('aria-required') === 'true', options: [], meta: { role },
    }, el);
  }
  // Ordinals among controls with the same kind and label.
  const counts = new Map();
  for (const c of out) {
    const key = `${c.kind}|${c.label.toLowerCase()}`;
    c.ordinal = counts.get(key) || 0;
    counts.set(key, c.ordinal + 1);
    c.max_length = c.max_length === undefined ? null : c.max_length;
  }
  return { controls: out, elements: els, password };
};
"""

_SIGNALS = r"""
const visibleBox = (el, minSize) => {
  if (!isRendered(el)) return false;
  const r = el.getBoundingClientRect();
  return r.width >= minSize && r.height >= minSize;
};

const challengeMarkers = () => {
  const markers = new Set();
  const frameSrc = (needle) =>
    Array.from(document.querySelectorAll('iframe')).some(
      (f) => (f.src || '').includes(needle) && visibleBox(f, 20)
    );
  if (frameSrc('recaptcha/api2/bframe') || frameSrc('recaptcha/api2/anchor') || frameSrc('recaptcha/enterprise/anchor')) markers.add('recaptcha');
  if (Array.from(document.querySelectorAll('.g-recaptcha')).some(
      (n) => n.getAttribute('data-size') !== 'invisible' && visibleBox(n, 20))) markers.add('recaptcha');
  if (frameSrc('hcaptcha.com') || Array.from(document.querySelectorAll('.h-captcha')).some((n) => visibleBox(n, 20))) markers.add('hcaptcha');
  if (frameSrc('challenges.cloudflare.com') || Array.from(document.querySelectorAll('.cf-turnstile')).some((n) => visibleBox(n, 20))) markers.add('turnstile');
  if (frameSrc('arkoselabs.com') || frameSrc('funcaptcha')) markers.add('arkose');
  if (frameSrc('captcha-delivery.com') || frameSrc('geo.captcha-delivery')) markers.add('datadome');
  if (document.getElementById('px-captcha')) markers.add('perimeterx');
  const title = norm(document.title).toLowerCase();
  if (/^(just a moment|attention required|checking your browser|security check|access denied|verifying you are human)/.test(title)) markers.add('interstitial');
  return Array.from(markers);
};

const buttonLabels = () => {
  const labels = [];
  const nodes = document.querySelectorAll(
    'button, input[type=submit], input[type=button], input[type=image], [role=button], a[role=button], a.button, a.btn'
  );
  for (const n of nodes) {
    if (!isRendered(n)) continue;
    const text = norm(n.tagName === 'INPUT' ? (n.value || n.getAttribute('alt') || '') : (n.getAttribute('aria-label') || n.textContent));
    if (text && text.length <= 80) labels.push(text);
    if (labels.length >= 60) break;
  }
  return labels;
};
"""

SNAPSHOT_JS = (
    "(args) => {"
    + _COLLECT
    + _SIGNALS
    + r"""
  const { controls, password } = collect();
  const limit = args.max;
  return {
    url: location.href,
    title: trunc(norm(document.title), 300),
    controls: controls.slice(0, limit),
    truncated: controls.length > limit,
    signals: { has_password: password, challenge_markers: challengeMarkers(), button_labels: buttonLabels() },
  };
}"""
)

# Find the one element a locator (and, for a group, an option value) refers to.
_FIND = r"""
const find = (loc, optionValue) => {
  const { controls, elements } = collect();
  const sameLabel = (c) => c.label.toLowerCase() === (loc.label || '').toLowerCase();
  const matches = [];
  controls.forEach((c, i) => {
    if (c.kind !== loc.kind) return;
    if (loc.form_index !== null && loc.form_index !== undefined && c.form_index !== loc.form_index) return;
    matches.push(i);
  });
  let index = -1;
  // 1. a stable id, 2. the name, 3. the label (the nth with that label).
  if (loc.dom_id) index = matches.find((i) => controls[i].dom_id === loc.dom_id) ?? -1;
  if (index < 0 && loc.name) {
    const byName = matches.filter((i) => controls[i].name === loc.name);
    if (byName.length === 1) index = byName[0];
    else if (byName.length > 1) {
      const sub = byName.filter((i) => sameLabel(controls[i]));
      if (sub.length) index = sub[Math.min(loc.ordinal, sub.length - 1)];
    }
  }
  if (index < 0) {
    const byLabel = matches.filter((i) => sameLabel(controls[i]));
    if (byLabel.length) index = byLabel[Math.min(loc.ordinal, byLabel.length - 1)];
  }
  if (index < 0) return null;
  const target = elements[index];
  if (Array.isArray(target)) {
    if (optionValue === null || optionValue === undefined) return target;
    return target.find((e) => e.value === optionValue) || null;
  }
  return target;
};
"""

RESOLVE_JS = (
    "(args) => {"
    + _COLLECT
    + _FIND
    + "\n  return find(args.locator, args.option === undefined ? null : args.option);\n}"
)

READ_JS = (
    "(args) => {"
    + _COLLECT
    + _FIND
    + r"""
  const loc = args.locator;
  const found = find(loc, null);
  if (!found || (Array.isArray(found) && !found.length)) return { found: false };
  if (Array.isArray(found)) {
    const checked = found.filter((e) => e.checked);
    return {
      found: true,
      selected: checked.map((e) => e.value),
      selected_labels: checked.map((e) => (e.labels && e.labels.length ? textWithout(e.labels[0], e) : e.value)),
    };
  }
  const el = found;
  const tag = el.tagName.toLowerCase();
  if (tag === 'select') {
    const chosen = Array.from(el.selectedOptions).filter((o) => o.value !== '');
    return { found: true, text: chosen.length ? chosen[0].value : '', selected: chosen.map((o) => o.value),
             selected_labels: chosen.map((o) => norm(o.label || o.textContent)) };
  }
  if (tag === 'input') {
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    if (type === 'checkbox' || type === 'radio') return { found: true, checked: !!el.checked };
    if (type === 'file') return { found: true, files: Array.from(el.files || []).map((f) => [f.name, f.size]) };
    return { found: true, text: el.value };
  }
  if (tag === 'textarea') return { found: true, text: el.value };
  return { found: true, text: el.isContentEditable ? el.textContent : '' };
}"""
)

# What kind of element a resolved handle is (the write guard in Python checks it).
ELEMENT_KIND_JS = r"""(el) => {
  const tag = el.tagName.toLowerCase();
  const type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : null;
  return { tag, type, readonly: !!el.readOnly, disabled: !!el.disabled };
}"""
