(function () {
  const appEl = document.getElementById('app');
  const yearEl = document.getElementById('year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  const routes = {
    dashboard: renderDashboard,
    orders: () => section('Orders', 'Review and manage medication orders.'),
    prescriptions: () => section('Prescriptions', 'Create and track e-prescriptions.'),
    pharmacies: () => section('Pharmacies', 'Browse and manage partner pharmacies.'),
    profile: () => section('Profile', 'Manage account and preferences.')
  };

  function section(title, subtitle) {
    return `
      <section class="card">
        <h1>${title}</h1>
        <p class="muted">${subtitle}</p>
      </section>
    `;
  }

  async function renderDashboard() {
    const health = await fetchHealth();
    return `
      <section class="grid">
        <div class="card">
          <h1>Dashboard</h1>
          <p class="muted">Overview of InstaPharma.</p>
        </div>
        <div class="card">
          <h2>Service Health</h2>
          <p>Status: <strong class="${health.ok ? 'ok' : 'bad'}">${health.text}</strong></p>
        </div>
      </section>
    `;
  }

  async function fetchHealth() {
    try {
      const res = await fetch('/health', { cache: 'no-store' });
      if (!res.ok) return { ok: false, text: 'unreachable' };
      const data = await res.json();
      return { ok: data && data.status === 'ok', text: data.status || 'unknown' };
    } catch (_) {
      return { ok: false, text: 'error' };
    }
  }

  function setActiveLink(route) {
    document.querySelectorAll('.nav-link').forEach(a => {
      a.classList.toggle('active', a.getAttribute('data-route') === route);
    });
  }

  async function renderRoute() {
    const route = (location.hash.replace('#/', '') || 'dashboard').toLowerCase();
    const view = routes[route] || routes.dashboard;
    setActiveLink(route);
    const html = await view();
    appEl.innerHTML = html;
    appEl.focus();
  }

  window.addEventListener('hashchange', renderRoute);
  window.addEventListener('DOMContentLoaded', () => {
    if (!location.hash) location.hash = '#/dashboard';
    renderRoute();
  });
})();
