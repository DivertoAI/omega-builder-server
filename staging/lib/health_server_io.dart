import 'dart:io';

Future<void> startHealthServer() async {
  HttpServer server;
  try {
    server = await HttpServer.bind(InternetAddress.loopbackIPv4, 8080);
  } catch (_) {
    try {
      server = await HttpServer.bind(InternetAddress.anyIPv4, 8080);
    } catch (_) {
      server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    }
  }

  print('Health server listening on http://${server.address.address}:${server.port}/health');

  server.listen((HttpRequest request) {
    if (request.method == 'GET' && request.uri.path == '/health') {
      request.response.statusCode = HttpStatus.ok;
      request.response.headers.contentType = ContentType('text', 'plain', charset: 'utf-8');
      request.response.write('ok');
    } else {
      request.response.statusCode = HttpStatus.notFound;
      request.response.headers.contentType = ContentType('text', 'plain', charset: 'utf-8');
      request.response.write('not found');
    }
    request.response.close();
  });
}
