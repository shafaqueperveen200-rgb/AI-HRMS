document.addEventListener('DOMContentLoaded', () => {
    const menuControls = [
        {
            button: document.getElementById('employeeNotificationsButton'),
            panel: document.getElementById('employeeNotificationsPanel')
        },
        {
            button: document.getElementById('employeeAccountButton'),
            panel: document.getElementById('employeeAccountPanel')
        }
    ].filter(({ button, panel }) => button && panel);

    function closeMenus(exceptPanel) {
        menuControls.forEach(({ button, panel }) => {
            if (panel !== exceptPanel) {
                panel.hidden = true;
                button.setAttribute('aria-expanded', 'false');
            }
        });
    }

    menuControls.forEach(({ button, panel }) => {
        button.addEventListener('click', () => {
            const shouldOpen = panel.hidden;
            closeMenus(panel);
            panel.hidden = !shouldOpen;
            button.setAttribute('aria-expanded', String(shouldOpen));
        });
    });

    document.addEventListener('click', (event) => {
        if (!event.target.closest('.employee-header-menu')) {
            closeMenus();
        }
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            const openMenu = menuControls.find(({ button }) => (
                button.getAttribute('aria-expanded') === 'true'
            ));
            closeMenus();
            if (openMenu) {
                openMenu.button.focus();
            }
        }
    });
});
