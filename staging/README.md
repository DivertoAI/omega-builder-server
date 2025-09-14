FlutterListDetailApp

A minimal Flutter app that shows a list on the home screen and a detail screen for each item. Includes a simple health endpoint service returning "ok".

Requirements
- Flutter SDK (3.x+) and Dart SDK (3.x+)

Getting Started (App)
1) Initialize platform folders (once):
   flutter create --project-name flutter_list_detail_app .
2) Install dependencies:
   flutter pub get
3) Run the app:
   flutter run

Routes
- /                Home list
- /items/:id       Detail for item with the given id (e.g., /items/3)

Health Endpoint Service
A minimal Dart HTTP server exposing GET /health -> 200 ok.

Run:
  dart bin/health_server.dart

Test:
  curl -i http://localhost:8080/health
  HTTP/1.1 200 OK
  ok

Notes
- The health server binds to localhost:8080 by default. Set PORT env var to override.
- The server is independent of the Flutter app and can be used for basic liveness checks.