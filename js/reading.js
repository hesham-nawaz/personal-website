// Search and filters for reading.html. The entries are rendered into the page
// by tool/reading.py; this script only shows, hides and reorders them, and
// keeps the filters in the URL so a filtered view can be shared as a link.
document.addEventListener('DOMContentLoaded', function () {
    const list = document.querySelector('.reading-list');
    if (!list) return;

    const $ = (id) => document.getElementById(id);
    const searchInput = $('reading-q');
    const companySelect = $('reading-company');
    const sortSelect = $('reading-sort');
    const tagGroupsEl = $('reading-tag-groups');
    const countEl = $('reading-count');
    const clearBtn = $('reading-clear');
    const expandBtn = $('reading-expand');
    const emptyEl = $('reading-empty');
    const typeButtons = Array.from(document.querySelectorAll('.reading-type-btn'));

    let defs = {};
    try {
        defs = JSON.parse($('reading-tag-defs').textContent);
    } catch (e) { /* tags still work, just without groups or definitions */ }

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

    const state = { q: '', company: '', tags: [], type: '', sort: 'reviewed' };

    // ---- Build the controls from the entries -------------------------------

    const companyCounts = countBy(entries.map((e) => e.company).filter(Boolean));
    Object.keys(companyCounts).sort((a, b) => a.localeCompare(b)).forEach((c) => {
        const opt = document.createElement('option');
        opt.value = c;
        opt.textContent = `${c} (${companyCounts[c]})`;
        companySelect.appendChild(opt);
    });

    const allTags = new Set(entries.flatMap((e) => e.tags));
    const groups = {};
    Object.keys(defs).forEach((tag) => {
        if (!allTags.has(tag)) return;
        (groups[defs[tag].group] = groups[defs[tag].group] || []).push(tag);
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
        state.company = companyCounts[p.get('company')] ? p.get('company') : '';
        state.tags = (p.get('tags') || '').split(',').filter((t) => allTags.has(t));
        state.type = p.get('type') || '';
        state.sort = ['reviewed', 'published', 'company'].includes(p.get('sort')) ? p.get('sort') : 'reviewed';
    }

    function writeUrl() {
        const p = new URLSearchParams();
        if (state.q) p.set('q', state.q);
        if (state.company) p.set('company', state.company);
        if (state.tags.length) p.set('tags', state.tags.join(','));
        if (state.type) p.set('type', state.type);
        if (state.sort !== 'reviewed') p.set('sort', state.sort);
        if (params.has('owner')) p.set('owner', '');
        const qs = p.toString().replace(/=(&|$)/g, '$1');
        history.replaceState(null, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
    }

    // ---- Filtering ---------------------------------------------------------

    function matches(e, skip) {
        if (skip !== 'company' && state.company && e.company !== state.company) return false;
        if (skip !== 'type' && state.type && e.type !== state.type) return false;
        if (skip !== 'tags' && !state.tags.every((t) => e.tags.includes(t))) return false;
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

        // Tag counts answer "how many would I see if I added this tag?"
        const tagPool = entries.filter((e) => matches(e, 'tags'));
        Object.entries(tagButtons).forEach(([tag, btn]) => {
            const active = state.tags.includes(tag);
            const n = tagPool.filter((e) => e.tags.includes(tag) && state.tags.every((t) => e.tags.includes(t))).length;
            btn.setAttribute('aria-pressed', String(active));
            btn.lastChild.textContent = n;
            btn.disabled = !active && n === 0;
        });
        $('reading-tag-active').textContent = state.tags.length ? `(${state.tags.length} selected)` : '';
        list.querySelectorAll('.reading-tag').forEach((btn) => {
            btn.classList.toggle('is-active', state.tags.includes(btn.dataset.tag));
        });
        list.querySelectorAll('.reading-company').forEach((btn) => {
            btn.classList.toggle('is-active', btn.dataset.company === state.company);
        });

        searchInput.value = state.q;
        companySelect.value = state.company;
        sortSelect.value = state.sort;
        typeButtons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.type === state.type)));

        const filtered = state.q || state.company || state.tags.length || state.type;
        countEl.textContent = filtered
            ? `Showing ${visible.length} of ${entries.length} reviews`
            : `${entries.length} reviews`;
        clearBtn.hidden = !filtered;
        emptyEl.hidden = visible.length > 0;
        writeUrl();
    }

    function toggleTag(tag) {
        state.tags = state.tags.includes(tag) ? state.tags.filter((t) => t !== tag) : state.tags.concat(tag);
        render();
    }

    function clearFilters() {
        Object.assign(state, { q: '', company: '', tags: [], type: '' });
        render();
    }

    // ---- Events ------------------------------------------------------------

    let searchTimer;
    searchInput.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => { state.q = searchInput.value.trim(); render(); }, 120);
    });
    companySelect.addEventListener('change', () => { state.company = companySelect.value; render(); });
    sortSelect.addEventListener('change', () => { state.sort = sortSelect.value; render(); });
    typeButtons.forEach((b) => b.addEventListener('click', () => { state.type = b.dataset.type; render(); }));
    clearBtn.addEventListener('click', clearFilters);
    $('reading-empty-clear').addEventListener('click', clearFilters);

    list.addEventListener('click', (ev) => {
        const tagBtn = ev.target.closest('.reading-tag');
        if (tagBtn) { toggleTag(tagBtn.dataset.tag); return; }
        const coBtn = ev.target.closest('.reading-company');
        if (coBtn) {
            state.company = state.company === coBtn.dataset.company ? '' : coBtn.dataset.company;
            render();
        }
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

    function countBy(items) {
        return items.reduce((acc, x) => { acc[x] = (acc[x] || 0) + 1; return acc; }, {});
    }
});
