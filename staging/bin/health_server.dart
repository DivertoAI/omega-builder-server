import 'dart:io';

Future<void> main(List<String> args) async {
  final envPort = Platform.environment['PORT'];
  final port = int.tryParse(envPort ?? '') ?? 8080;
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, port);
  print('Health server listening on http://${server.address.host}:$port');

  await for (final request in server) {
    if (request.method == 'GET' && request.uri.path == '/health') {
      request.response
        ..statusCode = HttpStatus.ok
        ..headers.contentType = ContentType.text
        ..write('ok');
    } else {
      request.response
        ..statusCode = HttpStatus.notFound
        ..headers.contentType = ContentType.text
        ..write('not found');
    }
    await request.response.close();
  }
}
