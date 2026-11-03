/* Use the device's IANA zone, including DST, as in the upstream agenda. */
(() => {
    const form = document.getElementById('agenda-filters');
    if (!form) return;
    document.getElementById('agenda-meeting')?.addEventListener('change', () => form.requestSubmit());
    const selector = document.getElementById('agenda-zone');
    const hidden = document.getElementById('agenda-local-zone');
    form.addEventListener('submit', event => {
        // The clicked timezone button supplies tz; avoid submitting it twice.
        selector.name = 'tz';
        selector.disabled = event.submitter?.name === 'tz';
    });
    document.querySelectorAll('.fgtida-agenda-tools').forEach(item => { item.hidden = false; });
    const filters = document.getElementById('agenda-filter-panel');
    const showFilters = () => { filters.open = true; document.getElementById('agenda-search').focus(); };
    document.getElementById('agenda-find')?.addEventListener('click', showFilters);
    document.getElementById('agenda-filter-toggle')?.addEventListener('click', showFilters);
    const rows = [...document.querySelectorAll('tr[data-session]')];
    const pickKey = `fgtida-agenda-picks:${document.getElementById('agenda-meeting').value}`;
    let picked = new Set();
    try { const saved = JSON.parse(localStorage.getItem(pickKey) || '[]'); if (Array.isArray(saved)) picked = new Set(saved); } catch { /* Storage may be disabled. */ }
    let pickedOnly = false;
    const updatePicks = () => {
        rows.forEach(row => { row.hidden = pickedOnly && !picked.has(row.dataset.session); });
        document.getElementById('agenda-pick-empty').hidden = !pickedOnly || rows.some(row => !row.hidden);
    };
    rows.forEach(row => {
        const label = document.createElement('label');
        label.className = 'fgtida-session-pick';
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.className = 'form-check-input';
        input.checked = picked.has(row.dataset.session);
        input.setAttribute('aria-label', `Pick ${row.querySelector('.fgtida-agenda-event > summary').textContent.trim()}`);
        input.addEventListener('change', () => {
            if (input.checked) picked.add(row.dataset.session); else picked.delete(row.dataset.session);
            rows.filter(other => other.dataset.session === row.dataset.session).forEach(other => { other.querySelector('.fgtida-session-pick input').checked = input.checked; });
            try { localStorage.setItem(pickKey, JSON.stringify([...picked])); } catch { /* Keep picks for this page. */ }
            updatePicks();
        });
        label.append(input);
        label.hidden = true;
        row.lastElementChild.prepend(label);
    });
    document.getElementById('agenda-pick-toggle')?.addEventListener('click', event => {
        const active = event.currentTarget.getAttribute('aria-pressed') !== 'true';
        event.currentTarget.setAttribute('aria-pressed', String(active));
        document.querySelectorAll('.fgtida-session-pick').forEach(label => { label.hidden = !active; });
        document.getElementById('agenda-pick-options').hidden = !active;
        if (!active) { pickedOnly = false; document.getElementById('agenda-picked-only').checked = false; updatePicks(); }
    });
    document.getElementById('agenda-picked-only')?.addEventListener('change', event => { pickedOnly = event.target.checked; updatePicks(); });
    document.getElementById('agenda-colors')?.addEventListener('click', event => {
        const active = event.currentTarget.getAttribute('aria-pressed') !== 'true';
        event.currentTarget.setAttribute('aria-pressed', String(active));
        rows.forEach(row => {
            let hash = 0;
            for (const char of row.dataset.group) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
            row.style.borderLeft = active ? `5px solid hsl(${hash % 360} 65% 45%)` : '';
        });
    });
    let zone;
    try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { return; }
    if (!zone) return;
    const previous = hidden.value;
    hidden.value = zone;
    // Reload once so the server can group sessions by their local calendar day.
    if (selector.value === 'local' && previous !== zone) {
        const url = new URL(window.location.href);
        url.searchParams.set('tz', 'local');
        url.searchParams.set('local_zone', zone);
        window.location.replace(url);
    }
})();
