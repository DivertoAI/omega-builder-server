import 'dart:io';
import 'package:shelf/shelf.dart';
import 'package:shelf/shelf_io.dart' as io;

String _contentTypeFor(String path) {
  final ext = path.split('.').last.toLowerCase();
  switch (ext) {
    case 'html':
      return 'text/html; charset=utf-8';
    case 'css':
      return 'text/css; charset=utf-8';
    case 'js':
      return 'application/javascript; charset=utf-8';
    case 'json':
      return 'application/json; charset=utf-8';
    case 'png':
      return 'image/png';
    case 'jpg':
    case 'jpeg':
      return 'image/jpeg';
    case 'svg':
      return 'image/svg+xml';
    default:
      return 'application/octet-stream';
  }
}

Future<Response> _staticHandler(Request req) async {
  final relativePath = req.url.path.isEmpty ? 'index.html' : req.url.path;
  for (final root in ['build/web', 'web']) {
    final file = File('$root/$relativePath');
    if (await file.exists()) {
      final headers = {
        'content-type': _contentTypeFor(file.path),
        'cache-control': 'no-cache',
      };
      return Response.ok(file.openRead(), headers: headers);
    }
  }
  return Response.notFound('Not found');
}

void main(List<String> args) async {
  final handler = const Pipeline()
      .addMiddleware(logRequests())
      .addHandler((Request req) async {
    if (req.url.path == 'health') {
      return Response.ok('ok', headers: {'content-type': 'text/plain; charset=utf-8'});
    }
    return _staticHandler(req);
  });

  final port = int.tryParse(Platform.environment['PORT'] ?? '') ?? 8080;
  final server = await io.serve(handler, InternetAddress.anyIPv4, port);
  stdout.writeln('Server running on http://${server.address.address}:${server.port}');
  stdout.writeln('Health: http://localhost:${server.port}/health');
}
