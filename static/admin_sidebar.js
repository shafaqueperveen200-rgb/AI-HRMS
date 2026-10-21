function toggleSidebar() {
    const sidebar = document.querySelector('.dashboard-layout > .sidebar');
    const closeButton = document.querySelector('.sidebar-toggle-btn');
    const openButton = document.querySelector('.sidebar-reopen-btn');

    if (!sidebar || !closeButton || !openButton) {
        return;
    }

    if (window.matchMedia('(max-width: 767.98px)').matches) {
        const isOpen = sidebar.classList.toggle('mobile-open');
        updateSidebarControls(sidebar, !isOpen);
        return;
    }

    const isCollapsed = sidebar.classList.toggle('collapsed');
    updateSidebarControls(sidebar, isCollapsed);
}

function updateSidebarControls(sidebar, isCollapsed) {
    const closeButton = sidebar.querySelector('.sidebar-toggle-btn');
    const openButton = document.querySelector('.dashboard-layout > .sidebar-reopen-btn');
    const backdrop = document.querySelector('.sidebar-backdrop');

    if (closeButton) {
        closeButton.setAttribute('aria-expanded', String(!isCollapsed));
        closeButton.setAttribute('aria-label', isCollapsed ? 'Open sidebar' : 'Close sidebar');
        closeButton.setAttribute('title', isCollapsed ? 'Open sidebar' : 'Close sidebar');
    }
    if (openButton) {
        openButton.setAttribute('aria-expanded', String(!isCollapsed));
    }
    if (backdrop) {
        backdrop.classList.toggle(
            'is-visible',
            window.matchMedia('(max-width: 767.98px)').matches && !isCollapsed
        );
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const layout = document.querySelector('.dashboard-layout');
    const sidebar = layout.querySelector('.sidebar');
    if (!layout || !sidebar) {
        return;
    }

    const backdrop = document.createElement('button');
    backdrop.type = 'button';
    backdrop.className = 'sidebar-backdrop';
    backdrop.setAttribute('aria-label', 'Close navigation menu');
    layout.append(backdrop);

    const setMobileState = () => {
        sidebar.classList.remove('mobile-open');
        sidebar.classList.remove('collapsed');
        updateSidebarControls(sidebar, true);
    };

    backdrop.addEventListener('click', () => {
        sidebar.classList.remove('mobile-open');
        updateSidebarControls(sidebar, true);
    });

    sidebar.querySelectorAll('.sidebar-menu a, .logout-link').forEach((link) => {
        link.addEventListener('click', () => {
            if (window.matchMedia('(max-width: 767.98px)').matches) {
                sidebar.classList.remove('mobile-open');
                updateSidebarControls(sidebar, true);
            }
        });
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && window.matchMedia('(max-width: 767.98px)').matches) {
            sidebar.classList.remove('mobile-open');
            updateSidebarControls(sidebar, true);
        }
    });

    setMobileState();
    window.addEventListener('resize', setMobileState);
    const mobileQuery = window.matchMedia('(max-width: 767.98px)');
    if (mobileQuery.addEventListener) {
        mobileQuery.addEventListener('change', setMobileState);
    } else {
        mobileQuery.addListener(setMobileState);
    }
});
