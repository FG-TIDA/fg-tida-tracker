(() => {
    const header = document.getElementById('fg-tida-navbar');
    if (!header) return;
    const update = () => document.documentElement.style.setProperty(
        '--fg-tida-header-height', `${Math.ceil(header.getBoundingClientRect().height)}px`);
    update();
    // Account for mobile menus, font loading, zoom, and changes in header height.
    if ('ResizeObserver' in window) new ResizeObserver(update).observe(header);
    window.addEventListener('resize', update);
})();
