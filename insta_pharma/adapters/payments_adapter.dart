// adapters/payments_adapter.dart
class PaymentsAdapter {
  static const _provider = String.fromEnvironment('PAYMENT_PROVIDER', defaultValue: 'mock');
  static bool get enabled => _provider != 'disabled';

  Future<String> charge(int cents, {required String currency}) async {
    if (!enabled) return 'disabled';
    // TODO: wire real provider
    return 'ok:$_provider:$cents$currency';
  }
}
