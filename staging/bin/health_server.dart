import 'dart:io';

Future<void> main(List<String> args) async {
  final portEnv = Platform.environment['PORT'];
  final port = int.tryParse(portEnv ?? '') ?? 8080;
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, port);
  stdout.writeln('Health server listening on http://${server.address.address}:$port');

  await for (final req in server) {
    if (req.method == 'GET' && req.uri.path == '/health') {
      req.response.statusCode = HttpStatus.ok;
      req.response.headers.contentType = ContentType.text;
      req.response.write('ok');
      await req.response.close();
    } else {
      req.response.statusCode = HttpStatus.notFound;
      await req.response.close();
    }
  }
}
