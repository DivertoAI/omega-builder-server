(function () {
  const appEl = document.getElementById('app');
  const navEl = document.getElementById('nav');

  const view = (html) => { appEl.innerHTML = html; setActiveNav(); };

  function setActiveNav() {
    const hash = location.hash.replace('#', '') || 'pharmacistDashboard';
    [...navEl.querySelectorAll('a')].forEach(a => {
      const isActive = a.getAttribute('href') === '#' + hash;
      a.classList.toggle('active', isActive);
      a.setAttribute('aria-current', isActive ? 'page' : 'false');
    });
  }

  const routes = {
    pharmacistDashboard: () => `
      <section class="kpi">
        <div class="card"><h3>Open Orders</h3><div class="value">3</div></div>
        <div class="card"><h3>Awaiting Rx</h3><div class="value">1</div></div>
        <div class="card"><h3>Low Stock</h3><div class="value">5</div></div>
      </section>
    `,
    inventory: () => `
      <section class="card">
        <h2 class="section-title">Inventory</h2>
        <table class="table">
          <thead><tr><th>Item</th><th>Stock</th><th></th></tr></thead>
          <tbody>
            <tr><td>Paracetamol 500mg</td><td>42</td><td><button class="button secondary">Restock</button></td></tr>
            <tr><td>Ibuprofen 200mg</td><td>12</td><td><button class="button secondary">Restock</button></td></tr>
            <tr><td>Vitamin C 1000mg</td><td>8</td><td><button class="button secondary">Restock</button></td></tr>
          </tbody>
        </table>
      </section>
    `,
    fulfillment: () => `
      <section class="card">
        <h2 class="section-title">Fulfillment</h2>
        <p class="small">Pack and ship pending orders.</p>
        <div class="grid">
          <article class="card">
            <strong>Order #1001</strong>
            <div class="small">2 items</div>
            <button class="button">Mark as Shipped</button>
          </article>
          <article class="card">
            <strong>Order #1002</strong>
            <div class="small">1 item</div>
            <button class="button">Mark as Shipped</button>
          </article>
        </div>
      </section>
    `,
  };

  function router() {
    const hash = location.hash.replace('#', '') || 'pharmacistDashboard';
    const page = routes[hash] ? hash : 'pharmacistDashboard';
    view(routes[page]());
  }

  window.addEventListener('hashchange', router);
  window.addEventListener('DOMContentLoaded', () => {
    if (!location.hash) location.hash = '#pharmacistDashboard';
    router();
  });
})();
