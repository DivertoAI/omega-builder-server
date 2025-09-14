import 'dart:convert';
import 'dart:io';

HttpServer? _server;

Future<void> startHealthServer({int port = 8080}) async {
  if (_server != null) return;
  try {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, port);
    _server!.listen((HttpRequest req) async {
      if (req.method == 'GET' && req.uri.path == '/health') {
        final body = jsonEncode({'status': 'ok'});
        req.response.statusCode = HttpStatus.ok;
        req.response.headers.contentType = ContentType.json;
        req.response.write(body);
      } else {
        req.response.statusCode = HttpStatus.notFound;
      }
      await req.response.close();
    });
  } catch (_) {
    // Port may already be bound; ignore in that case.
  }
}
