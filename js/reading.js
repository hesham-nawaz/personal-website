// Search and filters for reading.html. The entries are rendered into the page
// by tool/reading.py; this script only shows, hides and reorders them, and
// keeps the filters in the URL so a filtered view can be shared as a link.
//
// Filter rules: choices within one control widen the results (any of the
// selected companies; any of the selected tags in a row), and the controls
// narrow each other (companies AND topics AND techniques AND type AND search).
document.addEventListener('DOMContentLoaded', function () {
    const list = document.querySelector('.reading-list');
    if (!list) return;

    const $ = (id) => document.getElementById(id);
    const searchInput = $('reading-q');
    const sortSelect = $('reading-sort');
    const tagGroupsEl = $('reading-tag-groups');
    const countEl = $('reading-count');
    const clearBtn = $('reading-clear');
    const expandBtn = $('reading-expand');
    const emptyEl = $('reading-empty');
    const typeButtons = Array.from(document.querySelectorAll('.reading-type-btn'));
    const picker = $('reading-company-picker');
    const pickerBtn = $('reading-company-btn');
    const pickerMenu = $('reading-company-menu');

    let defs = {};
    try {
        defs = JSON.parse($('reading-tag-defs').textContent);
    } catch (e) { /* tags still work, just without groups or definitions */ }
    const groupOf = (tag) => (defs[tag] && defs[tag].group) || 'Other';

    const entries = Array.from(list.querySelectorAll('.reading-entry')).map((el) => ({
        el,
        company: el.dataset.company,
        tags: el.dataset.tags ? el.dataset.tags.split('|') : [],
        year: parseInt(el.dataset.year, 10) || 0,
        type: el.dataset.type,
        order: parseInt(el.dataset.order, 10),
        // Search covers the header and the notes; lowercased once up front.
        text: el.textContent.toLowerCase().replace(/\s+/g, ' '),
    }));

    const state = { q: '', companies: [], tags: [], type: '', sort: 'reviewed' };

    // ---- Build the controls from the entries -------------------------------

    const companies = Array.from(new Set(entries.map((e) => e.company).filter(Boolean)))
        .sort((a, b) => a.localeCompare(b));
    const companyBoxes = {};
    companies.forEach((c) => {
        const row = document.createElement('label');
        row.className = 'reading-company-option';
        row.innerHTML = '<input type="checkbox"><span class="reading-company-name"></span><span class="reading-chip-count"></span>';
        const box = row.querySelector('input');
        box.value = c;
        row.querySelector('.reading-company-name').textContent = c;
        box.addEventListener('change', () => toggleCompany(c));
        companyBoxes[c] = row;
        $('reading-company-options').appendChild(row);
    });

    const allTags = new Set(entries.flatMap((e) => e.tags));
    const groups = {};
    Object.keys(defs).forEach((tag) => {
        if (allTags.has(tag)) (groups[groupOf(tag)] = groups[groupOf(tag)] || []).push(tag);
    });
    const undefinedTags = Array.from(allTags).filter((t) => !defs[t]).sort();
    if (undefinedTags.length) groups.Other = (groups.Other || []).concat(undefinedTags);

    const tagButtons = {};
    Object.keys(groups).forEach((group) => {
        const row = document.createElement('div');
        row.className = 'reading-tag-group';
        const label = document.createElement('span');
        label.className = 'reading-tag-group-label';
        label.textContent = group;
        row.appendChild(label);
        groups[group].forEach((tag) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'reading-chip';
            btn.dataset.tag = tag;
            btn.setAttribute('aria-pressed', 'false');
            if (defs[tag] && defs[tag].definition) btn.title = defs[tag].definition;
            btn.innerHTML = `<span></span><span class="reading-chip-count"></span>`;
            btn.firstChild.textContent = tag;
            btn.addEventListener('click', () => toggleTag(tag));
            tagButtons[tag] = btn;
            row.appendChild(btn);
        });
        tagGroupsEl.appendChild(row);
    });

    // On phones the tag chips fill a whole screen, so start them folded away.
    const tagPanel = $('reading-tag-panel');
    if (window.matchMedia('(max-width: 768px)').matches) tagPanel.open = false;

    // Tag definitions as tooltips on the in-card tags too.
    list.querySelectorAll('.reading-tag').forEach((btn) => {
        const d = defs[btn.dataset.tag];
        if (d && d.definition) btn.title = d.definition;
    });

    // The sync link is for me: reveal it with ?owner once per browser.
    const params = new URLSearchParams(location.search);
    try {
        if (params.has('owner')) localStorage.setItem('reading-owner', '1');
        if (localStorage.getItem('reading-owner')) $('reading-sync').hidden = false;
    } catch (e) {
        if (params.has('owner')) $('reading-sync').hidden = false;
    }

    // ---- State <-> URL -----------------------------------------------------

    function readUrl() {
        const p = new URLSearchParams(location.search);
        state.q = p.get('q') || '';
        state.companies = (p.get('company') || '').split(',').filter((c) => companies.includes(c));
        state.tags = (p.get('tags') || '').split(',').filter((t) => allTags.has(t));
        state.type = p.get('type') || '';
        state.sort = ['reviewed', 'published', 'company'].includes(p.get('sort')) ? p.get('sort') : 'reviewed';
    }

    function writeUrl() {
        const p = new URLSearchParams();
        if (state.q) p.set('q', state.q);
        if (state.companies.length) p.set('company', state.companies.join(','));
        if (state.tags.length) p.set('tags', state.tags.join(','));
        if (state.type) p.set('type', state.type);
        if (state.sort !== 'reviewed') p.set('sort', state.sort);
        if (params.has('owner')) p.set('owner', '');
        const qs = p.toString().replace(/=(&|$)/g, '$1');
        history.replaceState(null, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
    }

    // ---- Filtering ---------------------------------------------------------

    function selectedByGroup() {
        const out = {};
        state.tags.forEach((t) => { (out[groupOf(t)] = out[groupOf(t)] || []).push(t); });
        return out;
    }

    // `skip` leaves one control out, which is how each control's counts are
    // computed: "how many reviews have this, given everything else you chose".
    function matches(e, skip) {
        if (skip !== 'company' && state.companies.length && !state.companies.includes(e.company)) return false;
        if (skip !== 'type' && state.type && e.type !== state.type) return false;
        const byGroup = selectedByGroup();
        for (const group in byGroup) {
            if (group !== skip && !byGroup[group].some((t) => e.tags.includes(t))) return false;
        }
        if (state.q) {
            const words = state.q.toLowerCase().split(/\s+/).filter(Boolean);
            if (!words.every((w) => e.text.includes(w))) return false;
        }
        return true;
    }

    function sortKey(a, b) {
        if (state.sort === 'published') return (b.year - a.year) || (a.order - b.order);
        if (state.sort === 'company') return a.company.localeCompare(b.company) || (a.order - b.order);
        return a.order - b.order;
    }

    function render() {
        const visible = entries.filter((e) => matches(e));
        const shown = new Set(visible);
        entries.slice().sort(sortKey).forEach((e) => {
            e.el.hidden = !shown.has(e);
            list.appendChild(e.el);
        });

        Object.keys(groups).forEach((group) => {
            const pool = entries.filter((e) => matches(e, group));
            groups[group].forEach((tag) => {
                const btn = tagButtons[tag];
                const active = state.tags.includes(tag);
                const n = pool.filter((e) => e.tags.includes(tag)).length;
                btn.setAttribute('aria-pressed', String(active));
                btn.lastChild.textContent = n;
                btn.disabled = !active && n === 0;
            });
        });

        const companyPool = entries.filter((e) => matches(e, 'company'));
        companies.forEach((c) => {
            const row = companyBoxes[c];
            const box = row.firstChild;
            const n = companyPool.filter((e) => e.company === c).length;
            box.checked = state.companies.includes(c);
            box.disabled = !box.checked && n === 0;
            row.classList.toggle('is-disabled', box.disabled);
            row.lastChild.textContent = n;
        });
        $('reading-company-label').textContent =
            state.companies.length === 0 ? 'All companies'
                : state.companies.length === 1 ? state.companies[0]
                    : `${state.companies.length} companies`;
        pickerBtn.classList.toggle('has-selection', state.companies.length > 0);
        $('reading-company-clear').hidden = !state.companies.length;

        $('reading-tag-active').textContent = state.tags.length ? `(${state.tags.length} selected)` : '';
        list.querySelectorAll('.reading-tag').forEach((btn) => {
            btn.classList.toggle('is-active', state.tags.includes(btn.dataset.tag));
        });
        list.querySelectorAll('.reading-company').forEach((btn) => {
            btn.classList.toggle('is-active', state.companies.includes(btn.dataset.company));
        });

        searchInput.value = state.q;
        sortSelect.value = state.sort;
        typeButtons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.type === state.type)));

        const filtered = state.q || state.companies.length || state.tags.length || state.type;
        countEl.textContent = filtered
            ? `Showing ${visible.length} of ${entries.length} reviews`
            : `${entries.length} reviews`;
        clearBtn.hidden = !filtered;
        emptyEl.hidden = visible.length > 0;
        writeUrl();
    }

    const toggle = (arr, x) => (arr.includes(x) ? arr.filter((y) => y !== x) : arr.concat(x));

    function toggleTag(tag) {
        state.tags = toggle(state.tags, tag);
        render();
    }

    function toggleCompany(company) {
        state.companies = toggle(state.companies, company);
        render();
    }

    function clearFilters() {
        Object.assign(state, { q: '', companies: [], tags: [], type: '' });
        render();
    }

    // ---- Company menu ------------------------------------------------------

    function setMenu(open) {
        pickerMenu.hidden = !open;
        pickerBtn.setAttribute('aria-expanded', String(open));
        if (!open) return;
        // Keep the menu on screen: slide it left if it would run off the edge.
        pickerMenu.style.left = '0px';
        const overflow = pickerMenu.getBoundingClientRect().right - (document.documentElement.clientWidth - 12);
        if (overflow > 0) pickerMenu.style.left = `${-overflow}px`;
    }
    pickerBtn.addEventListener('click', () => setMenu(pickerMenu.hidden));
    $('reading-company-clear').addEventListener('click', () => { state.companies = []; render(); });
    document.addEventListener('click', (ev) => {
        if (!pickerMenu.hidden && !picker.contains(ev.target)) setMenu(false);
    });
    picker.addEventListener('keydown', (ev) => {
        if (ev.key === 'Escape' && !pickerMenu.hidden) {
            setMenu(false);
            pickerBtn.focus();
        }
    });
    picker.addEventListener('focusout', (ev) => {
        if (ev.relatedTarget && !picker.contains(ev.relatedTarget)) setMenu(false);
    });

    // ---- Events ------------------------------------------------------------

    let searchTimer;
    searchInput.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => { state.q = searchInput.value.trim(); render(); }, 120);
    });
    sortSelect.addEventListener('change', () => { state.sort = sortSelect.value; render(); });
    typeButtons.forEach((b) => b.addEventListener('click', () => { state.type = b.dataset.type; render(); }));
    clearBtn.addEventListener('click', clearFilters);
    $('reading-empty-clear').addEventListener('click', clearFilters);

    list.addEventListener('click', (ev) => {
        const tagBtn = ev.target.closest('.reading-tag');
        if (tagBtn) { toggleTag(tagBtn.dataset.tag); return; }
        const coBtn = ev.target.closest('.reading-company');
        if (coBtn) toggleCompany(coBtn.dataset.company);
    });

    expandBtn.addEventListener('click', () => {
        const open = expandBtn.textContent === 'Expand all';
        entries.forEach((e) => { if (!e.el.hidden) e.el.querySelector('details').open = open; });
        expandBtn.textContent = open ? 'Collapse all' : 'Expand all';
    });

    // #entry-id opens that review, clearing any filter that would hide it.
    function openFromHash() {
        const id = decodeURIComponent(location.hash.slice(1));
        const target = id && document.getElementById(id);
        if (!target || !target.classList.contains('reading-entry')) return;
        if (target.hidden) clearFilters();
        target.querySelector('details').open = true;
        target.scrollIntoView({ block: 'start' });
        target.classList.remove('is-target');
        void target.offsetWidth; // restart the highlight animation
        target.classList.add('is-target');
    }
    window.addEventListener('hashchange', openFromHash);

    readUrl();
    render();
    openFromHash();
});
