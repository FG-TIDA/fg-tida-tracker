(() => {
    const dialog = document.getElementById('session-materials-dialog');
    const content = document.getElementById('session-materials-content');
    let controller;
    let trigger;
    dialog?.addEventListener('close', () => {
        controller?.abort();
        if (trigger?.isConnected) trigger.focus();
    });
    document.getElementById('session-materials-close')?.addEventListener('click', () => dialog.close());
    document.addEventListener('click', async event => {
        const tab = event.target.closest('.session-material-tab');
        if (tab && (dialog?.contains(tab) || document.querySelector('.fgtida-session-materials')?.contains(tab))) {
            event.preventDefault();
            const root = tab.closest('.fgtida-session-materials') || content;
            root.querySelectorAll('[data-material-panel]').forEach(panel => { panel.hidden = panel.dataset.materialPanel !== tab.dataset.materialTab; });
            root.querySelectorAll('[data-material-tab]').forEach(button => {
                const active = button === tab;
                button.classList.toggle('active', active);
                if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
            });
            return;
        }
        const link = event.target.closest('.session-materials-open');
        if (!link || !dialog?.showModal || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        const url = new URL(link.href, location.href);
        if (url.origin !== location.origin) return;
        event.preventDefault();
        controller?.abort();
        controller = new AbortController();
        const requestController = controller;
        trigger = link;
        content.textContent = 'Loading session materials…';
        dialog.showModal();
        url.searchParams.set('partial', '1');
        try {
            const response = await fetch(url, {signal: requestController.signal, credentials: 'same-origin'});
            if (!response.ok) throw new Error('Session unavailable');
            const html = await response.text();
            if (requestController !== controller || requestController.signal.aborted) return;
            // Same-origin, server-rendered fragment: all user-authored prose is
            // escaped or sanitized by the existing reading-preview renderer.
            content.innerHTML = html;
        } catch (error) {
            if (error.name === 'AbortError') return;
            dialog.close();
            location.assign(link.href);
        }
    });
})();
