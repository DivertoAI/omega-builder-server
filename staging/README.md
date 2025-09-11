Omega App

A minimal Flutter app to browse items with search and a detail page. It also exposes a simple health endpoint.

Run
- Ensure Flutter SDK is installed.
- In the project directory, create platform folders if needed: flutter create .
- Run the app: flutter run

Health endpoint
- When the app starts (on mobile/desktop), it launches a tiny HTTP server.
- Endpoint: GET http://localhost:8080/health
- Response: ok (200)
- If port 8080 is unavailable, it falls back to another port; the chosen address is printed to the debug console.
- Note: On Flutter Web, the health server is disabled.

Features
- Home screen with a search box filtering a simple list of items.
- Tap an item to view a basic detail page.
