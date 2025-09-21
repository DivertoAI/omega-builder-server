InstaPharma

A minimal scaffold for an e-prescription and medication ordering platform. Ships a simple SPA shell (Dashboard, Orders, Prescriptions, Pharmacies, Profile) and a health endpoint.

Features
- SPA shell with navigation; home: Dashboard
- Node.js/Express server
- Health endpoint: GET /health -> { "status": "ok" }

Quick start
1) Install dependencies
   npm install

2) Start the service
   npm start

3) Open the app
   http://localhost:3000

Health check
- Endpoint: GET /health
- Expected: { "status": "ok" }

Navigation routes
- #/dashboard (home)
- #/orders
- #/prescriptions
- #/pharmacies
- #/profile

Project structure
- server.js: Express server and /health
- public/: Static SPA assets (index.html, app.js, styles.css)
