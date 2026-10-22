(() => {
    const links = [...document.querySelectorAll('.settings-nav a[href^="#"]')];
    if (!links.length) return;

    function activate(id) {
        for (const link of links) {
            const selected = link.hash === `#${id}`;
            link.classList.toggle('active', selected);
            if (selected) link.setAttribute('aria-current', 'location');
            else link.removeAttribute('aria-current');
        }
    }

    for (const link of links) link.addEventListener('click', () => activate(link.hash.slice(1)));
    window.addEventListener('hashchange', () => activate(location.hash.slice(1) || 'profile'));
    activate(location.hash.slice(1) || 'profile');
})();
