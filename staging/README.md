# Hello Button App

A minimal single-screen Flutter app that displays "Hello" text and a button.

Run (Web):
- Ensure Flutter SDK is installed and Chrome is available.
- From the project root:
  1. flutter pub get
  2. flutter run -d chrome

Health endpoint (acceptance):
- When running on web, the dev server serves a static health endpoint.
- Copy the local URL printed by Flutter (e.g., http://localhost:12345), then open or curl:
  - curl http://localhost:12345/health
- Expected response: "ok"

Notes:
- The app defines a single route named "/home" matching the spec's navigation.home.
