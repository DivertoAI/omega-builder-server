import 'package:test/test.dart';
import 'package:auth/auth.dart';

void main() {
  test('auth says hello', () {
    expect(authHello(), 'Hello from auth');
  });
}
