(function () {
  const appEl = document.getElementById('app');
  const navEl = document.getElementById('nav');

  const view = (html) => { appEl.innerHTML = html; setActiveNav(); };

  function setActiveNav() {
    const hash = location.hash.replace('#', '') || 'catalog';
    [...navEl.querySelectorAll('a')].forEach(a => {
      const isActive = a.getAttribute('href') === '#' + hash;
      a.classList.toggle('active', isActive);
      a.setAttribute('aria-current', isActive ? 'page' : 'false');
    });
  }

  const routes = {
    catalog: () => `
      <section class="hero">
        <h2 class="section-title">Catalog</h2>
        <p class="small">Browse medicines and health products.</p>
      </section>
      <section style="margin-top:12px;" class="grid">
        ${[
          {name:'Paracetamol 500mg', price:'$4.99'},
          {name:'Ibuprofen 200mg', price:'$5.49'},
          {name:'Vitamin C 1000mg', price:'$9.99'}
        ].map(p => `
          <article class="card product">
            <h3>${p.name}</h3>
            <div class="small">${p.price}</div>
            <button class="button" onclick="alert('Added to cart: ${p.name}')">Add to cart</button>
          </article>
        `).join('')}
      </section>
    `,
    cart: () => `
      <section class="card">
        <h2 class="section-title">Cart</h2>
        <p>Your cart is empty. Add items from the catalog.</p>
      </section>
    `,
    checkout: () => `
      <section class="card">
        <h2 class="section-title">Checkout</h2>
        <label>Name<br><input class="input" placeholder="Full name"></label><br><br>
        <label>Address<br><input class="input" placeholder="Delivery address"></label><br><br>
        <button class="button">Place order</button>
      </section>
    `,
    orders: () => `
      <section class="card">
        <h2 class="section-title">Orders</h2>
        <p class="small">You have no recent orders.</p>
      </section>
    `,
    uploadRx: () => `
      <section class="card">
        <h2 class="section-title">Upload Prescription</h2>
        <input type="file" accept="image/*,.pdf">
        <p class="small">Accepted formats: JPG, PNG, PDF.</p>
        <button class="button" onclick="alert('Uploaded')">Upload</button>
      </section>
    `,
  };

  function router() {
    const hash = location.hash.replace('#', '') || 'catalog';
    const page = routes[hash] ? hash : 'catalog';
    view(routes[page]());
  }

  window.addEventListener('hashchange', router);
  window.addEventListener('DOMContentLoaded', () => {
    if (!location.hash) location.hash = '#catalog';
    router();
  });
})();
