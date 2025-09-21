import 'dart:io';
import 'package:shelf/shelf.dart';
import 'package:shelf/shelf_io.dart';

void main(List<String> args) async {
  final handler = Pipeline()
      .addMiddleware(logRequests())
      .addHandler((Request request) {
    if (request.url.path == 'health') {
      return Response.ok('ok', headers: {'content-type': 'text/plain'});
    }
    return Response.notFound('not found');
  });

  final port = int.tryParse(Platform.environment['PORT'] ?? '') ?? 8080;
  final server = await serve(handler, InternetAddress.anyIPv4, port);
  print('Health server listening on http://localhost:${server.port}');
}
