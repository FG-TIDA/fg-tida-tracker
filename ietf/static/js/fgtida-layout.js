(() => {
    // Derive mobile card labels from the real table headings, keeping one set
    // of content and links for desktop, mobile and assistive technology.
    document.querySelectorAll('#content table.table').forEach(table => {
        if (table.matches('.fg-mobile-cards, .fg-document-metadata, .fgtida-agenda-table')) return;
        if (table.tHead?.rows.length !== 1) return;
        const headings = [...table.tHead.rows[0].cells];
        if (headings.some(cell => cell.colSpan !== 1 || cell.rowSpan !== 1)) return;
        table.classList.add('fg-mobile-cards');
        table.setAttribute('role', 'table');
        table.tHead.setAttribute('role', 'rowgroup');
        table.tHead.rows[0].setAttribute('role', 'row');
        headings.forEach(cell => { cell.setAttribute('scope', 'col'); cell.setAttribute('role', 'columnheader'); });
        [...table.tBodies].forEach(body => {
            body.setAttribute('role', 'rowgroup');
            [...body.rows].forEach(row => {
                row.setAttribute('role', 'row');
                if (row.cells.length === 1 && row.cells[0].colSpan > 1) row.classList.add('fg-empty');
                [...row.cells].forEach((cell, index) => {
                    cell.setAttribute('role', 'cell');
                    cell.dataset.label = headings[index]?.textContent.trim() || '';
                });
            });
        });
    });
    const header = document.getElementById('fg-tida-navbar');
    if (!header) return;
    const update = () => document.documentElement.style.setProperty(
        '--fg-tida-header-height', `${Math.ceil(header.getBoundingClientRect().height)}px`);
    update();
    // Account for mobile menus, font loading, zoom, and changes in header height.
    if ('ResizeObserver' in window) new ResizeObserver(update).observe(header);
    window.addEventListener('resize', update);
})();
